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

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import founder_alerts, usage
from docflow_core.constants import (
    ABUSE_CEILING_MULTIPLIER,
    DAILY_AI_COST_CEILING_USD,
    DAILY_TOKEN_CEILING,
)

HoldReason = Literal["abuse_ceiling", "cost_breaker"]


# ── The lifecycle gate (Section 7.14; review finding H10) ───────────────────
#
# 7.14, on entering `suspended`: "the upload endpoint and API return a clear
# error, not a 404 or a silent failure." Until Stage 2b only email intake
# refused, and it refused for a different reason -- it reads
# `intake_address_active`, which the suspend transition clears
# (`lifecycle.py:312`) and which is *also* false before go-live. So a cancelled
# customer could keep uploading through the app and keep spending the founder's
# model budget, which is both the 7.14 breach and a cost leak.
#
# **One source of truth for the lifecycle question, not one gate for both
# channels.** The two channels genuinely refuse different things and collapsing
# them would be wrong:
#
#   * email also refuses a *not yet live* address (INT-005), because mail to an
#     address that isn't reading yet has to be answered;
#   * upload must NOT refuse before go-live -- the Console's own test batch and
#     an invited user trying the app both arrive that way, and nothing asks for
#     those to be blocked.
#
# What both must agree on is "does this tenant's lifecycle state stop new work",
# and that is `blocks_new_intake` below: one predicate, used by both, so the
# answer cannot drift between them.

# NOT the same thing as `lifecycle.REACTIVATABLE`, which holds the same two
# states today and answers a different question: which states a tenant may be
# reactivated *from*. They are free to diverge -- a future state could block new
# intake without being reactivatable, or the reverse -- so each is named where it
# is meant and neither is defined in terms of the other. Do not collapse them.
LIFECYCLE_BLOCKED_STATUSES = ("suspended", "pending_deletion")


def blocks_new_intake(tenant_status: str | None) -> bool:
    """
    Whether this lifecycle state stops new documents arriving (7.14).

    `suspended` and `pending_deletion` only. Deliberately NOT `cancelling`: 7.14
    is explicit that everything keeps working until the effective date, so a
    tenant who has given notice keeps processing orders to the last day. And
    deliberately not a read/export check of any kind -- a suspended tenant keeps
    full read and export access, which 7.14 calls the wrong incentive to build
    into a company whose pitch is trust.
    """
    return tenant_status in LIFECYCLE_BLOCKED_STATUSES


def lifecycle_block(session: Session, tenant_id: UUID) -> str | None:
    """
    The catalog code to refuse an upload with, or None to carry on.

    INT-010 rather than email's INT-006: INT-006 is written for a buyer whose
    mail bounced ("this email was logged", "contact this company directly"), and
    the reader here is the tenant's own user, who *is* the company (D-172).
    """
    status = session.execute(
        text("SELECT status FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
    ).scalar_one_or_none()
    return "INT-010" if blocks_new_intake(status) else None


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
