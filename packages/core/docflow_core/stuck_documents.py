"""
Documents left behind by a crash are found, retried, and in the end failed
with a catalog code -- never left stuck (Section 7.9; review H3; D-158).

A worker that dies mid-job leaves its document in `processing`. The claim in
`document_status.claim_for_processing` lets a later job take over once the
claim is older than STUCK_PROCESSING_TIMEOUT_MIN; this sweep is what makes
sure there IS a later job, and that trying stops somewhere:

* `processing` past the timeout: `decide()` says retry or fail (the table
  is in its docstring). A retry enqueues it again and the claim takes over
  the stale lease. A failure is DOC-022 with a `document_stuck` founder
  alert (the catalog tells the customer DocFlow has been alerted -- here it
  has) whose payload names the cause: `timeout` when the document task hit
  its hard time limit (recorded by `document_status.record_timeout`, Stage 3a), otherwise
  `worker_stopped`. One alert per tenant per cause per day, so a timeout is
  never hidden inside a dead-worker alert from the same day.
* `pending` past the timeout: its job may have been lost before any worker
  saw it (D-095), so it is enqueued again -- a duplicate is harmless, the
  claim makes it a no-op -- and the founder gets one `document_stuck` alert
  per tenant per day saying how many are waiting. A pending document is not
  failed for waiting: a 500-document backfill legitimately waits.
* Exports still `pending`, and imports still `parsing`, STUCK_PROCESSING_
  TIMEOUT_MIN after they were created are failed: EXP-009 / IMP-009 (Stage
  3a). Their jobs are short (5-minute hard limits), so by then the job was
  lost or killed; the reader starts it again. The export and import jobs only
  ever finish a row still in that state, so one arriving late changes nothing.
  No alert per export (EXP-009 promises none): the health strip shows the
  day's count, and a tenant with more than EXPORTS_NOT_FINISHED_ALERT_PER_DAY
  in a UTC day raises one `exports_not_finishing` alert that day.

Read and changed in each tenant's own tenant_session(); tenants are listed
through `pipeline_sweep_session` (migration 0027), which can read nothing else.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import document_status, founder_alerts
from docflow_core.constants import (
    EXPORTS_NOT_FINISHED_ALERT_PER_DAY,
    MAX_PROCESSING_ATTEMPTS,
    STUCK_PROCESSING_TIMEOUT_MIN,
)
from docflow_core.db import pipeline_sweep_session, tenant_session

logger = logging.getLogger(__name__)

STUCK_CODE = "DOC-022"
EXPORT_NOT_FINISHED_CODE = "EXP-009"
IMPORT_NOT_FINISHED_CODE = "IMP-009"
CAUSE_TIMEOUT = "timeout"
CAUSE_WORKER_STOPPED = "worker_stopped"


def decide(
    processing_attempts: int, timeout_attempts: list[int], *, max_attempts: int = MAX_PROCESSING_ATTEMPTS
) -> tuple[str, str | None]:
    """
    What to do with a document stuck in `processing`: ("retry", None) or
    ("fail", cause). Agreed with the founder before building (Stage 3a):

    | State                                          | Outcome              |
    |------------------------------------------------|----------------------|
    | its first timeout was on the latest attempt    | retry once           |
    | a try has already run since its first timeout  | fail, cause timeout  |
    | no timeout, max_attempts used                  | fail, worker_stopped |
    | otherwise                                      | retry                |

    A timeout gets at most one retry: a file that hangs a parser will hang
    it again. Pure, so the table is tested without a database.
    """
    if timeout_attempts:
        first_timeout = min(timeout_attempts)
        if processing_attempts > first_timeout:
            return ("fail", CAUSE_TIMEOUT)
        return ("retry", None)
    if processing_attempts >= max_attempts:
        return ("fail", CAUSE_WORKER_STOPPED)
    return ("retry", None)


@dataclass
class SweepResult:
    requeued: list[UUID] = field(default_factory=list)
    failed: list[UUID] = field(default_factory=list)
    waiting: list[UUID] = field(default_factory=list)
    exports_failed: list[UUID] = field(default_factory=list)
    imports_failed: list[UUID] = field(default_factory=list)


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
                SELECT id, status, processing_attempts, timeout_attempts FROM documents
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
            if row["status"] != "processing":
                result.waiting.append(document_id)
                continue
            timeout_attempts = [int(n) for n in (row["timeout_attempts"] or [])]
            attempts = int(row["processing_attempts"])
            action, cause = decide(attempts, timeout_attempts, max_attempts=max_attempts)
            if action == "retry":
                result.requeued.append(document_id)
                continue
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
                    session,
                    tenant_id=tenant_id,
                    error_code=STUCK_CODE,
                    document_id=document_id,
                    cause=cause,
                    # Numbers only (Section 7.10: the payload is emailed).
                    detail={"attempts": attempts, "timed_out_attempts": timeout_attempts},
                )

        if result.waiting:
            founder_alerts.raise_alert(
                session,
                alert_type="document_stuck",
                severity="warning",
                tenant_id=tenant_id,
                payload={"waiting": len(result.waiting), "timeout_min": timeout_min},
                dedupe_key=f"document_stuck:waiting:{tenant_id}",
                dedupe_per_utc_day=True,
            )

        _fail_unfinished_exports_and_imports(session, tenant_id, result, timeout_min)

    # After the commit: a job must never run against a state it can't see.
    for document_id in [*result.requeued, *result.waiting]:
        enqueue(tenant_id, document_id)
    # Ids and counts only (Section 7.10).
    if result.requeued or result.failed or result.waiting or result.exports_failed or result.imports_failed:
        logger.info(
            "stuck_sweep tenant_id=%s requeued=%d failed=%d waiting=%d exports_failed=%d imports_failed=%d",
            tenant_id,
            len(result.requeued),
            len(result.failed),
            len(result.waiting),
            len(result.exports_failed),
            len(result.imports_failed),
        )
    return result


def _fail_unfinished_exports_and_imports(
    session: Session, tenant_id: UUID, result: SweepResult, timeout_min: int
) -> None:
    """EXP-009 / IMP-009 for jobs that never finished (the module docstring)."""
    result.exports_failed = [
        UUID(str(row[0]))
        for row in session.execute(
            text(
                """
                UPDATE exports SET status = 'failed', error_code = :code, generated_at = now()
                WHERE tenant_id = :tid AND status = 'pending' AND deleted_at IS NULL
                  AND requested_at < now() - make_interval(mins => :t)
                RETURNING id
                """
            ),
            {"tid": str(tenant_id), "code": EXPORT_NOT_FINISHED_CODE, "t": timeout_min},
        )
    ]
    result.imports_failed = [
        UUID(str(row[0]))
        for row in session.execute(
            text(
                """
                UPDATE catalog_imports SET status = 'failed', error_code = :code
                WHERE tenant_id = :tid AND status = 'parsing' AND deleted_at IS NULL
                  AND created_at < now() - make_interval(mins => :t)
                RETURNING id
                """
            ),
            {"tid": str(tenant_id), "code": IMPORT_NOT_FINISHED_CODE, "t": timeout_min},
        )
    ]
    if not result.exports_failed:
        return
    today = session.execute(
        text(
            """
            SELECT count(*) FROM exports
            WHERE tenant_id = :tid AND error_code = :code
              AND generated_at >= (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
            """
        ),
        {"tid": str(tenant_id), "code": EXPORT_NOT_FINISHED_CODE},
    ).scalar_one()
    if today > EXPORTS_NOT_FINISHED_ALERT_PER_DAY:
        founder_alerts.raise_alert(
            session,
            alert_type="exports_not_finishing",
            severity="warning",
            tenant_id=tenant_id,
            # Counts only (Section 7.10: the payload is emailed).
            payload={
                "error_code": EXPORT_NOT_FINISHED_CODE,
                "exports_not_finished_today": int(today),
                "alert_above": EXPORTS_NOT_FINISHED_ALERT_PER_DAY,
            },
            dedupe_key=f"exports_not_finishing:{tenant_id}",
            dedupe_per_utc_day=True,
        )


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
