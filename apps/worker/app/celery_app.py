"""
The isolated parsing/extraction worker (CLAUDE.md Section 7.11): this
process is deliberately separate from the FastAPI web app (apps/api), on
its own deploy, so a crash here takes one document to `failed`, never the
web app down.

Three queues:
  - "interactive": every document task, sent only by the dispatcher
    (docflow_core.dispatch), plus exports and imports.
  - "bulk": scheduled sweeps and other background work.
  - "dispatch": the dispatch task, and nothing else. Read only by the worker
    machine's one dispatch process (app.run_workers), never by the document
    worker (founder, 2026-10-01).
Celery's Redis transport takes turns between the queues a worker reads
(kombu's round-robin queue order); it does NOT drain "interactive" first.
Until Stage 3d this docstring claimed it did (a doc/code contradiction found
2026-10-01). Since 3d it no longer matters for documents: per-tenant
fairness and "interactive before bulk" within a tenant are decided by the
dispatcher, which keeps the queue no longer than the worker's document
slots (DISPATCH_IN_FLIGHT_TARGET), so the queue's own order decides nothing.

Celery beat runs as its own process (README locally; the `beat` process
group on Fly, exactly one -- BUILD-STATUS "3d -- APPROVED WITH CHANGES",
change C). Every beat task is safe if two beats ever fire it.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_init, worker_process_init
from docflow_core import db
from docflow_core.config import get_settings
from docflow_core.constants import DISPATCH_INTERVAL_SECONDS, DISPATCH_QUEUE, WORKER_MAX_MEMORY_PER_CHILD_KIB

settings = get_settings()

# Stage 3e (F-1): every worker session runs as docflow_worker.
db.use_own_login("worker")

celery_app = Celery(
    "docflow_worker",
    broker=settings.redis_url,
    # No result backend (founder, 2026-10-08; D-196). Nothing reads a task's
    # result: every send is a bare `send_task`, and what a task did is in the
    # database. With a backend, each of the roughly 156 scheduled tasks an
    # hour stored a result in Redis for a day that nobody fetched.
    # Without this a worker started as `celery -A app.celery_app worker`
    # registers no tasks at all, and every enqueued document is rejected as
    # an unregistered task and sits in `pending` forever (DECISIONS.md D-095).
    include=[
        "app.tasks.parse_and_extract",
        "app.tasks.generate_export",
        "app.tasks.parse_import",
        "app.tasks.daily_rollup",
        "app.tasks.scheduled_jobs",
        "app.tasks.lifecycle_sweep",
        "app.tasks.stuck_sweep",
        "app.tasks.dispatch",
    ],
)

# How often `celery beat` asks for due scheduled jobs (D-113). Beat only
# sends the reminder; the jobs themselves are rows in `scheduled_jobs`.
SCHEDULED_JOBS_SWEEP_SECONDS = 300
# The nightly rollup (Section 7.15.3). 03:15 UTC: after midnight in every US
# timezone DocFlow serves, and well clear of the working day it summarises.
DAILY_ROLLUP_HOUR_UTC = 3
DAILY_ROLLUP_MINUTE_UTC = 15
# The lifecycle sweep (Section 7.15.4, D-123): moves cancelling tenants to
# suspended/pending_deletion at their effective date, and raises the
# ready-to-delete alert. Same cadence as the scheduled-jobs sweep -- neither
# needs to be tighter than a few minutes for a solo-founder's tenant count.
LIFECYCLE_SWEEP_SECONDS = 300
# The stuck-document sweep (Section 7.9, D-158): finds documents a dead worker
# left in `processing`, or whose job was lost while `pending`, and re-queues
# or fails them. Well inside STUCK_PROCESSING_TIMEOUT_MIN.
STUCK_SWEEP_SECONDS = 300
# How long Redis waits before handing an unacknowledged job to another worker
# (tasks acknowledge late, so a worker that dies mid-job has its job
# redelivered). Longer than any task should run; a redelivery that arrives
# anyway is a no-op, because the task claims its document first (H3, D-158).
BROKER_VISIBILITY_TIMEOUT_SECONDS = 2 * 60 * 60
# How long a worker's blocking read on its queues waits before it asks Redis
# again (kombu's `polling_interval` option, which sets its BRPOP timeout; 1 s
# by default). Once a worker is running, the read returns the moment a job
# arrives, so this is not a delay before a document is picked up: it is how
# often an idle worker repeats the question. The exception is a worker's
# start: for about this long after `ready` it received nothing, a job already
# in the queue included (measured on Fly staging at four starts of four,
# 2026-10-08; D-197. The cause is read as Celery's start-up neighbour search,
# mingle, and is not proven). At 1 s the two worker processes sent about 7,200
# commands an hour, half of everything Upstash billed for an idle worker
# (measured at the first worker deploy; D-196). Two things follow from it:
#   * a stopping worker waits for the read it has open, so shutdown takes up
#     to this long more: `kill_timeout` in fly.toml must stay well above it
#     (tests/test_celery_app.py holds the two together);
#   * without the event loop (the `solo` pool on Windows, local development)
#     kombu also sleeps after an empty read, for up to about 2 s. The
#     deployed prefork worker has the event loop and does not.
BROKER_POLL_SECONDS = 10

celery_app.conf.update(
    task_default_queue="interactive",
    task_queues={
        "interactive": {"exchange": "interactive", "routing_key": "interactive"},
        "bulk": {"exchange": "bulk", "routing_key": "bulk"},
        DISPATCH_QUEUE: {"exchange": DISPATCH_QUEUE, "routing_key": DISPATCH_QUEUE},
    },
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Stage 3d (founder, Q2): the document worker's slots ARE the
    # dispatcher's in-flight target, set from the one setting so the two
    # can't drift. The dispatch pass has its own process on the same machine,
    # reading only the dispatch queue (app.run_workers sets it to 1), so a
    # 15-minute order can't hold up the heartbeat and make /healthz read
    # "stale" (gap 1). RUNBOOK 9.2 when adding workers.
    worker_concurrency=settings.dispatch_in_flight_target,
    # Stage 3a (review H5): a worker process is replaced after the task that
    # took it past this much memory. Time limits are per task, on each task's
    # decorator (hard only; see docflow_core.constants), and both need
    # Celery's prefork pool, which production runs: the `solo` pool used on
    # Windows ignores them.
    worker_max_memory_per_child=WORKER_MAX_MEMORY_PER_CHILD_KIB,
    broker_transport_options={
        "visibility_timeout": BROKER_VISIBILITY_TIMEOUT_SECONDS,
        "polling_interval": BROKER_POLL_SECONDS,
    },
    # With no backend there is nowhere to store a result; this says so for
    # every task as well, so configuring a backend later stores nothing
    # until someone decides a task's result is wanted.
    task_ignore_result=True,
    beat_schedule={
        "run-scheduled-jobs": {
            "task": "docflow.run_scheduled_jobs",
            "schedule": SCHEDULED_JOBS_SWEEP_SECONDS,
            "options": {"queue": "bulk"},
        },
        "run-daily-rollup": {
            "task": "docflow.run_daily_rollup",
            "schedule": crontab(hour=DAILY_ROLLUP_HOUR_UTC, minute=DAILY_ROLLUP_MINUTE_UTC),
            "options": {"queue": "bulk"},
        },
        "run-lifecycle-sweep": {
            "task": "docflow.run_lifecycle_sweep",
            "schedule": LIFECYCLE_SWEEP_SECONDS,
            "options": {"queue": "bulk"},
        },
        "sweep-stuck-documents": {
            "task": "docflow.sweep_stuck_documents",
            "schedule": STUCK_SWEEP_SECONDS,
            "options": {"queue": "bulk"},
        },
        # Stage 3d: the dispatcher's backstop (Q4). Expires rather than piling
        # up behind a busy worker: a later beat sends a fresh one.
        "dispatch-waiting-documents": {
            "task": "docflow.dispatch",
            "schedule": DISPATCH_INTERVAL_SECONDS,
            "options": {"queue": DISPATCH_QUEUE, "expires": DISPATCH_INTERVAL_SECONDS},
        },
    },
)


@worker_process_init.connect
def _forget_inherited_db_connections(**_kwargs: object) -> None:
    """Stage 3a: the main process now writes to the database (it records the
    document task's timeouts, app.timeouts), and prefork forks each
    replacement child from it after a kill. A child must never reuse the
    parent's pooled connections."""
    from docflow_core.db import forget_inherited_connections

    forget_inherited_connections()


@worker_init.connect
def _check_own_login(**_kwargs: object) -> None:
    """Stage 3e (A4): the worker's URL must connect as docflow_worker, or the
    worker stops before taking a task (a pasted API URL would otherwise run
    every job with the API's policies). Unreachable is logged, not fatal:
    the heartbeat stops and the external monitor says so (RUNBOOK 9.4)."""
    import logging

    try:
        db.verify_logins(("worker",))
    except db.WrongLoginError as exc:
        logging.getLogger("docflow.worker").critical("worker_refused_to_start %s", exc)
        raise SystemExit(1) from None
    except Exception as exc:  # noqa: BLE001 -- unreachable, not wrong
        logging.getLogger("docflow.worker").error(
            "login_startup_check_unreachable error_type=%s", type(exc).__name__
        )
