"""
The worker's external heartbeat (Stage 3e, part C; founder, 2026-10-02).

After a dispatch pass that succeeded, the dispatch process pings
HEARTBEAT_URL, a Healthchecks.io check (Q1). When the pings stop, Healthchecks.io
emails the founder. That covers what nothing inside DocFlow can report: a
worker that is down, and one crash-looping because it can't reach the
database. Neither raises an alert of its own, since `dispatcher_stopped` and
`worker_restarting` both need a running worker that reaches the database.

**What a ping proves** (C2): the dispatch process runs and reaches the
database (the pass wrote the heartbeat row), and the documents worker hasn't
exited (the launcher stops the dispatch worker when it does). And, through
the unclaimed check, that the documents worker is taking work: a document
dispatched but not claimed for DISPATCH_UNCLAIMED_ALERT_MIN sends `/fail`
instead, which alerts at once. Without it, a documents worker that is up but
not consuming would leave dispatched documents to cycle back to waiting
every 30 minutes (the stuck sweep, D-095) with nobody told. Not covered here,
each with its own alert: a document hanging inside a task (the 27-minute
limit, `document_stuck`), the parse service (`parse_service_unavailable`),
the model provider (`model_api_failure`).

**Throttled** (C3): at most one ping per HEARTBEAT_PING_MIN, except that a
change between success and /fail goes at once. Healthchecks.io records no
more than 5 pings a minute per check. The ping is a GET with no body: it
carries nothing (Section 7.10). Its failure is logged by error type and never
fails the pass. The URL is never logged: not by this module, and not by
httpx, whose own request log is off while the ping is sent (D-194).

Only the dispatch process calls this (`app.tasks.dispatch`), never the pass
at the end of a document task: a documents worker pinging would hide a
dispatch process that had stopped.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
from sqlalchemy import text

from docflow_core.config import get_settings
from docflow_core.constants import (
    DISPATCH_UNCLAIMED_ALERT_MIN,
    HEARTBEAT_PING_MIN,
    HEARTBEAT_PING_TIMEOUT_SECONDS,
)
from docflow_core.db import dispatcher_session

logger = logging.getLogger(__name__)

# One dispatch process per worker machine, so this process's memory is the
# whole record. A restart pinging early is harmless.
_last_kind: str | None = None
_last_at: float | None = None


def reset() -> None:
    """For tests."""
    global _last_kind, _last_at
    _last_kind = None
    _last_at = None


@contextmanager
def _httpx_request_log_off() -> Iterator[None]:
    """httpx logs every request's URL at INFO, the level the worker runs at,
    and the ping URL is a secret (D-194: the first worker deploy wrote it to
    Fly's log on every ping). Off for the ping only; every other request
    keeps its log line."""
    httpx_logger = logging.getLogger("httpx")
    level = httpx_logger.level
    httpx_logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        httpx_logger.setLevel(level)


def unclaimed_age_seconds() -> int | None:
    """Migration 0036's dispatch_unclaimed_age(): seconds since the oldest
    document still dispatched but not claimed was sent; None if there is none."""
    with dispatcher_session() as session:
        age = session.execute(text("SELECT public.dispatch_unclaimed_age()")).scalar_one()
    return None if age is None else int(age)


def after_pass(*, succeeded: bool, now: float | None = None) -> str | None:
    """
    Called by the dispatch task after each pass. Returns what was pinged
    ("success" or "fail"), or None when nothing was sent (no URL, a failed
    pass, or throttled). Never raises.
    """
    global _last_kind, _last_at
    url = get_settings().heartbeat_url
    if not url or not succeeded:
        return None
    now = time.monotonic() if now is None else now
    try:
        age = unclaimed_age_seconds()
    except Exception as exc:  # noqa: BLE001 -- no reading, no ping
        logger.error("heartbeat_unclaimed_unreadable error_type=%s", type(exc).__name__)
        return None
    kind = "fail" if age is not None and age > DISPATCH_UNCLAIMED_ALERT_MIN * 60 else "success"
    if kind == _last_kind and _last_at is not None and now - _last_at < HEARTBEAT_PING_MIN * 60:
        return None
    target = url.rstrip("/") + "/fail" if kind == "fail" else url
    try:
        with _httpx_request_log_off():
            httpx.get(target, timeout=HEARTBEAT_PING_TIMEOUT_SECONDS).raise_for_status()
    except Exception as exc:  # noqa: BLE001 -- never fails the pass
        logger.error("heartbeat_ping_failed kind=%s error_type=%s", kind, type(exc).__name__)
        return None
    if kind == "fail":
        logger.error("heartbeat_fail_sent unclaimed_age_seconds=%s", age)
    _last_kind, _last_at = kind, now
    return kind
