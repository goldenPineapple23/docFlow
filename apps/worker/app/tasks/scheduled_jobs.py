"""
The scheduled-jobs sweep (DECISIONS.md D-113). Celery beat sends this every
few minutes; the work is `docflow_core.scheduled_jobs.run_due_jobs`, so its
tests need no queue. Due jobs (the first-week check-in, and the lifecycle
jobs still to come) live in the `scheduled_jobs` table, not in Redis, so a
restart loses nothing.
"""

from __future__ import annotations

from docflow_core.scheduled_jobs import run_due_jobs

from app.celery_app import celery_app


@celery_app.task(name="docflow.run_scheduled_jobs")
def run_scheduled_jobs() -> int:
    return run_due_jobs()
