"""
The stuck-document sweep (Section 7.9; review H3; D-158). Celery beat sends it
every STUCK_SWEEP_SECONDS; the work is docflow_core.stuck_documents, so its
tests need no queue. A document it re-queues is claimed again through the
normal task, which takes over the dead worker's stale claim.
"""

from __future__ import annotations

from uuid import UUID

from docflow_core import stuck_documents

from app.celery_app import celery_app


def _enqueue(tenant_id: UUID, document_id: UUID) -> None:
    celery_app.send_task(
        "docflow.parse_and_extract", args=[str(tenant_id), str(document_id)], queue="interactive"
    )


@celery_app.task(name="docflow.sweep_stuck_documents")
def sweep_stuck_documents() -> None:
    stuck_documents.sweep_all(_enqueue)
