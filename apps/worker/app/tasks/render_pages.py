"""
A document's page images (viewer A3; D-228, D-232). The parse service draws
them (`POST /v1/pages`, A2) in a sandboxed job of its own; this task asks
from page 0, then again from each answer's `next_page`, and stores every
drawn page under its fixed key, `derived/{doc}/pages/0001`, ... Nothing here
opens an image: each page is base64 text checked by the client, decoded,
uploaded and released before the next.

* **Its own queue and process** (PAGES_QUEUE): a long render never takes a
  document's slot, and with the one document slot it leaves one of the parse
  service's two (PARSE_SLOTS) for the document.
* **Written once, when complete.** `page_count` and `pages` are set together
  in one update, only after every page is stored, and only while
  `page_count` is still NULL: the first complete run wins, and a run that
  finds the list already written stops without deleting anything. After the
  write, page keys the new list doesn't name are deleted.
* **Before every write** the document must still exist, not be deleted, and
  its tenant must not be `pending_deletion` or `deleted`; otherwise the task
  stops (`pages_skipped`).
* **Best effort, no alert for a render failure** (founder, 2026-11-03). The
  two security events keep theirs (founder, 2026-11-04): a refused stored
  path, and a seccomp kill (its own alert, `where="pages"`).
  Never-got-in
  (ParseUnavailable) and a Storage outage are retried after
  PAGES_RETRY_COUNTDOWNS_SECONDS. Giving up after the last retry, any error
  that isn't retried, and the deadline below all end the same way: the
  document's page keys are deleted best effort, both columns stay NULL (the
  viewer falls back; A6's backfill finds it), and one `pages_failed` line is
  logged with the error's type only.
* **No soft time limit** (constants.py: none is set anywhere). The task
  stops itself at PAGES_DEADLINE_SECONDS, checked before each answer and
  each page's upload; once every page is stored, the list is written even
  past it. The hard limit stays as the backstop.

`tenant_id` comes from the enqueuing document task, which had it from the
session (Section 7.5); every query runs in `tenant_session(tenant_id)`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from uuid import UUID

from celery.exceptions import Reject, Retry
from docflow_core import founder_alerts, parse_client
from docflow_core.constants import (
    PAGES_QUEUE,
    PAGES_RETRY_COUNTDOWNS_SECONDS,
    PAGES_TASK_TIME_LIMIT_SECONDS,
    PAGES_UPLOAD_ALLOWANCE_SECONDS,
)
from docflow_core.db import tenant_session
from docflow_core.storage import (
    StorageUnavailableError,
    UnsafeStoragePathError,
    delete_pages,
    list_pages,
    page_path,
    read_file,
    save_page,
)
from sqlalchemy import text

from app.celery_app import celery_app

logger = logging.getLogger(__name__)

# The hard limit minus the most one answer can take: the parse client's
# patience for getting in, the upload allowance, then its read timeout.
# 900 - (45 + 60 + 210) = 585 s. A check before each page's upload means a
# Storage call (about two minutes at worst, storage.S3Backend) never spans
# more than one check either.
PAGES_DEADLINE_SECONDS = PAGES_TASK_TIME_LIMIT_SECONDS - (
    parse_client.PARSE_CONNECT_PATIENCE_SECONDS
    + PAGES_UPLOAD_ALLOWANCE_SECONDS
    + parse_client.READ_TIMEOUT_SECONDS
)

# What a retry can fix: the service never let us in, or Storage was out.
_RETRIED = (parse_client.ParseUnavailable, StorageUnavailableError)

_OPEN_STATUSES_SQL = "t.status NOT IN ('pending_deletion', 'deleted')"


class Deadline(Exception):
    """PAGES_DEADLINE_SECONDS passed: stop before the hard limit does."""


class NotWanted(Exception):
    """The document is gone, deleted, its tenant is being deleted, or its
    list is already written: stop, write nothing, delete nothing."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Refused(Exception):
    """An answer or a check that a retry won't change: the service refused or
    stopped on the file, or the stored bytes aren't the file received."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class Drawn:
    page_count: int
    pages: list[dict]


def _check_wanted(tid: UUID, did: UUID) -> None:
    """Before every write: raises NotWanted unless the document still exists,
    isn't deleted, its tenant isn't being deleted, and no list is written."""
    with tenant_session(tid) as session:
        row = session.execute(
            text(
                "SELECT d.page_count FROM documents d JOIN tenants t ON t.id = d.tenant_id "
                f"WHERE d.id = :id AND d.deleted_at IS NULL AND {_OPEN_STATUSES_SQL}"
            ),
            {"id": str(did)},
        ).mappings().first()
    if row is None:
        raise NotWanted("gone")
    if row["page_count"] is not None:
        raise NotWanted("already_drawn")


def _past(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise Deadline()


def draw(tid: UUID, did: UUID, *, deadline: float) -> Drawn:
    """Ask for every page and store each drawn one. Returns the list to write;
    raises Deadline, NotWanted, Refused, or what the client and Storage raise."""
    with tenant_session(tid) as session:
        row = session.execute(
            text("SELECT storage_path, original_filename, content_sha256 FROM documents WHERE id = :id"),
            {"id": str(did)},
        ).mappings().first()
    if row is None:
        raise NotWanted("gone")
    _check_wanted(tid, did)
    try:
        content = read_file(tid, row["storage_path"])
    except UnsafeStoragePathError as exc:
        # The prefix rules refused the stored path (Section 7.5); nothing was
        # read. Recorded as every reader of a stored path records it.
        founder_alerts.report_refused_storage_path(exc, tenant_id=tid, where="pages", ref_id=did)
        raise Refused("path_refused") from exc
    if row["content_sha256"] and hashlib.sha256(content).hexdigest() != row["content_sha256"]:
        raise Refused("hash_mismatch")
    entries: list[dict] = []
    page_count: int | None = None
    first: int | None = 0
    while first is not None:
        _past(deadline)
        answer = parse_client.render_pages(content, row["original_filename"], first)
        if answer.outcome != "ok":
            if f"{answer.outcome}:{answer.cause}" == founder_alerts.SECCOMP_KILL_CAUSE:
                # A security event, not an ordinary render failure: the alert
                # names the render (its own dedupe key), and carries no
                # catalog code, since no document failed (founder, 2026-11-04).
                founder_alerts.alert_seccomp_kill(tid, where="pages", ref_id=did, error_code=None)
            raise Refused(f"{answer.outcome}:{answer.code or answer.cause or 'unknown'}")
        if page_count is not None and answer.page_count != page_count:
            raise parse_client.ParseAnswerInvalid("page_count_changed")
        page_count = answer.page_count
        for page in answer.pages:
            if "reason" in page:
                entries.append({"index": page["index"], "rendered": False, "reason": page["reason"]})
                continue
            _past(deadline)
            _check_wanted(tid, did)
            # Decoded, uploaded and released one page at a time: the task
            # never holds every decoded page at once (founder, 2026-11-03).
            image = parse_client.page_bytes(page)
            page["data"] = ""
            save_page(tid, did, page["index"], image, content_type=page["media_type"])
            del image
            entries.append({key: page[key] for key in ("index", "media_type", "width", "height")})
        first = answer.next_page
        del answer
    if page_count is None:  # never: the loop asks at least once
        raise parse_client.ParseAnswerInvalid("no_answer")
    return Drawn(page_count=page_count, pages=entries)


def write_list(tid: UUID, did: UUID, drawn: Drawn) -> bool:
    """The one update: both columns together, only while page_count is NULL
    and the document and its tenant are still wanted. False if it wrote
    nothing (another run won, or the document stopped being wanted)."""
    with tenant_session(tid) as session:
        result = session.execute(
            text(
                f"""
                UPDATE documents SET page_count = :n, pages = CAST(:pages AS jsonb)
                 WHERE id = :id AND deleted_at IS NULL AND page_count IS NULL
                   AND EXISTS (
                       SELECT 1 FROM tenants t WHERE t.id = documents.tenant_id AND {_OPEN_STATUSES_SQL}
                   )
                """
            ),
            {"id": str(did), "n": drawn.page_count, "pages": json.dumps(drawn.pages)},
        )
        return bool(result.rowcount)


def delete_stale(tid: UUID, did: UUID, drawn: Drawn) -> None:
    """After the write: delete every page key the new list doesn't name (a
    redraw that drew fewer pages, or a page not drawn this time). Best effort."""
    keep = {page_path(tid, did, entry["index"]) for entry in drawn.pages if "media_type" in entry}
    try:
        stale = [key for key in list_pages(tid, did) if key not in keep]
        delete_pages(tid, did, stale)
    except Exception as exc:  # noqa: BLE001 -- the list is written; a leftover key is only storage
        logger.error("pages_stale_delete_failed document_id=%s error_type=%s", did, type(exc).__name__)


def give_up(tid: UUID, did: UUID, error_type: str) -> None:
    """Every failed end: one log line with the error's type, then the page
    keys deleted best effort -- only while page_count is still NULL, so a
    list another run wrote never loses its pages. Both columns stay NULL.

    There is no per-document lock (D-232): a run of the same document that
    overlaps this one, with its pages stored and its list not yet written,
    would lose its pages here. Safe only while one pages process runs at
    concurrency 1 (app.run_workers; RUNBOOK 9.2)."""
    logger.error("pages_failed document_id=%s error_type=%s", did, error_type)
    try:
        with tenant_session(tid) as session:
            row = session.execute(
                text("SELECT page_count FROM documents WHERE id = :id"), {"id": str(did)}
            ).mappings().first()
        if row is not None and row["page_count"] is not None:
            return
        delete_pages(tid, did, list_pages(tid, did))
    except Exception as exc:  # noqa: BLE001 -- best effort: the columns are NULL either way
        logger.error("pages_cleanup_failed document_id=%s error_type=%s", did, type(exc).__name__)


@celery_app.task(
    name="docflow.render_pages",
    bind=True,
    time_limit=PAGES_TASK_TIME_LIMIT_SECONDS,
    max_retries=len(PAGES_RETRY_COUNTDOWNS_SECONDS),
)
def render_pages(self, tenant_id: str, document_id: str) -> None:  # type: ignore[no-untyped-def]
    tid, did = UUID(tenant_id), UUID(document_id)
    deadline = time.monotonic() + PAGES_DEADLINE_SECONDS
    try:
        drawn = draw(tid, did, deadline=deadline)
        # No deadline check here: every page is stored, and the one UPDATE
        # that remains is worth more than the time it saves (founder,
        # 2026-11-04). The hard limit stays the backstop.
        if not write_list(tid, did, drawn):
            logger.info("pages_not_written document_id=%s", did)
            return
    except NotWanted as exc:
        logger.info("pages_skipped document_id=%s reason=%s", did, exc.reason)
        return
    except _RETRIED as exc:
        attempt = self.request.retries
        if attempt >= len(PAGES_RETRY_COUNTDOWNS_SECONDS):
            give_up(tid, did, type(exc).__name__)
            return
        logger.warning(
            "pages_retry document_id=%s error_type=%s attempt=%d", did, type(exc).__name__, attempt + 1
        )
        try:
            self.retry(countdown=PAGES_RETRY_COUNTDOWNS_SECONDS[attempt], exc=exc)
        except Retry:
            raise
        except Reject as lost:
            # The retry couldn't be queued: Celery drops this message, so end
            # it here as a failure rather than leave its keys behind.
            give_up(tid, did, f"retry_not_queued:{type(lost.reason).__name__}")
        return
    except Deadline:
        give_up(tid, did, "deadline")
        return
    except Refused as exc:
        give_up(tid, did, f"refused:{exc.reason}")
        return
    except Exception as exc:  # noqa: BLE001 -- any other error is not retried; see the docstring
        give_up(tid, did, type(exc).__name__)
        return
    delete_stale(tid, did, drawn)
    logger.info("pages_written document_id=%s page_count=%d", did, drawn.page_count)


def queue_pages(tid: UUID, did: UUID) -> None:
    """How the document task asks for its pages. Never raises and never
    waits on a broker that is down (retry=False): a failure to queue costs
    the document its page images, never its review (founder, 2026-11-03)."""
    try:
        celery_app.send_task(
            "docflow.render_pages", args=[str(tid), str(did)], queue=PAGES_QUEUE, retry=False
        )
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.error("pages_enqueue_failed document_id=%s error_type=%s", did, type(exc).__name__)
