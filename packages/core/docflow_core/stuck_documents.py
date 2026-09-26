"""
Documents left behind by a crash are found, retried, and in the end failed
with a catalog code -- never left stuck (Section 7.9; review H3; D-158).

A worker that dies mid-job leaves its document in `processing`. The claim in
`document_status.claim_for_processing` lets a later job take over once the
claim is older than STUCK_PROCESSING_TIMEOUT_MIN; this sweep is what makes
sure there IS a later job, and that trying stops somewhere:

* `processing` past the timeout, fewer than MAX_PROCESSING_ATTEMPTS tries:
  enqueue it again. The claim takes over the stale lease.
* `processing` past the timeout after MAX_PROCESSING_ATTEMPTS tries: `failed`
  with DOC-022 and a `document_stuck` founder alert (the catalog tells the
  customer DocFlow has been alerted -- here it has).
* `pending` past the timeout: its job may have been lost before any worker
  saw it (D-095), so it is enqueued again -- a duplicate is harmless, the
  claim makes it a no-op -- and the founder gets one `document_stuck` alert
  per tenant per day saying how many are waiting. A pending document is not
  failed for waiting: a 500-document backfill legitimately waits.

Read and changed in each tenant's own tenant_session(); tenants are listed
through `pipeline_sweep_session` (migration 0027), which can read nothing else.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text

from docflow_core import document_status, founder_alerts
from docflow_core.constants import MAX_PROCESSING_ATTEMPTS, STUCK_PROCESSING_TIMEOUT_MIN
from docflow_core.db import pipeline_sweep_session, tenant_session

logger = logging.getLogger(__name__)

STUCK_CODE = "DOC-022"


@dataclass
class SweepResult:
    requeued: list[UUID] = field(default_factory=list)
    failed: list[UUID] = field(default_factory=list)
    waiting: list[UUID] = field(default_factory=list)


def sweep_tenant(
    tenant_id: UUID,
    enqueue: Callable[[UUID, UUID], None],
    *,
    timeout_min: int = STUCK_PROCESSING_TIMEOUT_MIN,
    max_attempts: int = MAX_PROCESSING_ATTEMPTS,
) -> SweepResult:
    result = SweepResult()
    with tenant_session(tenant_id) as session:
        stale = session.execute(
            text(
                """
                SELECT id, status, processing_attempts FROM documents
                WHERE deleted_at IS NULL
                  AND (
                    (status = 'processing'
                     AND (processing_started_at IS NULL
                          OR processing_started_at < now() - make_interval(mins => :t)))
                    OR (status = 'pending' AND created_at < now() - make_interval(mins => :t)
                        AND coalesce(released_at, created_at) < now() - make_interval(mins => :t))
                  )
                ORDER BY created_at, id
                """
            ),
            {"t": timeout_min},
        ).mappings().all()

        for row in stale:
            document_id = UUID(str(row["id"]))
            if row["status"] == "processing" and row["processing_attempts"] >= max_attempts:
                moved = document_status.transition(
                    session,
                    document_id,
                    from_statuses=["processing"],
                    to="failed",
                    values={"failure_code": STUCK_CODE, "processed_at": document_status.NOW},
                )
                if moved:
                    result.failed.append(document_id)
                    founder_alerts.raise_for_failure(
                        session, tenant_id=tenant_id, error_code=STUCK_CODE, document_id=document_id
                    )
            elif row["status"] == "processing":
                result.requeued.append(document_id)
            else:
                result.waiting.append(document_id)

        if result.waiting:
            day = datetime.now(timezone.utc).date().isoformat()
            founder_alerts.raise_alert(
                session,
                alert_type="document_stuck",
                severity="warning",
                tenant_id=tenant_id,
                payload={"waiting": len(result.waiting), "timeout_min": timeout_min},
                dedupe_key=f"document_stuck:waiting:{tenant_id}:{day}",
            )

    # After the commit: a job must never run against a state it can't see.
    for document_id in [*result.requeued, *result.waiting]:
        enqueue(tenant_id, document_id)
    # Ids and counts only (Section 7.10).
    if result.requeued or result.failed or result.waiting:
        logger.info(
            "stuck_sweep tenant_id=%s requeued=%d failed=%d waiting=%d",
            tenant_id,
            len(result.requeued),
            len(result.failed),
            len(result.waiting),
        )
    return result


def sweep_all(enqueue: Callable[[UUID, UUID], None]) -> dict[UUID, SweepResult]:
    with pipeline_sweep_session() as session:
        tenant_ids = [
            UUID(str(row[0]))
            for row in session.execute(text("SELECT id FROM tenants WHERE deleted_at IS NULL"))
        ]
    results: dict[UUID, SweepResult] = {}
    for tenant_id in tenant_ids:
        try:
            results[tenant_id] = sweep_tenant(tenant_id, enqueue)
        except Exception as exc:  # noqa: BLE001 -- one tenant never stops the sweep
            logger.error("stuck_sweep_failed tenant_id=%s error_type=%s", tenant_id, type(exc).__name__)
    return results
