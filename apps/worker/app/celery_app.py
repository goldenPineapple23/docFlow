"""
The isolated parsing/extraction worker (CLAUDE.md Section 7.11): this
process is deliberately separate from the FastAPI web app (apps/api), on
its own deploy, so a crash here takes one document to `failed`, never the
web app down. Actual parsing/extraction tasks are added in Phase 1.

Two queues from day one, per the Section 5.1 scale envelope:
  - "interactive": single-document uploads and re-runs a reviewer is
    waiting on. Always drained first.
  - "bulk": large backfills (e.g. a 500-document onboarding test batch or a
    tenant's history import). Never allowed to starve "interactive".

Per-tenant fairness (one tenant's 500-document dump must not starve another
tenant's normal traffic) is a Phase 1 concern, implemented via Celery's
routing + worker concurrency/rate-limit primitives once real tasks exist --
this file only establishes the two-queue shape so that work lands in the
right place from the start.
"""

from __future__ import annotations

from celery import Celery
from docflow_core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "docflow_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
    # Without this a worker started as `celery -A app.celery_app worker`
    # registers no tasks at all, and every enqueued document is rejected as
    # an unregistered task and sits in `pending` forever (DECISIONS.md D-095).
    include=[
        "app.tasks.parse_and_extract",
        "app.tasks.generate_export",
        "app.tasks.parse_import",
        "app.tasks.scheduled_jobs",
    ],
)

# How often `celery beat` asks for due scheduled jobs (D-113). Beat only
# sends the reminder; the jobs themselves are rows in `scheduled_jobs`.
SCHEDULED_JOBS_SWEEP_SECONDS = 300

celery_app.conf.update(
    task_default_queue="interactive",
    task_queues={
        "interactive": {"exchange": "interactive", "routing_key": "interactive"},
        "bulk": {"exchange": "bulk", "routing_key": "bulk"},
    },
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "run-scheduled-jobs": {
            "task": "docflow.run_scheduled_jobs",
            "schedule": SCHEDULED_JOBS_SWEEP_SECONDS,
            "options": {"queue": "bulk"},
        },
    },
)
