"""
The pages backfill (viewer A6; DECISIONS.md D-236): queue the normal page
render (`docflow.render_pages`, D-232) for every reviewable document that has
no page images yet. There is no second pipeline: everything after the queue is
A3's task as merged, which re-checks the document in its tenant's own session,
draws the pages in the parse service's sandbox and writes the list once.

Run on the worker machine, under a Grant (RUNBOOK 9.2), never in the 48 hours
before a booked demo (D-235 item 3):

    python -m app.backfill_pages --dry-run          # counts only, queues nothing
    python -m app.backfill_pages --limit 5          # a first small run
    python -m app.backfill_pages                    # everything still not drawn

* **Which documents.** `page_count` NULL, not deleted, the tenant not
  `pending_deletion` or `deleted`, and a status a reviewer opens:
  BACKFILL_STATUSES. Never `quarantined` (never parsed), `failed` (no review
  screen), or `pending`, `staged` and `processing` (the live pipeline queues
  their render itself). Oldest first.
* **Tenant scope (Section 7.5).** Tenants are listed through
  `pipeline_sweep_session`, which reads tenant ids and nothing else (founder,
  2026-11-11; D-158's note). Each tenant's documents are read in its own
  `tenant_session()`, and each render carries the tenant id from there.
* **Bounded.** At most `--batch` renders are queued at once; the next batch
  waits until the `pages` queue is empty, so a new document's render waits
  behind one batch at most. Documents never wait on it: they have their own
  process (D-232 item 2). A queue that doesn't empty within `--max-wait`
  seconds stops the run.
* **Idempotent and resumable.** It keeps no state: the database is the state.
  A second run selects only what is still NULL, and a render that finds its
  list already written stops (`pages_skipped reason=already_drawn`). A
  document whose render fails is queued once per run, never in a loop.
* **What it prints (Section 7.10):** counts, tenant ids and document ids,
  never a filename or anything from a document. Failures are already logged
  by the task (`pages_failed document_id=... error_type=...`).
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from docflow_core.constants import PAGES_QUEUE, PAGES_TASK_TIME_LIMIT_SECONDS
from docflow_core.db import pipeline_sweep_session, tenant_session
from sqlalchemy import text

# The statuses a reviewer opens (founder, 2026-11-11).
BACKFILL_STATUSES = ("needs_review", "approved", "exported", "rejected")
DEFAULT_BATCH = 3
MAX_BATCH = 10
POLL_SECONDS = 5
# Two renders' hard limit: past this the pages process isn't taking work.
DEFAULT_MAX_WAIT_SECONDS = 2 * PAGES_TASK_TIME_LIMIT_SECONDS

_STATUSES_SQL = ", ".join(f"'{status}'" for status in BACKFILL_STATUSES)


class QueueNotDraining(Exception):
    """The pages queue didn't empty within --max-wait seconds."""


class BrokerUnavailable(Exception):
    """The broker's queue length couldn't be read."""


@dataclass(frozen=True)
class Candidate:
    tenant_id: UUID
    document_id: UUID


def tenant_ids() -> list[UUID]:
    """Every tenant's id, and nothing else, through the one narrow session
    that may list them (migration 0027's `pipeline_sweep_read`)."""
    with pipeline_sweep_session() as session:
        return [
            UUID(str(row[0]))
            for row in session.execute(text("SELECT id FROM tenants WHERE deleted_at IS NULL"))
        ]


def tenant_candidates(tenant_id: UUID) -> list[tuple[tuple[datetime, str], Candidate]]:
    """One tenant's documents with no pages yet, read in its own session."""
    with tenant_session(tenant_id) as session:
        rows = session.execute(
            text(
                "SELECT d.id, d.created_at FROM documents d JOIN tenants t ON t.id = d.tenant_id "
                "WHERE d.tenant_id = :tid AND d.page_count IS NULL AND d.deleted_at IS NULL "
                f"AND d.status IN ({_STATUSES_SQL}) "
                "AND t.status NOT IN ('pending_deletion', 'deleted') "
                "ORDER BY d.created_at, d.id"
            ),
            {"tid": str(tenant_id)},
        ).all()
    return [((row[1], str(row[0])), Candidate(tenant_id, UUID(str(row[0])))) for row in rows]


def select_candidates() -> list[Candidate]:
    """Every tenant's candidates, oldest first across tenants."""
    keyed: list[tuple[tuple[datetime, str], Candidate]] = []
    for tid in tenant_ids():
        keyed.extend(tenant_candidates(tid))
    keyed.sort(key=lambda pair: pair[0])
    return [candidate for _, candidate in keyed]


def wait_for_empty_queue(
    queue_length: Callable[[], int | None],
    *,
    max_wait: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    deadline = clock() + max_wait
    while True:
        length = queue_length()
        if length is None:
            raise BrokerUnavailable()
        if length == 0:
            return
        if clock() >= deadline:
            raise QueueNotDraining()
        sleep(POLL_SECONDS)


def run(
    candidates: Sequence[Candidate],
    send: Callable[[Candidate], None],
    queue_length: Callable[[], int | None],
    *,
    batch: int = DEFAULT_BATCH,
    max_wait: float = DEFAULT_MAX_WAIT_SECONDS,
    out: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Queue every candidate, `batch` at a time, each batch only once the
    pages queue is empty. Returns how many were queued."""
    if not 1 <= batch <= MAX_BATCH:
        raise ValueError(f"batch must be 1 to {MAX_BATCH}")
    queued = 0
    for start in range(0, len(candidates), batch):
        wait_for_empty_queue(queue_length, max_wait=max_wait, sleep=sleep, clock=clock)
        for candidate in candidates[start : start + batch]:
            send(candidate)
            queued += 1
            out(f"backfill_pages queued tenant_id={candidate.tenant_id} document_id={candidate.document_id}")
    return queued


def dry_run_lines(candidates: Sequence[Candidate]) -> list[str]:
    counts = Counter(candidate.tenant_id for candidate in candidates)
    lines = [
        f"backfill_pages dry_run tenant_id={tid} documents={count}"
        for tid, count in sorted(counts.items(), key=str)
    ]
    lines.append(f"backfill_pages dry_run total={len(candidates)} tenants={len(counts)}")
    return lines


def _send(candidate: Candidate) -> None:
    from app.celery_app import celery_app

    # retry=False: a broker that is down stops the run rather than hanging it.
    try:
        celery_app.send_task(
            "docflow.render_pages",
            args=[str(candidate.tenant_id), str(candidate.document_id)],
            queue=PAGES_QUEUE,
            retry=False,
        )
    except Exception as exc:  # noqa: BLE001 -- any send failure stops the run (main)
        raise BrokerUnavailable() from exc


def _pages_queue_length() -> int | None:
    """The `pages` list's length on the broker, as the Console's strip reads
    it. None when the broker can't be read."""
    try:
        import redis
        from docflow_core.config import get_settings

        client = redis.Redis.from_url(get_settings().redis_url, socket_timeout=5)
        return int(client.llen(PAGES_QUEUE))  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 -- reported by the caller as the broker being unreadable
        return None


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.backfill_pages", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("--dry-run", action="store_true", help="count per tenant, queue nothing")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH, help=f"1 to {MAX_BATCH}")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many documents")
    parser.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT_SECONDS, help="seconds")
    args = parser.parse_args(argv)
    if not 1 <= args.batch <= MAX_BATCH:
        parser.error(f"--batch must be 1 to {MAX_BATCH}")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.max_wait < POLL_SECONDS:
        parser.error(f"--max-wait must be at least {POLL_SECONDS}")
    return args


def main(
    argv: Sequence[str] | None = None,
    *,
    select: Callable[[], list[Candidate]] = select_candidates,
    send: Callable[[Candidate], None] = _send,
    queue_length: Callable[[], int | None] = _pages_queue_length,
) -> int:
    args = _parse(argv)
    candidates = select()
    if args.limit is not None:
        candidates = candidates[: args.limit]
    if args.dry_run:
        for line in dry_run_lines(candidates):
            print(line, flush=True)
        return 0
    try:
        queued = run(
            candidates,
            send,
            queue_length,
            batch=args.batch,
            max_wait=args.max_wait,
            out=lambda line: print(line, flush=True),
        )
    except BrokerUnavailable:
        print("backfill_pages stopped: the broker's pages queue can't be read", file=sys.stderr, flush=True)
        return 1
    except QueueNotDraining:
        print(
            f"backfill_pages stopped: the pages queue didn't empty in {args.max_wait} s; "
            "is the pages process running? (RUNBOOK 9.2)",
            file=sys.stderr,
            flush=True,
        )
        return 1
    print(
        f"backfill_pages done queued={queued}. Run --dry-run once the pages queue is empty "
        "to count what is still not drawn.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
