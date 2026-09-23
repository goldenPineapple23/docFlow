"""
The hard ceilings that exist only for abuse (CLAUDE.md Section 7.16.2, 7.9;
D-126).

A purchase order DocFlow refuses is a lost order; a document it processes
unnecessarily costs cents. So a ceiling never drops anything: it makes new
documents arrive as `quarantined` -- stored, never sent to the model -- and
tells the founder.

`hold_reason` is called by every way a document can enter (email intake and
the upload endpoint), so there is one rule, not one per channel. It is
STATELESS: it is recomputed at each arrival from the month's count and today's
AI spend, so there is no "paused" flag that can get stuck on. The founder
alert is deduplicated (one per tenant, reason and day), so a flood raises one
alert, not a thousand.

The setup test batch never goes through here (Section 7.15.2: it is excluded
from metering, and a founder's own onboarding must not trip a customer-abuse
ceiling).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from docflow_core import founder_alerts, usage
from docflow_core.constants import (
    ABUSE_CEILING_MULTIPLIER,
    DAILY_AI_COST_CEILING_USD,
    DAILY_TOKEN_CEILING,
)

HoldReason = Literal["abuse_ceiling", "cost_breaker"]


def hold_reason(session: Session, tenant_id: UUID) -> HoldReason | None:
    """The reason new documents for this tenant must be held right now, or
    None. Raises the founder alert when it returns a reason."""
    allowance = usage.allowance_for(session, tenant_id)
    if allowance.allowance is not None:
        ceiling = allowance.allowance * ABUSE_CEILING_MULTIPLIER
        if allowance.used >= ceiling:
            _alert(
                session,
                tenant_id,
                "abuse_ceiling_tripped",
                {"documents_this_month": allowance.used, "ceiling": ceiling, "month": allowance.month},
                f"abuse:{tenant_id}:{allowance.month}",
            )
            return "abuse_ceiling"

    cost, tokens = usage.daily_ai_spend(session, tenant_id)
    if cost >= DAILY_AI_COST_CEILING_USD or tokens >= DAILY_TOKEN_CEILING:
        _alert(
            session,
            tenant_id,
            "cost_breaker_tripped",
            {
                "est_cost_usd_today": str(cost),
                "cost_ceiling_usd": str(DAILY_AI_COST_CEILING_USD),
                "tokens_today": tokens,
                "token_ceiling": DAILY_TOKEN_CEILING,
            },
            f"cost:{tenant_id}:{_utc_day(session)}",
        )
        return "cost_breaker"
    return None


def _utc_day(session: Session) -> str:
    from sqlalchemy import text

    return str(session.execute(text("SELECT (now() AT TIME ZONE 'UTC')::date")).scalar_one())


def _alert(session: Session, tenant_id: UUID, alert_type: str, payload: dict, dedupe_key: str) -> None:
    # Ids, counts and codes only (Section 7.10) -- the payload is emailed.
    founder_alerts.raise_alert(
        session,
        alert_type=alert_type,
        severity="high",
        tenant_id=tenant_id,
        payload=payload,
        dedupe_key=dedupe_key,
    )
