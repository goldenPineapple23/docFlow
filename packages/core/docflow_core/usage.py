"""
Metering: how much a tenant has used (CLAUDE.md Section 7.16.1, D-126).

One definition of "a document that counts", used by the allowance banner and
emails, the abuse ceiling, and (through a test that keeps them equal) the
Console tenant list:

  * the current calendar month, in the TENANT's timezone;
  * not a test-batch document (7.15.2 -- setup work is never metered);
  * status not `failed` or `quarantined` -- a held or failed document is not
    counted until it is released and processes;
  * not a linked duplicate -- "Duplicates linked to an existing document (7.8)
    are not counted twice";
  * not soft-deleted.

The daily AI spend is a separate question with its own definition: what the
model actually cost today, from `extraction_runs`, excluding the test batch.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

# The SQL fragment for "documents that count this month". Kept as one string so
# the Console's batch query and this module cannot drift apart silently; the
# test that compares them is the second guard.
COUNTED_DOCUMENTS_SQL = """
    NOT d.is_test_batch
    AND d.deleted_at IS NULL
    AND d.duplicate_of_document_id IS NULL
    AND d.status NOT IN ('failed', 'quarantined', 'staged')
    AND (d.created_at AT TIME ZONE coalesce(nullif(t.timezone, ''), 'UTC'))
        >= date_trunc('month', now() AT TIME ZONE coalesce(nullif(t.timezone, ''), 'UTC'))
"""


@dataclass(frozen=True)
class Allowance:
    """A tenant's month so far, against its tier."""

    used: int
    allowance: int | None  # None: no tier set yet, so nothing to measure against
    tier_name: str | None
    next_tier_name: str | None
    next_tier_allowance: int | None
    month: str  # 'YYYY-MM', tenant-local


def month_used(session: Session, tenant_id: UUID) -> int:
    """Documents that count against this month's allowance."""
    return int(
        session.execute(
            text(
                f"""
                SELECT count(*)
                FROM documents d JOIN tenants t ON t.id = d.tenant_id
                WHERE d.tenant_id = :tenant_id AND {COUNTED_DOCUMENTS_SQL}
                """
            ),
            {"tenant_id": str(tenant_id)},
        ).scalar_one()
    )


def allowance_for(session: Session, tenant_id: UUID) -> Allowance:
    """This month's usage, the tier's allowance, and the next tier up (for the
    'Growth includes 1,000 -- contact us to upgrade' wording)."""
    row = (
        session.execute(
            text(
                """
            SELECT tr.name AS tier_name, tr.document_allowance, tr.monthly_price,
                   to_char(now() AT TIME ZONE coalesce(nullif(t.timezone, ''), 'UTC'), 'YYYY-MM') AS month
            FROM tenants t LEFT JOIN tiers tr ON tr.id = t.tier_id
            WHERE t.id = :tenant_id
            """
            ),
            {"tenant_id": str(tenant_id)},
        )
        .mappings()
        .first()
    )
    if row is None:
        return Allowance(0, None, None, None, None, "")
    nxt: Any = None
    if row["tier_name"] is not None:
        nxt = (
            session.execute(
                text(
                    """
                SELECT name, document_allowance FROM tiers
                WHERE is_current AND monthly_price > :price
                ORDER BY monthly_price LIMIT 1
                """
                ),
                {"price": row["monthly_price"]},
            )
            .mappings()
            .first()
        )
    return Allowance(
        used=month_used(session, tenant_id),
        allowance=row["document_allowance"],
        tier_name=row["tier_name"],
        next_tier_name=nxt["name"] if nxt else None,
        next_tier_allowance=nxt["document_allowance"] if nxt else None,
        month=row["month"],
    )


def daily_ai_spend(session: Session, tenant_id: UUID) -> tuple[Decimal, int]:
    """(estimated dollars, tokens) the model has used for this tenant today
    (UTC day), across every extraction run, excluding test-batch documents.
    Example-prompt overhead is included because it is inside input_tokens."""
    row = (
        session.execute(
            text(
                """
            SELECT coalesce(sum(r.est_cost_usd), 0) AS cost,
                   coalesce(sum(coalesce(r.input_tokens, 0) + coalesce(r.output_tokens, 0)), 0) AS tokens
            FROM extraction_runs r JOIN documents d ON d.id = r.document_id
            WHERE r.tenant_id = :tenant_id
              AND NOT d.is_test_batch
              AND (r.created_at AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date
            """
            ),
            {"tenant_id": str(tenant_id)},
        )
        .mappings()
        .one()
    )
    return Decimal(row["cost"]), int(row["tokens"])
