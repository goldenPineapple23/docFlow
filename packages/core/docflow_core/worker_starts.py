"""
The worker's restart record (Stage 3e, part B; founder, 2026-10-01).

A worker that restarts slowly (up a few minutes, then a worker exits, again
and again) keeps the dispatcher's heartbeat fresh, so /healthz can't show it
(RUNBOOK 9.2). So the launcher (`app.run_workers`) records every start, before
it starts the two Celery workers, and raises `worker_restarting` itself at
WORKER_RESTART_ALERT_STARTS starts within WORKER_RESTART_WINDOW_MIN. It is
the launcher that raises it because, in a crash loop, nothing else gets the
chance: the stuck sweep that raises `dispatcher_stopped` may never run.

Migration 0036's `worker_starts` table is global (no tenant, no customer
data) and is reached only through two SECURITY DEFINER functions:
`record_worker_start()` (docflow_worker) and `worker_starts_last_hour()`
(docflow_api for /healthz, docflow_admin).

Recording never stops the worker from starting: if the database can't be
reached, the error type is logged and the launcher carries on. A worker that
can't reach the database is the external heartbeat's to report.
"""

from __future__ import annotations

import logging
import os
import socket

from sqlalchemy import text

from docflow_core import founder_alerts
from docflow_core.constants import WORKER_RESTART_ALERT_STARTS, WORKER_RESTART_WINDOW_MIN
from docflow_core.db import dispatcher_session, function_session

logger = logging.getLogger(__name__)


def this_machine() -> tuple[str, str | None]:
    """Fly's machine id and image when on Fly; the host name locally."""
    machine = os.environ.get("FLY_MACHINE_ID") or socket.gethostname()
    return machine, os.environ.get("FLY_IMAGE_REF") or None


def record_start() -> int | None:
    """
    Record this start; raise `worker_restarting` if it makes
    WORKER_RESTART_ALERT_STARTS or more within the window. Returns the
    starts in the window, or None if the database couldn't be reached.
    Never raises.
    """
    machine, image = this_machine()
    try:
        with dispatcher_session() as session:
            starts = int(
                session.execute(
                    text("SELECT public.record_worker_start(:machine, :image)"),
                    {"machine": machine, "image": image},
                ).scalar_one()
            )
            if starts >= WORKER_RESTART_ALERT_STARTS:
                raised = founder_alerts.raise_alert(
                    session,
                    alert_type="worker_restarting",
                    severity="high",
                    tenant_id=None,
                    payload={
                        "starts_last_hour": starts,
                        "machine": machine,
                        "alert_at_starts": WORKER_RESTART_ALERT_STARTS,
                        "window_min": WORKER_RESTART_WINDOW_MIN,
                    },
                    dedupe_key="worker_restarting",
                    dedupe_per_utc_hour=True,
                )
                if raised:
                    logger.error("worker_restarting starts_last_hour=%d", starts)
    except Exception as exc:  # noqa: BLE001 -- never stop the worker starting
        logger.error("worker_start_not_recorded error_type=%s", type(exc).__name__)
        return None
    logger.info("worker_start_recorded starts_last_hour=%d", starts)
    return starts


def starts_last_hour() -> int:
    """For /healthz. Raises if the database can't be reached; the caller
    decides what to show."""
    with function_session() as session:
        return int(session.execute(text("SELECT public.worker_starts_last_hour()")).scalar_one())
