# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
Scheduled jobs (DECISIONS.md D-113; migration 0013) against the real
database: the sweep claims only due jobs, never runs one twice, runs each in
its own tenant's session, and a failing job is retried and then reported to
the founder -- never silently dropped (Section 7.9).

The first-week check-in (Section 7.15.2 Step 9) is the first real job: it
emails the customer a count-only summary and raises a founder alert.
All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import UUID, uuid4

from docflow_core import scheduled_jobs
from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import requires_console_schema
from tests.test_console_api import _Console, _environment, stripe  # noqa: F401 -- fixtures
from tests.test_onboarding_api import requires_go_live_schema


def _live_tenant(client, console) -> str:
    tenant_id = console.create_tenant(client).json()["tenant_id"]
    with platform_session() as session:
        session.execute(
            text("UPDATE tenants SET went_live_at = now() - interval '7 days' WHERE id = :t"),
            {"t": tenant_id},
        )
    return tenant_id


def _job(tenant_id, *, due: bool = True, job_type: str = "first_week_checkin") -> str:
    job_id = str(uuid4())
    with platform_session() as session:
        session.execute(
            text(
                "INSERT INTO scheduled_jobs (id, tenant_id, job_type, run_at, dedupe_key) "
                "VALUES (:id, :t, :type, now() + (CASE WHEN :due THEN interval '-1 minute' "
                "ELSE interval '1 day' END), :dedupe)"
            ),
            {"id": job_id, "t": tenant_id, "type": job_type, "due": due, "dedupe": f"test:{job_id}"},
        )
    return job_id


def _row(job_id) -> dict:
    with platform_session() as session:
        return dict(
            session.execute(
                text("SELECT status, attempts, last_error FROM scheduled_jobs WHERE id = :id"), {"id": job_id}
            ).mappings().one()
        )


def _cleanup(tenant_id) -> None:
    with platform_session() as session:
        session.execute(text("DELETE FROM scheduled_jobs WHERE tenant_id = :t"), {"t": tenant_id})


@requires_go_live_schema
@requires_console_schema
def test_a_due_first_week_checkin_emails_the_customer_and_alerts_the_founder(client, stripe):
    with _Console() as console:
        tenant_id = _live_tenant(client, console)
        try:
            due = _job(tenant_id)
            later = _job(tenant_id, due=False)

            assert scheduled_jobs.run_due_jobs(tenant_id=UUID(tenant_id)) == 1
            assert _row(due)["status"] == "done"
            assert _row(later)["status"] == "pending"  # not due, not touched

            emails = [e for e in console.outbox(client, tenant_id) if e["template"] == "first_week_checkin"]
            assert len(emails) == 1
            with platform_session() as session:
                alert = session.execute(
                    text("SELECT type, severity, payload FROM founder_alerts WHERE tenant_id = :t"),
                    {"t": tenant_id},
                ).mappings().one()
            assert (alert["type"], alert["severity"]) == ("first_week_checkin", "info")
            assert set(alert["payload"]) == {
                "documents_received", "documents_approved", "zero_edit_approvals", "awaiting_review",
            }

            # A second sweep finds nothing: a job never runs twice.
            assert scheduled_jobs.run_due_jobs(tenant_id=UUID(tenant_id)) == 0
        finally:
            _cleanup(tenant_id)


@requires_go_live_schema
@requires_console_schema
def test_a_failing_job_is_retried_then_marked_failed_and_reported(client, stripe, monkeypatch):
    def broken(session, job):
        raise RuntimeError("test failure")

    monkeypatch.setitem(scheduled_jobs.HANDLERS, "first_week_checkin", broken)
    with _Console() as console:
        tenant_id = _live_tenant(client, console)
        try:
            job_id = _job(tenant_id)
            for attempt in range(1, scheduled_jobs.MAX_ATTEMPTS + 1):
                assert scheduled_jobs.run_due_jobs(tenant_id=UUID(tenant_id)) == 0
                row = _row(job_id)
                assert row["attempts"] == attempt
                assert row["last_error"] == "RuntimeError"  # the type, never a message
                if attempt < scheduled_jobs.MAX_ATTEMPTS:
                    assert row["status"] == "pending"
                    with platform_session() as session:  # make the retry due now
                        session.execute(
                            text("UPDATE scheduled_jobs SET run_at = now() WHERE id = :id"), {"id": job_id}
                        )
            assert _row(job_id)["status"] == "failed"
            with platform_session() as session:
                alert_type = session.execute(
                    text("SELECT type FROM founder_alerts WHERE tenant_id = :t"), {"t": tenant_id}
                ).scalar_one()
            assert alert_type == "scheduled_job_failed"
        finally:
            _cleanup(tenant_id)


@requires_go_live_schema
@requires_console_schema
def test_a_job_left_running_by_a_dead_worker_is_picked_up_again(client, stripe):
    with _Console() as console:
        tenant_id = _live_tenant(client, console)
        try:
            job_id = _job(tenant_id)
            with platform_session() as session:
                session.execute(
                    text(
                        "UPDATE scheduled_jobs SET status = 'running', attempts = 1, "
                        "started_at = now() - interval '2 hours' WHERE id = :id"
                    ),
                    {"id": job_id},
                )
            assert scheduled_jobs.run_due_jobs(tenant_id=UUID(tenant_id)) == 1
            assert _row(job_id)["status"] == "done"
        finally:
            _cleanup(tenant_id)
