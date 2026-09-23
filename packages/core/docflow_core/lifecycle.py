"""
Tenant lifecycle (CLAUDE.md Section 7.14 / 7.15.4): cancel, the scheduled
move into suspended, reactivate. Mirrors docflow_core.onboarding's shape --
pure functions that take an already-open `Session` and never open their own
transaction, so the Console's /admin routes and the worker's recurring sweep
can each wrap them in the session that fits their own context.

D-123 -- Section 7.14's state diagram (active -> cancelling -> suspended ->
pending_deletion -> deleted) never states what triggers suspended ->
pending_deletion; the founder's decision (2026-09-22) was that they happen
in the same tick. `suspend_tenant` below performs both: it sets
`cancellation`-derived fields, blocks intake, stops billing (the caller
cancels the Stripe subscription -- see below), and immediately starts the
deletion clock. A tenant is never observably "suspended" without its
deletion date already set; `tenants.status` lands directly on
'pending_deletion'. Both transitions are still logged as distinct
`tenant_lifecycle_events` rows, so the history reads exactly like the state
diagram even though the database passes through the first state instantly.

Stripe is intentionally kept out of this module, exactly like
`onboarding.complete_go_live` keeps it out: an external call and a database
transaction can't commit atomically, so the caller (the /admin route for
cancel/reactivate, the worker task for suspend) makes the Stripe call
itself, between a "plan" read and a "complete" write, both of which run
inside a session this module is handed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import email_outbox, founder_alerts
from docflow_core.constants import (
    CURE_PERIOD_DAYS,
    EXPORT_WINDOW_DAYS,
    REMINDER_DAYS,
    constants_in_effect,
)

REASONS = ("customer_requested", "non_payment", "for_cause")
# A tenant may only be cancelled from 'active'; may only be reactivated from
# one of these.
REACTIVATABLE = ("suspended", "pending_deletion")


class LifecycleError(Exception):
    """A refusal with an error-catalog code (LIFE-0xx)."""

    def __init__(self, code: str, detail: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


def _lifecycle_event(
    session: Session,
    tenant_id: UUID,
    event_type: str,
    *,
    actor_user_id: UUID | None,
    payload: dict[str, Any] | None = None,
    constants: dict[str, Any] | None = None,
) -> None:
    # clock_timestamp(), not now(): claim_for_suspend writes two of these in
    # one transaction, and now() is frozen for the whole transaction in
    # Postgres -- both rows would get an identical created_at, making
    # "ORDER BY created_at" (which the audit trail depends on) a tie.
    session.execute(
        text(
            """
            INSERT INTO tenant_lifecycle_events
                (id, tenant_id, event_type, actor_user_id, constants_in_effect, payload, created_at)
            VALUES (:id, :tenant_id, :event_type, :actor, CAST(:constants AS jsonb),
                    CAST(:payload AS jsonb), clock_timestamp())
            """
        ),
        {
            "id": str(uuid4()),
            "tenant_id": str(tenant_id),
            "event_type": event_type,
            "actor": str(actor_user_id) if actor_user_id else None,
            "constants": json.dumps(constants or {}),
            "payload": json.dumps(payload or {}, default=str),
        },
    )


def _owner_email(session: Session, tenant_id: UUID) -> str | None:
    row = session.execute(
        text(
            "SELECT email FROM users WHERE tenant_id = :id AND role = 'owner' AND deleted_at IS NULL "
            "ORDER BY created_at LIMIT 1"
        ),
        {"id": str(tenant_id)},
    ).first()
    return row[0] if row else None


def status(session: Session, tenant_id: UUID) -> dict[str, Any] | None:
    """The Lifecycle tab's summary: current state plus everything the cancel
    form and the billing badge need. None if the tenant doesn't exist."""
    row = session.execute(
        text(
            "SELECT status, cancellation_reason, cancellation_effective_at, "
            "deletion_scheduled_at, stripe_subscription_status, stripe_current_period_end, "
            "first_past_due_at FROM tenants WHERE id = :id"
        ),
        {"id": str(tenant_id)},
    ).mappings().first()
    return dict(row) if row is not None else None


# ── Cancel ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EffectiveDatePlan:
    effective_at: datetime
    rule: str
    flagged: bool


def compute_effective_at(session: Session, tenant_id: UUID, reason: str) -> EffectiveDatePlan:
    """
    Section 7.15.4's three rules, in the Offboarding doc's terms (D-122's
    sibling decision, logged separately): current_period_end for a normal
    cancellation, the cure period from the first past-due event for
    non-payment, immediate for cause.
    """
    row = session.execute(
        text(
            "SELECT stripe_subscription_id, stripe_current_period_end, first_past_due_at "
            "FROM tenants WHERE id = :id"
        ),
        {"id": str(tenant_id)},
    ).mappings().first()
    if row is None:
        raise LifecycleError("CON-001")

    now = datetime.now(UTC)
    if reason == "customer_requested":
        if row["stripe_subscription_id"] is None:
            return EffectiveDatePlan(now, "no subscription yet: immediate", False)
        if row["stripe_current_period_end"] is not None:
            return EffectiveDatePlan(
                row["stripe_current_period_end"], "end of the current billing period", False
            )
        return EffectiveDatePlan(now, "no billing period on record: immediate", True)
    if reason == "non_payment":
        first_past_due = row["first_past_due_at"]
        base = first_past_due if first_past_due is not None else now
        rule = (
            "the first past-due notice (Net terms are the only grace period, D-125)"
            if CURE_PERIOD_DAYS == 0
            else f"{CURE_PERIOD_DAYS} days from the first past-due notice"
        )
        return EffectiveDatePlan(base + timedelta(days=CURE_PERIOD_DAYS), rule, first_past_due is None)
    if reason == "for_cause":
        return EffectiveDatePlan(now, "immediate (for cause)", False)
    raise LifecycleError("LIFE-001")


def cancel(
    session: Session,
    tenant_id: UUID,
    *,
    reason: str,
    note: str | None,
    actor_user_id: UUID,
    override_effective_at: datetime | None = None,
) -> dict[str, Any]:
    """
    Section 7.15.4 "Cancel tenant": computes the effective date, records it,
    and sends the confirmation email. Nothing about billing or intake
    changes yet -- that happens when the sweep suspends the tenant at the
    effective date (Section 7.14: "Everything keeps working until the
    effective date").
    """
    if reason not in REASONS:
        raise LifecycleError("LIFE-001")
    row = session.execute(
        text("SELECT name, status FROM tenants WHERE id = :id FOR UPDATE"),
        {"id": str(tenant_id)},
    ).mappings().first()
    if row is None:
        raise LifecycleError("CON-001")
    if row["status"] != "active":
        raise LifecycleError("LIFE-001", {"status": row["status"]})
    if reason == "for_cause" and (not note or len(note.strip()) < 20):
        raise LifecycleError("LIFE-002")

    plan = compute_effective_at(session, tenant_id, reason)
    effective_at = plan.effective_at
    if override_effective_at is not None:
        if override_effective_at < plan.effective_at:
            raise LifecycleError("LIFE-003")
        effective_at = override_effective_at

    session.execute(
        text(
            """
            UPDATE tenants SET
                status = 'cancelling',
                status_changed_at = now(),
                cancellation_effective_at = :effective_at,
                cancellation_reason = :reason,
                updated_at = now()
            WHERE id = :id
            """
        ),
        {"id": str(tenant_id), "effective_at": effective_at, "reason": reason},
    )

    owner_email = _owner_email(session, tenant_id)
    if owner_email:
        email_outbox.enqueue(
            session,
            tenant_id=tenant_id,
            to_address=owner_email,
            template="cancellation_confirmed",
            params={
                "tenant_name": row["name"],
                "effective_date": effective_at.date().isoformat(),
                "deletion_date": (effective_at + timedelta(days=EXPORT_WINDOW_DAYS)).date().isoformat(),
            },
            related_type="tenant",
            related_id=tenant_id,
        )

    payload = {
        "reason": reason,
        "note": note,
        "effective_at": effective_at.isoformat(),
        "rule": plan.rule,
        "flagged": plan.flagged,
        "overridden": override_effective_at is not None,
        "acting_as_tenant_id": str(tenant_id),
    }
    _lifecycle_event(
        session,
        tenant_id,
        "cancellation_scheduled",
        actor_user_id=actor_user_id,
        payload=payload,
        constants=constants_in_effect("CURE_PERIOD_DAYS", "EXPORT_WINDOW_DAYS"),
    )
    return {"status": "cancelling", "cancellation_effective_at": effective_at, "rule": plan.rule, "flagged": plan.flagged}


# ── The sweep: cancelling -> suspended + pending_deletion (same tick) ───────


@dataclass(frozen=True)
class SuspendClaim:
    tenant_id: UUID
    tenant_name: str
    stripe_subscription_id: str | None
    stripe_customer_id: str | None
    deletion_scheduled_at: datetime


def claim_for_suspend(session: Session, tenant_id: UUID) -> SuspendClaim | None:
    """
    Claims one tenant whose cancellation_effective_at has passed and performs
    the entire in-database transition (D-123): status -> pending_deletion,
    intake blocked, the deletion clock started, the three reminder emails
    scheduled (Section 7.14: "day 1, 15, 25"), the tenant's own confirmation
    email queued, and both lifecycle events written. Returns the Stripe
    subscription id so the caller can cancel it -- the one piece of this
    transition Stripe must confirm, not this database.

    Re-checks status and the effective date under a row lock, so two sweep
    ticks can never suspend one tenant twice. Returns None if the tenant
    isn't (or is no longer) due.
    """
    row = session.execute(
        text(
            "SELECT name, status, cancellation_effective_at, stripe_subscription_id, "
            "stripe_customer_id FROM tenants WHERE id = :id FOR UPDATE"
        ),
        {"id": str(tenant_id)},
    ).mappings().first()
    if row is None:
        return None
    if row["status"] != "cancelling":
        return None
    if row["cancellation_effective_at"] is None or row["cancellation_effective_at"] > datetime.now(UTC):
        return None

    deletion_at = datetime.now(UTC) + timedelta(days=EXPORT_WINDOW_DAYS)
    session.execute(
        text(
            """
            UPDATE tenants SET
                status = 'pending_deletion',
                status_changed_at = now(),
                intake_address_active = false,
                deletion_scheduled_at = :deletion_at,
                updated_at = now()
            WHERE id = :id
            """
        ),
        {"id": str(tenant_id), "deletion_at": deletion_at},
    )

    cconsts = constants_in_effect("EXPORT_WINDOW_DAYS", "REMINDER_DAYS")
    _lifecycle_event(session, tenant_id, "suspended", actor_user_id=None, constants=cconsts)
    _lifecycle_event(
        session,
        tenant_id,
        "pending_deletion_entered",
        actor_user_id=None,
        payload={"deletion_scheduled_at": deletion_at.isoformat()},
        constants=cconsts,
    )

    owner_email = _owner_email(session, tenant_id)
    if owner_email:
        email_outbox.enqueue(
            session,
            tenant_id=tenant_id,
            to_address=owner_email,
            template="suspended_notice",
            params={"tenant_name": row["name"], "deletion_date": deletion_at.date().isoformat()},
            related_type="tenant",
            related_id=tenant_id,
        )
    for day in REMINDER_DAYS:
        _schedule_reminder(session, tenant_id, run_at=datetime.now(UTC) + timedelta(days=day))

    founder_alerts.raise_alert(
        session,
        alert_type="tenant_entered_pending_deletion",
        severity="warning",
        tenant_id=tenant_id,
        payload={"deletion_scheduled_at": deletion_at.isoformat()},
        dedupe_key=f"tenant_entered_pending_deletion:{tenant_id}",
    )

    return SuspendClaim(
        tenant_id=tenant_id,
        tenant_name=row["name"],
        stripe_subscription_id=row["stripe_subscription_id"],
        stripe_customer_id=row["stripe_customer_id"],
        deletion_scheduled_at=deletion_at,
    )


def _schedule_reminder(session: Session, tenant_id: UUID, *, run_at: datetime) -> None:
    session.execute(
        text(
            """
            INSERT INTO scheduled_jobs (id, tenant_id, job_type, run_at, dedupe_key)
            VALUES (:id, :tenant_id, 'pending_deletion_reminder', :run_at, :dedupe)
            ON CONFLICT (dedupe_key) DO NOTHING
            """
        ),
        {
            "id": str(uuid4()),
            "tenant_id": str(tenant_id),
            "run_at": run_at,
            "dedupe": f"pending_deletion_reminder:{tenant_id}:{run_at.date().isoformat()}",
        },
    )


def due_for_suspend(session: Session, *, limit: int = 100) -> list[UUID]:
    """Tenant ids the sweep should attempt (`lifecycle_session()` only --
    read-only, cross-tenant). Each is then claimed, one at a time, in its own
    tenant_session so a crash mid-sweep suspends a prefix, never a partial
    tenant."""
    rows = session.execute(
        text(
            """
            SELECT id FROM tenants
            WHERE status = 'cancelling' AND cancellation_effective_at <= now()
            ORDER BY cancellation_effective_at
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).all()
    return [UUID(str(r[0])) for r in rows]


def due_for_ready_to_delete_alert(session: Session, *, limit: int = 100) -> list[UUID]:
    """pending_deletion tenants whose window has already elapsed, for the
    sweep to alert on (deduped -- fires once, not every tick)."""
    rows = session.execute(
        text(
            """
            SELECT id FROM tenants
            WHERE status = 'pending_deletion' AND deletion_scheduled_at <= now()
            ORDER BY deletion_scheduled_at
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).all()
    return [UUID(str(r[0])) for r in rows]


def raise_ready_to_delete_alert(session: Session, tenant_id: UUID) -> None:
    founder_alerts.raise_alert(
        session,
        alert_type="tenant_ready_to_delete",
        severity="warning",
        tenant_id=tenant_id,
        dedupe_key=f"tenant_ready_to_delete:{tenant_id}",
    )


# ── Reactivate ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReactivatePlan:
    tenant_name: str
    customer_id: str | None
    tier_id: UUID
    tier_name: str
    monthly_price: Decimal
    document_allowance: int


def plan_reactivate(session: Session, tenant_id: UUID) -> ReactivatePlan:
    row = session.execute(
        text(
            """
            SELECT t.name, t.status, t.stripe_customer_id,
                   tr.id AS tier_id, tr.name AS tier_name, tr.monthly_price, tr.document_allowance
            FROM tenants t
            LEFT JOIN tiers tr ON tr.id = t.tier_id
            WHERE t.id = :id
            """
        ),
        {"id": str(tenant_id)},
    ).mappings().first()
    if row is None:
        raise LifecycleError("CON-001")
    if row["status"] not in REACTIVATABLE:
        raise LifecycleError("LIFE-004", {"status": row["status"]})
    if row["tier_id"] is None:
        raise LifecycleError("LIFE-004", {"reason": "no tier on record"})
    return ReactivatePlan(
        tenant_name=row["name"],
        customer_id=row["stripe_customer_id"],
        tier_id=UUID(str(row["tier_id"])),
        tier_name=row["tier_name"],
        monthly_price=Decimal(row["monthly_price"]),
        document_allowance=row["document_allowance"],
    )


@dataclass(frozen=True)
class ReactivateBilling:
    subscription_id: str | None
    subscription_status: str | None
    current_period_end: int | None  # unix seconds


def complete_reactivate(
    session: Session,
    tenant_id: UUID,
    *,
    actor_user_id: UUID,
    plan: ReactivatePlan,
    billing: ReactivateBilling,
    app_url: str,
) -> None:
    """Mirrors onboarding.complete_go_live: re-checks state under a row
    lock, so a reactivate racing a second reactivate (or the sweep) can't
    land twice."""
    row = session.execute(
        text("SELECT status FROM tenants WHERE id = :id FOR UPDATE"),
        {"id": str(tenant_id)},
    ).mappings().first()
    if row is None:
        raise LifecycleError("CON-001")
    if row["status"] not in REACTIVATABLE:
        raise LifecycleError("LIFE-004", {"status": row["status"]})

    period_end = (
        datetime.fromtimestamp(billing.current_period_end, tz=UTC)
        if billing.current_period_end
        else None
    )
    session.execute(
        text(
            """
            UPDATE tenants SET
                status = 'active',
                status_changed_at = now(),
                cancellation_effective_at = NULL,
                cancellation_reason = NULL,
                deletion_scheduled_at = NULL,
                first_past_due_at = NULL,
                intake_address_active = true,
                stripe_subscription_id = :sub_id,
                stripe_subscription_status = :sub_status,
                stripe_current_period_end = :period_end,
                updated_at = now()
            WHERE id = :id
            """
        ),
        {
            "id": str(tenant_id),
            "sub_id": billing.subscription_id,
            "sub_status": billing.subscription_status,
            "period_end": period_end,
        },
    )
    # A reactivated tenant gets no more "you're about to be deleted" mail.
    session.execute(
        text(
            "UPDATE scheduled_jobs SET status = 'cancelled' "
            "WHERE tenant_id = :id AND job_type = 'pending_deletion_reminder' AND status = 'pending'"
        ),
        {"id": str(tenant_id)},
    )

    owner_email = _owner_email(session, tenant_id)
    if owner_email:
        email_outbox.enqueue(
            session,
            tenant_id=tenant_id,
            to_address=owner_email,
            template="reactivated",
            params={"tenant_name": plan.tenant_name, "app_url": app_url},
            related_type="tenant",
            related_id=tenant_id,
        )
    _lifecycle_event(
        session,
        tenant_id,
        "reactivated",
        actor_user_id=actor_user_id,
        payload={
            "acting_as_tenant_id": str(tenant_id),
            "stripe_subscription_id": billing.subscription_id,
        },
    )
