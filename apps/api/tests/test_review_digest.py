# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
The "needs review" digest (slice 5.8c, D-131; migration 0022) against the real
database and RLS: a burst of orders is one email per person, only the people who
work the queue get it, nothing is sent for orders already reviewed or for a
tenant that is not live, orders sitting in the queue never re-nag, a test batch
never starts one, the next digest waits out the interval, and one tenant's
orders never appear in another's count.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import UUID

import pytest
from docflow_core import review_digest, scheduled_jobs
from docflow_core.constants import REVIEW_DIGEST_INTERVAL_MIN
from docflow_core.db import platform_session, tenant_session
from sqlalchemy import text

from tests.conftest import requires_database
from tests.test_console_api import _environment  # noqa: F401
from tests.test_quarantine_api import _Tenant


def _schema_available() -> bool:
    try:
        with platform_session() as session:
            return bool(
                session.execute(
                    text(
                        "SELECT 1 FROM pg_indexes WHERE indexname = 'uq_scheduled_jobs_pending_review_digest'"
                    )
                ).first()
            )
    except Exception:
        return False


requires_digest_schema = pytest.mark.skipif(
    not _schema_available(),
    reason="supabase/migrations/0022_review_digest.sql has not been applied yet -- see D-131.",
)


class _LiveTenant(_Tenant):
    """A live tenant whose queue is worked by an owner, an admin, a reviewer
    and a viewer."""

    def __enter__(self):
        super().__enter__()
        try:
            with platform_session() as session:
                session.execute(
                    text(
                        "UPDATE tenants SET onboarding_status = 'live', went_live_at = now(), "
                        "setup_fee_billing = 'invoiced_manually' WHERE id = :t"
                    ),
                    {"t": str(self.tenant_id)},
                )
            for role in ("admin", "reviewer", "viewer"):
                self.add_user(role)
        except BaseException:
            # A failure here would skip __exit__ and strand the tenant in staging.
            self.__exit__(None, None, None)
            raise
        return self

    def arrive(self, n: int, *, test_batch: bool = False) -> list[UUID]:
        """`n` orders reaching needs_review, noted exactly as the worker does."""
        ids = self.seed(n, status="needs_review", test_batch=test_batch)
        for document_id in ids:
            with tenant_session(self.tenant_id) as session:
                review_digest.note_needs_review(session, self.tenant_id, document_id)
        return ids

    def digests(self) -> list[dict]:
        return self.rows(
            "SELECT id, status, run_at, started_at, payload FROM scheduled_jobs "
            "WHERE tenant_id = :t AND job_type = 'review_digest' ORDER BY created_at"
        )

    def make_due(self) -> None:
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE scheduled_jobs SET run_at = now() - interval '1 second' "
                    "WHERE tenant_id = :t AND job_type = 'review_digest' AND status = 'pending'"
                ),
                {"t": str(self.tenant_id)},
            )

    def sweep(self) -> int:
        return scheduled_jobs.run_due_jobs(tenant_id=self.tenant_id)

    def email(self, role: str) -> str:
        return self.rows("SELECT email FROM users WHERE id = :u", u=str(self.users[role][0]))[0]["email"]


@requires_database
@requires_digest_schema
def test_a_burst_of_orders_is_one_digest_and_one_email_per_person_who_reviews():
    with _LiveTenant("Acme Test Digest Burst") as t:
        ids = t.arrive(40)

        [job] = t.digests()
        assert job["status"] == "pending"
        assert len(job["payload"]["document_ids"]) == 40
        # Fires an interval after the first order, not before.
        assert t.sweep() == 0

        t.make_due()
        assert t.sweep() == 1

        mail = t.outbox("review_digest")
        assert sorted(m["to_address"] for m in mail) == sorted(
            t.email(role) for role in ("owner", "admin", "reviewer")
        )
        body = mail[0]["body_text"]
        assert "40 new purchase orders are ready for review" in body
        # Counts only: no document id, and no filename, reaches the email (7.10).
        assert not any(str(i) in body for i in ids)
        assert "seed-" not in body
        assert t.digests()[0]["payload"]["outcome"] == {"sent_to": 3, "new_waiting": 40, "waiting": 40}


@requires_database
@requires_digest_schema
def test_orders_sitting_in_the_queue_never_send_a_reminder():
    with _LiveTenant("Acme Test Digest No Nag") as t:
        t.arrive(2)
        t.make_due()
        assert t.sweep() == 1

        # Nothing new arrives; the two orders still wait. No further digest.
        assert [d["status"] for d in t.digests()] == ["done"]
        t.make_due()
        assert t.sweep() == 0
        assert len(t.outbox("review_digest")) == 3


@requires_database
@requires_digest_schema
def test_the_next_digest_waits_out_the_interval_after_the_last_one():
    with _LiveTenant("Acme Test Digest Interval") as t:
        t.arrive(1)
        t.make_due()
        assert t.sweep() == 1

        t.arrive(1)
        first, second = t.digests()
        assert second["status"] == "pending"
        gap = (second["run_at"] - first["started_at"]).total_seconds()
        assert gap >= REVIEW_DIGEST_INTERVAL_MIN * 60 - 1

        t.make_due()
        assert t.sweep() == 1
        second_mail = [m for m in t.outbox("review_digest") if "1 new purchase order is" in m["body_text"]]
        # Both digests announce one new order; the second counts both waiting.
        assert any("2 purchase orders are waiting in all" in m["body_text"] for m in second_mail)


@requires_database
@requires_digest_schema
def test_nothing_is_sent_when_the_orders_were_reviewed_before_the_digest_fired():
    with _LiveTenant("Acme Test Digest Too Late") as t:
        ids = t.arrive(3)
        # Reviewed either way counts; rejecting avoids building an approval
        # snapshot the database rightly insists on (7.3).
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE documents SET status = 'rejected' "
                    "WHERE id = ANY(string_to_array(:ids, ',')::uuid[])"
                ),
                {"ids": ",".join(str(i) for i in ids)},
            )
        t.make_due()
        assert t.sweep() == 1
        assert t.outbox("review_digest") == []
        assert t.digests()[0]["payload"]["outcome"]["skipped"] == "already_reviewed"


@requires_database
@requires_digest_schema
def test_a_test_batch_never_starts_a_digest():
    with _LiveTenant("Acme Test Digest Test Batch") as t:
        t.arrive(5, test_batch=True)
        assert t.digests() == []


@requires_database
@requires_digest_schema
def test_a_tenant_that_is_not_live_or_is_suspended_is_not_emailed():
    with _LiveTenant("Acme Test Digest Not Live") as t:
        for onboarding, status in (("test_batch_complete", "active"), ("live", "suspended")):
            with platform_session() as session:
                session.execute(
                    text("UPDATE tenants SET onboarding_status = :o, status = :s WHERE id = :t"),
                    {"o": onboarding, "s": status, "t": str(t.tenant_id)},
                )
            t.arrive(1)
            t.make_due()
            assert t.sweep() == 1
            assert t.digests()[-1]["payload"]["outcome"]["skipped"] == "tenant_not_live"
        assert t.outbox("review_digest") == []


@requires_database
@requires_digest_schema
def test_one_tenant_never_counts_another_tenants_orders():
    with _LiveTenant("Acme Test Digest A") as a, _LiveTenant("Acme Test Digest B") as b:
        b.arrive(7)
        a.arrive(1)
        a.make_due()
        assert a.sweep() == 1
        assert a.digests()[0]["payload"]["outcome"]["waiting"] == 1
        assert all(m["to_address"] != b.email("owner") for m in a.outbox("review_digest"))
        # B's own digest is untouched by A's sweep.
        assert [d["status"] for d in b.digests()] == ["pending"]


@requires_database
@requires_digest_schema
def test_the_database_allows_only_one_pending_digest_per_tenant():
    with _LiveTenant("Acme Test Digest Unique") as t:
        t.arrive(1)
        with pytest.raises(Exception, match="uq_scheduled_jobs_pending_review_digest"):
            with platform_session() as session:
                session.execute(
                    text(
                        "INSERT INTO scheduled_jobs (tenant_id, job_type, run_at) "
                        "VALUES (:t, 'review_digest', now())"
                    ),
                    {"t": str(t.tenant_id)},
                )
