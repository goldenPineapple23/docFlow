"""
Allowance thresholds: the 80% and 100% notices (CLAUDE.md Section 7.16.1;
D-126).

Allowances are SOFT. Nothing here ever blocks, delays or degrades a document:
it only tells the customer (one email per threshold per month to the account's
admin, and a banner on their screens once the plan is nearly spent) and tells
the founder (an `allowance_reached` alert at 100%, a sales signal).

"One email per threshold per month" is enforced by a unique row in
`allowance_notices`, so a threshold crossed twice, or checked by two requests at
once, still sends once. The banner is not stored: it is derived from the
month's numbers each time it is shown, so it disappears when the month rolls.

All wording comes from the error catalog (LIM-001..004); this module only picks
which entry applies and supplies the numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import email_outbox, founder_alerts, usage
from docflow_core.constants import ALLOWANCE_BANNER_THRESHOLD, ALLOWANCE_THRESHOLDS
from docflow_core.errors import ErrorCatalogEntry, render_error

# The catalog entry for each threshold, with and without a higher tier to
# point at (the top tier has nothing to upgrade to).
_CODES = {80: ("LIM-001", "LIM-003"), 100: ("LIM-002", "LIM-004")}


@dataclass(frozen=True)
class Banner:
    threshold_pct: int
    used: int
    allowance: int
    entry: ErrorCatalogEntry


def _pct(threshold: float) -> int:
    return round(threshold * 100)


def _entry(a: usage.Allowance, threshold_pct: int) -> ErrorCatalogEntry:
    with_tier, top_tier = _CODES[threshold_pct]
    params = {"used": f"{a.used:,}", "allowance": f"{a.allowance:,}", "tier": a.tier_name}
    if a.next_tier_name is None or a.next_tier_allowance is None:
        return render_error(top_tier, **params)
    return render_error(
        with_tier, **params, next_tier=a.next_tier_name, next_allowance=f"{a.next_tier_allowance:,}"
    )


def current_banner(session: Session, tenant_id: UUID) -> Banner | None:
    """The banner the tenant should see now, or None.

    Shown only from `ALLOWANCE_BANNER_THRESHOLD` up (D-129) -- the admin is
    emailed at the 80% and 100% thresholds and has the running numbers on the
    dashboard; the people working the queue are interrupted only when the plan
    is nearly spent. Above that point it is still the crossed threshold's
    wording, so it becomes the 100% entry once the allowance is used up.
    """
    a = usage.allowance_for(session, tenant_id)
    if not a.allowance:
        return None
    if a.used < ALLOWANCE_BANNER_THRESHOLD * a.allowance:
        return None
    crossed = [_pct(t) for t in ALLOWANCE_THRESHOLDS if a.used >= t * a.allowance]
    if not crossed:
        return None
    pct = max(crossed)
    return Banner(threshold_pct=pct, used=a.used, allowance=a.allowance, entry=_entry(a, pct))


def record_thresholds(session: Session, tenant_id: UUID) -> list[int]:
    """Call after a document is counted. For each newly crossed threshold this
    month: write the notice row, email the tenant's owner(s), and at 100% raise
    the founder alert. Returns the thresholds that fired now (usually empty)."""
    a = usage.allowance_for(session, tenant_id)
    if not a.allowance:
        return []
    fired: list[int] = []
    for threshold in ALLOWANCE_THRESHOLDS:
        pct = _pct(threshold)
        if a.used < threshold * a.allowance:
            continue
        inserted = session.execute(
            text(
                """
                INSERT INTO allowance_notices (tenant_id, month, threshold_pct, used, allowance)
                VALUES (:tenant_id, :month, :pct, :used, :allowance)
                ON CONFLICT (tenant_id, month, threshold_pct) DO NOTHING
                RETURNING id
                """
            ),
            {
                "tenant_id": str(tenant_id),
                "month": a.month,
                "pct": pct,
                "used": a.used,
                "allowance": a.allowance,
            },
        ).first()
        if inserted is None:
            continue  # already told them this month
        fired.append(pct)
        _email_owners(session, tenant_id, _entry(a, pct))
        if pct == 100:
            # A sales signal, so severity info: good news in the attention panel.
            founder_alerts.raise_alert(
                session,
                alert_type="allowance_reached",
                severity="info",
                tenant_id=tenant_id,
                payload={
                    "documents_this_month": a.used,
                    "allowance": a.allowance,
                    "tier": a.tier_name,
                    "month": a.month,
                },
                dedupe_key=f"allowance:{tenant_id}:{a.month}",
            )
    return fired


def _email_owners(session: Session, tenant_id: UUID, entry: ErrorCatalogEntry) -> None:
    owners = (
        session.execute(
            text(
                "SELECT email FROM users WHERE tenant_id = :t AND role = 'owner' "
                "AND deleted_at IS NULL AND email IS NOT NULL"
            ),
            {"t": str(tenant_id)},
        )
        .scalars()
        .all()
    )
    tenant_name = session.execute(
        text("SELECT name FROM tenants WHERE id = :t"), {"t": str(tenant_id)}
    ).scalar_one()
    for address in owners:
        email_outbox.enqueue(
            session,
            tenant_id=tenant_id,
            to_address=address,
            template="allowance_notice",
            params={
                "tenant_name": tenant_name,
                "title": entry.title,
                "message": entry.message,
                "action": entry.action,
            },
            related_type="allowance_notice",
        )
