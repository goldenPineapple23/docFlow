"""
The customer's own dashboard (`docflow-mvp-features.docx`, "Simple dashboard --
documents by status, recent activity"; CLAUDE.md Section 7.16.4 puts the held
count here too). Slice 5.8a, D-128.

This is the tenant's view of their own account, not the founder's cross-tenant
dashboard (7.15.3, `metrics.py`), and it is deliberately different in kind:

  * it reads the tenant's own rows through the tenant-scoped layer, never
    `admin_data_access`;
  * it counts live rather than from the nightly rollup, because one tenant's
    own documents are a small, indexed set (`idx_documents_tenant_status`) and a
    customer should not see yesterday's numbers on their own screen;
  * it carries no money, no cost and no cross-tenant comparison.

Analytics proper -- time-saved reporting and touchless-rate trends -- is on the
features document's "Future releases" list and is not built here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

# The statuses a customer sees on their own dashboard, in the order they read:
# what needs me, what is done, what went wrong. `staged` and `quarantined` are
# deliberately absent -- staged is the founder's setup batch, and held documents
# have their own count (7.16.4) with their own plain-English reason.
QUEUE_STATUSES = ("needs_review", "approved", "exported", "rejected", "failed")


@dataclass(frozen=True)
class Activity:
    """One thing that happened, for the recent-activity list. Carries no value
    from any document (Section 7.10) -- what changed, never what it changed to."""

    at: datetime
    kind: str  # approved | rejected | edited | reopened | exported | released
    document_id: UUID | None
    document_name: str | None
    po_number: str | None
    by: str | None  # the person's email, or None
    by_docflow_support: bool
    detail: str | None  # e.g. "3 fields", "CSV" -- never an extracted value


def documents_by_status(session: Session, tenant_id: UUID) -> dict[str, int]:
    """Live counts for this tenant's own queue, excluding the founder's setup
    batch and anything deleted."""
    rows = session.execute(
        text(
            """
            SELECT status, count(*) AS n
            FROM documents
            WHERE tenant_id = :t AND deleted_at IS NULL AND NOT is_test_batch
              AND status = ANY(CAST(string_to_array(:statuses, ',') AS text[]))
            GROUP BY status
            """
        ),
        {"t": str(tenant_id), "statuses": ",".join(QUEUE_STATUSES)},
    ).mappings().all()
    counts = {status: 0 for status in QUEUE_STATUSES}
    for row in rows:
        counts[row["status"]] = int(row["n"])
    return counts


def waiting(session: Session, tenant_id: UUID) -> dict[str, Any]:
    """How long the oldest unreviewed order has been waiting, and how many
    arrived today -- the two numbers that tell a customer whether they are
    keeping up."""
    row = session.execute(
        text(
            """
            SELECT min(d.created_at) FILTER (WHERE d.status = 'needs_review') AS oldest_needs_review,
                   count(*) FILTER (
                       WHERE d.created_at >= date_trunc(
                           'day', now() AT TIME ZONE coalesce(nullif(t.timezone, ''), 'UTC')
                       ) AT TIME ZONE coalesce(nullif(t.timezone, ''), 'UTC')
                   ) AS received_today
            FROM documents d JOIN tenants t ON t.id = d.tenant_id
            WHERE d.tenant_id = :t AND d.deleted_at IS NULL AND NOT d.is_test_batch
            """
        ),
        {"t": str(tenant_id)},
    ).mappings().one()
    return {
        "oldest_needs_review_at": row["oldest_needs_review"],
        "received_today": int(row["received_today"]),
    }


def this_month(session: Session, tenant_id: UUID) -> dict[str, Any]:
    """This calendar month in the tenant's timezone: what arrived, what was
    approved and exported, and the median hours from arrival to approval.

    `arrived` is everything that came in, including documents that are held or
    that could not be read. `counted` is the subset that counts toward the plan
    (7.16.1, `usage.month_used`) -- the same number the allowance shows. They
    are reported separately and labelled separately on the screen, because one
    word covering both would have to be wrong for one of them: a customer whose
    buyer sent nine orders should see nine, and should also see why seven of
    them are what their plan is measured against.

    The median is over this month only -- a handful of rows, not a scan.
    """
    row = session.execute(
        text(
            """
            WITH month AS (
                SELECT d.*
                FROM documents d JOIN tenants t ON t.id = d.tenant_id
                WHERE d.tenant_id = :t AND d.deleted_at IS NULL AND NOT d.is_test_batch
                  AND (d.created_at AT TIME ZONE coalesce(nullif(t.timezone, ''), 'UTC'))
                      >= date_trunc('month', now() AT TIME ZONE coalesce(nullif(t.timezone, ''), 'UTC'))
            )
            SELECT count(*) AS arrived,
                   count(*) FILTER (WHERE approved_at IS NOT NULL) AS approved,
                   count(*) FILTER (WHERE status = 'exported') AS exported,
                   percentile_cont(0.5) WITHIN GROUP (
                       ORDER BY extract(epoch FROM (approved_at - created_at)) / 3600
                   ) FILTER (WHERE approved_at IS NOT NULL) AS median_hours_to_approval
            FROM month
            """
        ),
        {"t": str(tenant_id)},
    ).mappings().one()
    median = row["median_hours_to_approval"]
    from docflow_core import usage

    return {
        "arrived": int(row["arrived"]),
        "counted": usage.month_used(session, tenant_id),
        "approved": int(row["approved"]),
        "exported": int(row["exported"]),
        "median_hours_to_approval": round(float(median), 1) if median is not None else None,
    }


# What a person can filter the Activity page by. The first four are the
# `review_actions.action` values the schema allows; the last two come from the
# other two sources in the union below.
ACTIVITY_KINDS = ("edited", "approved", "rejected", "reopened", "exported", "released")

# Everything that happened in an account, from the rows the product already
# writes -- `review_actions`, finished `exports`, and released held documents --
# so there is no second record of the truth to keep in step. The dashboard's
# ten-item list and the Activity page (5.8b) both read this, so the two cannot
# disagree; only the filter, the limit and the offset differ.
_EVENTS_CTE = """
            WITH events AS (
                SELECT r.created_at AS at, r.sequence AS seq, r.action AS kind, r.document_id,
                       r.user_id, r.acting_as_tenant_id IS NOT NULL AS support,
                       CASE WHEN r.action = 'edited'
                            THEN jsonb_array_length(r.changes) || ' field'
                                 || CASE WHEN jsonb_array_length(r.changes) = 1 THEN '' ELSE 's' END
                       END AS detail
                FROM review_actions r
                WHERE r.tenant_id = :t
                UNION ALL
                SELECT e.generated_at, NULL, 'exported', e.document_id, e.generated_by,
                       e.acting_as_tenant_id IS NOT NULL, upper(e.format)
                FROM exports e
                WHERE e.tenant_id = :t AND e.status = 'ready'
                  AND e.generated_at IS NOT NULL AND e.deleted_at IS NULL
                UNION ALL
                SELECT d.released_at, NULL, 'released', d.id, d.released_by_user_id,
                       d.released_acting_as_tenant_id IS NOT NULL, NULL
                FROM documents d
                WHERE d.tenant_id = :t AND d.released_at IS NOT NULL
            )
"""


def recent_activity(session: Session, tenant_id: UUID, *, limit: int = 10) -> list[Activity]:
    """The last things that happened in this account, newest first -- the
    dashboard's short list. `activity_page` is the same query, paged."""
    return _select_activity(session, tenant_id, kinds=(), limit=limit, offset=0)


def activity_page(
    session: Session,
    tenant_id: UUID,
    *,
    kinds: tuple[str, ...] = (),
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """One page of the account's activity, newest first, optionally narrowed to
    certain kinds of event. `total` is the count matching the same filter, so a
    page can say which slice of what it is showing.

    Values are never included: an edit says how many fields changed, not what
    they became (Section 7.10).
    """
    kinds = tuple(k for k in kinds if k in ACTIVITY_KINDS)
    return {
        "items": _select_activity(session, tenant_id, kinds=kinds, limit=limit, offset=offset),
        "total": _count_activity(session, tenant_id, kinds=kinds),
        "limit": limit,
        "offset": offset,
    }


# The kind filter, written once: an empty list means every kind, so the page
# with no filter and the dashboard's list run the same query.
_KIND_FILTER = (
    "WHERE (:kinds = '' OR ev.kind = ANY(CAST(string_to_array(:kinds, ',') AS text[])))"
)


def _count_activity(session: Session, tenant_id: UUID, *, kinds: tuple[str, ...]) -> int:
    return int(
        session.execute(
            text(_EVENTS_CTE + "SELECT count(*) FROM events ev " + _KIND_FILTER),
            {"t": str(tenant_id), "kinds": ",".join(kinds)},
        ).scalar_one()
    )


def _select_activity(
    session: Session, tenant_id: UUID, *, kinds: tuple[str, ...], limit: int, offset: int
) -> list[Activity]:
    rows = session.execute(
        text(
            _EVENTS_CTE
            + """
            SELECT ev.at, ev.kind, ev.document_id, ev.support, ev.detail,
                   u.email AS by_email,
                   d.original_filename, h.po_number
            FROM events ev
            LEFT JOIN users u ON u.id = ev.user_id
            LEFT JOIN documents d ON d.id = ev.document_id
            LEFT JOIN document_headers h ON h.document_id = ev.document_id
            """
            + _KIND_FILTER
            + """
            -- `now()` is frozen for a transaction, so two review actions written
            -- together share a timestamp; `sequence` is what "these happened in
            -- this order" actually means (D-084). Rows from the other sources
            -- have no sequence and fall back to time alone.
            ORDER BY ev.at DESC, ev.seq DESC NULLS LAST
            LIMIT :limit OFFSET :offset
            """
        ),
        {"t": str(tenant_id), "kinds": ",".join(kinds), "limit": limit, "offset": offset},
    ).mappings().all()
    return [
        Activity(
            at=row["at"],
            kind=row["kind"],
            document_id=UUID(str(row["document_id"])) if row["document_id"] else None,
            document_name=row["original_filename"],
            po_number=row["po_number"],
            by=None if row["support"] else row["by_email"],
            by_docflow_support=bool(row["support"]),
            detail=row["detail"],
        )
        for row in rows
    ]
