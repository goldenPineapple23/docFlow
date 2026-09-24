"""
Jobs that run later (migration 0013, D-113): a row in `scheduled_jobs`, found
by a periodic sweep, run in its own tenant's session.

The sweep (`run_due_jobs`, called by a Celery beat task every few minutes)
claims due rows through `scheduler_session()`, which can see this one table
and nothing else, then runs each job in `tenant_session(job.tenant_id)` --
so a job's reads and writes are tenant-scoped exactly like any request's.

Claiming uses FOR UPDATE SKIP LOCKED, so two sweeps never run one job twice.
A job left 'running' by a crashed worker is released after a timeout. A job
that fails is retried with a growing delay, then marked 'failed' and
reported to the founder -- never silently dropped (Section 7.9).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import email_outbox, founder_alerts, review_digest
from docflow_core.db import scheduler_session, tenant_session

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY_MINUTES = (5, 30)  # after attempt 1, after attempt 2
RUNNING_TIMEOUT_MINUTES = 30
SWEEP_LIMIT = 25


@dataclass(frozen=True)
class Job:
    id: UUID
    tenant_id: UUID | None
    job_type: str
    payload: dict[str, Any]
    attempts: int


def claim_due_jobs(limit: int = SWEEP_LIMIT, *, tenant_id: UUID | None = None) -> list[Job]:
    """Due jobs, claimed. `tenant_id` narrows the sweep to one tenant (tests,
    and a future per-tenant "run now"); the beat sweep passes none."""
    with scheduler_session() as session:
        # A job whose worker died mid-run goes back to the queue.
        session.execute(
            text(
                "UPDATE scheduled_jobs SET status = 'pending' "
                "WHERE status = 'running' "
                "AND started_at < now() - make_interval(mins => :timeout)"
            ),
            {"timeout": RUNNING_TIMEOUT_MINUTES},
        )
        rows = session.execute(
            text(
                """
                UPDATE scheduled_jobs
                SET status = 'running', started_at = now(), attempts = attempts + 1
                WHERE id IN (
                    SELECT id FROM scheduled_jobs
                    WHERE status = 'pending' AND run_at <= now()
                      AND (CAST(:tenant_id AS uuid) IS NULL OR tenant_id = CAST(:tenant_id AS uuid))
                    ORDER BY run_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT :limit
                )
                RETURNING id, tenant_id, job_type, payload, attempts
                """
            ),
            {"limit": limit, "tenant_id": str(tenant_id) if tenant_id else None},
        ).mappings().all()
    return [
        Job(
            id=UUID(str(r["id"])),
            tenant_id=UUID(str(r["tenant_id"])) if r["tenant_id"] else None,
            job_type=r["job_type"],
            payload=dict(r["payload"] or {}),
            attempts=r["attempts"],
        )
        for r in rows
    ]


def run_job(job: Job) -> bool:
    """Run one claimed job. The handler's writes and the 'done' mark commit
    together, in the job's tenant session. Returns whether it succeeded."""
    handler = HANDLERS.get(job.job_type)
    try:
        if handler is None or job.tenant_id is None:
            raise LookupError(f"no handler for {job.job_type!r} without a tenant")
        with tenant_session(job.tenant_id) as session:
            handler(session, job)
            session.execute(
                text(
                    "UPDATE scheduled_jobs SET status = 'done', completed_at = now(), last_error = NULL "
                    "WHERE id = :id"
                ),
                {"id": str(job.id)},
            )
        return True
    except Exception as exc:  # noqa: BLE001 -- recorded, retried, and reported below
        # The exception TYPE only: a message could carry customer data (7.10).
        logger.error(
            "scheduled_job_failed job_id=%s type=%s error=%s", job.id, job.job_type, type(exc).__name__
        )
        _record_failure(job, type(exc).__name__)
        return False


def _record_failure(job: Job, error: str) -> None:
    final = job.attempts >= MAX_ATTEMPTS
    delay = RETRY_DELAY_MINUTES[min(job.attempts, len(RETRY_DELAY_MINUTES)) - 1]
    with scheduler_session() as session:
        session.execute(
            text(
                "UPDATE scheduled_jobs SET status = :status, last_error = :error, "
                "run_at = CASE WHEN :final THEN run_at ELSE now() + make_interval(mins => :delay) END "
                "WHERE id = :id"
            ),
            {
                "id": str(job.id),
                "status": "failed" if final else "pending",
                "error": error,
                "final": final,
                "delay": delay,
            },
        )
    if final and job.tenant_id is not None:
        with tenant_session(job.tenant_id) as session:
            founder_alerts.raise_alert(
                session,
                alert_type="scheduled_job_failed",
                severity="high",
                tenant_id=job.tenant_id,
                payload={"job_id": str(job.id), "job_type": job.job_type, "error": error},
                dedupe_key=f"scheduled_job_failed:{job.id}",
            )


def run_due_jobs(*, tenant_id: UUID | None = None) -> int:
    """The sweep. Returns how many jobs ran successfully."""
    return sum(run_job(job) for job in claim_due_jobs(tenant_id=tenant_id))


# ── Handlers ────────────────────────────────────────────────────────────────


def _first_week_checkin(session: Session, job: Job) -> None:
    """
    Step 9: "schedules the first-week check-in as a job that, seven days
    later, emails the customer and creates a founder alert summarizing that
    tenant's first week." Counts only -- never a document's contents (7.10).
    Test-batch documents are excluded, as from every metric (Section 3).
    """
    assert job.tenant_id is not None
    tenant = session.execute(
        text("SELECT name, status, went_live_at FROM tenants WHERE id = :id"),
        {"id": str(job.tenant_id)},
    ).mappings().first()
    if tenant is None or tenant["went_live_at"] is None:
        return
    stats = session.execute(
        text(
            """
            SELECT
                count(*) AS received,
                count(*) FILTER (WHERE d.status IN ('approved', 'exported')) AS approved,
                count(*) FILTER (WHERE d.status = 'needs_review') AS awaiting_review,
                count(*) FILTER (
                    WHERE d.status IN ('approved', 'exported')
                    AND NOT EXISTS (
                        SELECT 1 FROM review_actions ra
                        WHERE ra.document_id = d.id AND ra.action = 'edited'
                    )
                ) AS zero_edit
            FROM documents d
            WHERE NOT d.is_test_batch AND d.deleted_at IS NULL
              AND d.status NOT IN ('quarantined', 'staged')
              AND d.created_at >= :since
            """
        ),
        {"since": tenant["went_live_at"]},
    ).mappings().one()
    summary = {
        "documents_received": stats["received"],
        "documents_approved": stats["approved"],
        "zero_edit_approvals": stats["zero_edit"],
        "awaiting_review": stats["awaiting_review"],
    }

    owner = session.execute(
        text(
            "SELECT email FROM users WHERE tenant_id = :id AND role = 'owner' AND deleted_at IS NULL "
            "ORDER BY created_at LIMIT 1"
        ),
        {"id": str(job.tenant_id)},
    ).first()
    # Only a tenant still with us gets the customer email; the founder
    # hears about every one.
    if owner is not None and tenant["status"] == "active":
        email_outbox.enqueue(
            session,
            tenant_id=job.tenant_id,
            to_address=owner[0],
            template="first_week_checkin",
            params={"tenant_name": tenant["name"], **summary},
            related_type="scheduled_job",
            related_id=job.id,
        )
    founder_alerts.raise_alert(
        session,
        alert_type="first_week_checkin",
        severity="info",
        tenant_id=job.tenant_id,
        payload=summary,
        dedupe_key=f"first_week_checkin:{job.tenant_id}",
    )


def _pending_deletion_reminder(session: Session, job: Job) -> None:
    """Section 7.14: "Automated reminder emails at reasonable intervals
    (e.g. day 1, day 15, day 25)" during pending_deletion. A tenant that
    reactivated before this fired has already had the row cancelled
    (lifecycle.complete_reactivate), and one still pending_deletion but past
    its own deletion date just gets the reminder anyway -- the ready-to-
    delete alert is a separate, deduped condition (lifecycle.raise_
    ready_to_delete_alert), not this job's concern."""
    assert job.tenant_id is not None
    tenant = session.execute(
        text("SELECT name, status, deletion_scheduled_at FROM tenants WHERE id = :id"),
        {"id": str(job.tenant_id)},
    ).mappings().first()
    if tenant is None or tenant["status"] != "pending_deletion" or tenant["deletion_scheduled_at"] is None:
        return
    owner = session.execute(
        text(
            "SELECT email FROM users WHERE tenant_id = :id AND role = 'owner' AND deleted_at IS NULL "
            "ORDER BY created_at LIMIT 1"
        ),
        {"id": str(job.tenant_id)},
    ).first()
    if owner is None:
        return
    remaining = max((tenant["deletion_scheduled_at"] - datetime.now(UTC)).days, 0)
    email_outbox.enqueue(
        session,
        tenant_id=job.tenant_id,
        to_address=owner[0],
        template="pending_deletion_reminder",
        params={
            "tenant_name": tenant["name"],
            "deletion_date": tenant["deletion_scheduled_at"].date().isoformat(),
            "days_remaining": remaining,
        },
        related_type="scheduled_job",
        related_id=job.id,
    )


HANDLERS: dict[str, Callable[[Session, Job], None]] = {
    "first_week_checkin": _first_week_checkin,
    "pending_deletion_reminder": _pending_deletion_reminder,
    "review_digest": review_digest.send_digest,
}
