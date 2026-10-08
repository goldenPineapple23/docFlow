"""
A minimal Celery client the API uses only to enqueue tasks by name -- it
never defines or imports task bodies (those live in apps/worker, per
CLAUDE.md Section 7.11: parsing/extraction never runs in the web process).
Points at the same broker as apps/worker/app/celery_app.py so tasks sent
from here land on the same queues.

Stage 3d: the API never sends a document task. A new, released or
test-batch document waits as `pending`, and the API nudges the dispatcher,
which decides what goes to the queue next (docflow_core.dispatch).
"""

from __future__ import annotations

from celery import Celery
from docflow_core.config import get_settings
from docflow_core.constants import DISPATCH_QUEUE

settings = get_settings()

# No result backend: the API only sends, and never reads a task's result
# (founder, 2026-10-08; D-196; apps/worker/app/celery_app.py).
celery_client = Celery("docflow_api_client", broker=settings.redis_url)


def nudge_dispatcher() -> None:
    """Start a dispatch pass now, after the caller's transaction has
    committed. Passes never overlap, so extra nudges cost one quick no-op.
    On the dispatch queue, which only the worker's dispatch process reads."""
    celery_client.send_task("docflow.dispatch", queue=DISPATCH_QUEUE)
