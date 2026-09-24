"""
The "needs review" digest email (slice 5.8c; DECISIONS.md D-131; migration
0022).

A 500-document backfill must not send 500 emails, so the email is a digest:

  * When an order reaches `needs_review`, the worker calls `note_needs_review`,
    which adds the order's id to the tenant's one pending `review_digest` job
    in `scheduled_jobs`, creating the job if there is none. The database's
    partial unique index makes "one pending digest per tenant" true even when
    two workers finish orders at the same moment.
  * The job's run time is fixed when it is created: `REVIEW_DIGEST_INTERVAL_MIN`
    after the first order that started it, and never sooner than that after the
    previous digest began. So a tenant gets at most one digest per interval.
  * It then sends one email to each active owner, admin and reviewer (not a
    viewer): how many of the orders it collected still need review, how many
    are waiting in all, and how long the oldest has waited. Counts only --
    never a PO number, a buyer or a value (Section 7.10).
  * No re-nagging. Only a newly arrived order creates a digest; orders that
    simply sit in the queue never produce another email. If every order the
    digest collected was reviewed before it fired, nothing is sent.

Test-batch orders are the founder's to review during onboarding and never
start a digest, and a tenant that is not live, or has been suspended, gets
none.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import email_outbox
from docflow_core.config import get_settings
from docflow_core.constants import REVIEW_DIGEST_INTERVAL_MIN

if TYPE_CHECKING:
    from docflow_core.scheduled_jobs import Job

# The roles that work the queue (founder decision, 2026-09-23).
RECIPIENT_ROLES = ("owner", "admin", "reviewer")
# Lifecycle states in which the account is still being worked. A suspended
# tenant keeps read access (7.14) but is not asked to review anything.
_SENDING_STATUSES = ("active", "cancelling")


def note_needs_review(session: Session, tenant_id: UUID, document_id: UUID) -> bool:
    """Add a just-arrived order to the tenant's pending digest. Returns False
    for a test-batch order, which never starts one."""
    is_test_batch = session.execute(
        text("SELECT is_test_batch FROM documents WHERE id = :d"), {"d": str(document_id)}
    ).scalar_one()
    if is_test_batch:
        return False
    session.execute(
        text(
            """
            INSERT INTO scheduled_jobs (tenant_id, job_type, run_at, payload)
            VALUES (
                :t, 'review_digest',
                GREATEST(
                    now() + make_interval(mins => :interval),
                    COALESCE(
                        (SELECT max(started_at) FROM scheduled_jobs
                         WHERE tenant_id = :t AND job_type = 'review_digest'
                           AND started_at IS NOT NULL)
                        + make_interval(mins => :interval),
                        now()
                    )
                ),
                jsonb_build_object('document_ids', jsonb_build_array(CAST(:d AS text)))
            )
            ON CONFLICT (tenant_id) WHERE job_type = 'review_digest' AND status = 'pending'
            DO UPDATE SET payload = jsonb_set(
                scheduled_jobs.payload,
                '{document_ids}',
                COALESCE(scheduled_jobs.payload -> 'document_ids', '[]'::jsonb)
                    || jsonb_build_array(CAST(:d AS text))
            )
            """
        ),
        {"t": str(tenant_id), "d": str(document_id), "interval": REVIEW_DIGEST_INTERVAL_MIN},
    )
    return True


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def waited_phrase(since: datetime, now: datetime) -> str:
    """'about 3 hours', 'about 2 days' -- relative, so no timezone is needed."""
    minutes = max(int((now - since).total_seconds() // 60), 0)
    if minutes < 60:
        return "less than an hour"
    hours = minutes // 60
    if hours < 48:
        return f"about {_plural(hours, 'hour', 'hours')}"
    return f"about {_plural(hours // 24, 'day', 'days')}"


def send_digest(session: Session, job: Job) -> None:
    """The `review_digest` job handler. Writes what it decided onto its own
    job row (counts only), so the Console can see why an email did or did not
    go."""
    assert job.tenant_id is not None
    tenant_id = str(job.tenant_id)
    outcome: dict[str, object] = {"sent_to": 0}

    tenant = session.execute(
        text("SELECT name, status, onboarding_status FROM tenants WHERE id = :t"), {"t": tenant_id}
    ).mappings().first()
    ids = [str(i) for i in job.payload.get("document_ids", [])]
    if tenant is None or tenant["status"] not in _SENDING_STATUSES or tenant["onboarding_status"] != "live":
        outcome["skipped"] = "tenant_not_live"
    else:
        counts = session.execute(
            text(
                """
                SELECT
                    count(*) FILTER (WHERE d.id = ANY(string_to_array(:ids, ',')::uuid[])) AS new_waiting,
                    count(*) AS waiting,
                    min(d.created_at) AS oldest,
                    now() AS now
                FROM documents d
                WHERE d.status = 'needs_review' AND NOT d.is_test_batch AND d.deleted_at IS NULL
                """
            ),
            {"ids": ",".join(ids)},
        ).mappings().one()
        outcome.update(new_waiting=counts["new_waiting"], waiting=counts["waiting"])
        if not counts["new_waiting"]:
            outcome["skipped"] = "already_reviewed"
        else:
            recipients = (
                session.execute(
                    text(
                        "SELECT email FROM users WHERE tenant_id = :t AND is_active AND deleted_at IS NULL "
                        "AND email IS NOT NULL AND role = ANY(string_to_array(:roles, ',')) "
                        "ORDER BY created_at"
                    ),
                    {"t": tenant_id, "roles": ",".join(RECIPIENT_ROLES)},
                )
                .scalars()
                .all()
            )
            params = digest_params(
                tenant_name=tenant["name"],
                new_waiting=counts["new_waiting"],
                waiting=counts["waiting"],
                oldest=counts["oldest"],
                now=counts["now"],
            )
            for address in recipients:
                email_outbox.enqueue(
                    session,
                    tenant_id=job.tenant_id,
                    to_address=address,
                    template="review_digest",
                    params=params,
                    related_type="scheduled_job",
                    related_id=job.id,
                )
            outcome["sent_to"] = len(recipients)

    session.execute(
        text(
            "UPDATE scheduled_jobs SET payload = payload || CAST(:outcome AS jsonb) WHERE id = :id"
        ),
        {"id": str(job.id), "outcome": json.dumps({"outcome": outcome})},
    )


def digest_params(
    *, tenant_name: str, new_waiting: int, waiting: int, oldest: datetime, now: datetime
) -> dict[str, str]:
    """The template's fields. Separate so the wording can be tested without a
    database."""
    arrived = (
        "1 new purchase order is" if new_waiting == 1 else f"{new_waiting:,} new purchase orders are"
    )
    in_all = (
        "It is the only one waiting."
        if waiting == 1
        else f"{_plural(waiting, 'purchase order is', 'purchase orders are')} waiting in all; "
        f"the oldest has waited {waited_phrase(oldest, now)}."
    )
    return {
        "tenant_name": tenant_name,
        "count": f"{new_waiting:,}",
        "arrived": arrived,
        "in_all": in_all,
        "review_link": f"{get_settings().app_base_url.rstrip('/')}/review?status=needs_review",
        "interval_minutes": str(REVIEW_DIGEST_INTERVAL_MIN),
    }
