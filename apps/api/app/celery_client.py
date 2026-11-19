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

from collections.abc import Iterable
from uuid import UUID

from celery import Celery
from docflow_core.config import get_settings
from docflow_core.constants import DISPATCH_QUEUE
from docflow_core.email_intake import LABEL_MAIL_BODY_QUEUE, LABEL_MAIL_BODY_TASK

settings = get_settings()

# No result backend: the API only sends, and never reads a task's result
# (founder, 2026-10-08; D-196; apps/worker/app/celery_app.py).
celery_client = Celery("docflow_api_client", broker=settings.redis_url)


def send_label_tasks(tenant_id: UUID, raw_email_ids: Iterable[UUID]) -> None:
    """One labelling task per released email's held text, in the order given
    (received order), after the release's transaction has committed so the
    task finds the text `pending` (B1 slice 4b; D-248 item 1). Same task and
    queue the webhook uses for a text that was never held."""
    for raw_email_id in raw_email_ids:
        celery_client.send_task(
            LABEL_MAIL_BODY_TASK, args=[str(tenant_id), str(raw_email_id)], queue=LABEL_MAIL_BODY_QUEUE
        )


def nudge_dispatcher() -> None:
    """Start a dispatch pass now, after the caller's transaction has
    committed. Passes never overlap, so extra nudges cost one quick no-op.
    On the dispatch queue, which only the worker's dispatch process reads."""
    celery_client.send_task("docflow.dispatch", queue=DISPATCH_QUEUE)
