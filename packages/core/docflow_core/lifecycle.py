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
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import email_outbox, founder_alerts, system_actors
from docflow_core.constants import (
    CURE_PERIOD_DAYS,
    EXPORT_WINDOW_DAYS,
    REMINDER_DAYS,
    constants_in_effect,
)

REASONS = ("customer_requested", "non_payment", "for_cause")
# A tenant may only be cancelled from 'active'; may only be reactivated from
# one of these.
#
# Same two states as `intake_gate.LIFECYCLE_BLOCKED_STATUSES`, and deliberately a
# separate constant: that one answers "does this state stop new documents
# arriving", this one answers "can this tenant be brought back". They coincide
# today and are free to diverge. Neither is defined in terms of the other, so a
# change to one cannot silently change the other.
REACTIVATABLE = ("suspended", "pending_deletion")

logger = logging.getLogger(__name__)


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
    actor_user_id: UUID,
    payload: dict[str, Any] | None = None,
    constants: dict[str, Any] | None = None,
) -> None:
    # Every event names who did it: a person, or a named system actor
    # (docflow_core.system_actors, D-165). The column is NOT NULL since 0028.
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
            "actor": str(actor_user_id),
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
    # "Immediate" is the database's now, read in the same statement: the
    # sweep later compares the stored date with the database's clock, so that
    # clock sets it (D-170 #1). now() is the transaction's start, so cancel()'s
    # UPDATE in the same transaction stamps status_changed_at with this value.
    row = session.execute(
        text(
            "SELECT stripe_subscription_id, stripe_current_period_end, first_past_due_at, "
            "now() AS db_now FROM tenants WHERE id = :id"
        ),
        {"id": str(tenant_id)},
    ).mappings().first()
    if row is None:
        raise LifecycleError("CON-001")

    now = row["db_now"]
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



def _check_cancel(
    session: Session,
    tenant_id: UUID,
    *,
    reason: str,
    note: str | None,
    override_effective_at: datetime | None,
    lock: bool,
) -> tuple[Any, datetime, EffectiveDatePlan]:
    """Every rule a cancel must pass, its effective date, and the rule that
    produced it. Shared by
    `cancel` (under a row lock) and `trial_cancel_target` (read only, before
    Stripe is touched), so the two can never disagree."""
    if reason not in REASONS:
        raise LifecycleError("LIFE-001")
    row = session.execute(
        text("SELECT name, status FROM tenants WHERE id = :id" + (" FOR UPDATE" if lock else "")),
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
    return row, effective_at, plan


@dataclass(frozen=True)
class TrialCancelTarget:
    subscription_id: str
    customer_id: str


def trial_cancel_target(
    session: Session,
    tenant_id: UUID,
    *,
    reason: str,
    note: str | None,
    override_effective_at: datetime | None,
) -> TrialCancelTarget | None:
    """
    For a cancel confirmed while the tenant is still in its trial (founder,
    2026-09-29): the Stripe subscription whose trial must end with nothing
    charged, or None. Runs every rule `cancel` runs, without writing, so
    Stripe is only touched for a cancel that will be recorded; the caller then
    calls Stripe with no transaction open (D-173) and records the cancel.
    """
    _check_cancel(
        session, tenant_id, reason=reason, note=note, override_effective_at=override_effective_at, lock=False
    )
    row = session.execute(
        text(
            "SELECT stripe_subscription_status, stripe_subscription_id, stripe_customer_id "
            "FROM tenants WHERE id = :id"
        ),
        {"id": str(tenant_id)},
    ).mappings().one()
    if row["stripe_subscription_status"] != "trialing" or not row["stripe_subscription_id"]:
        return None
    if not row["stripe_customer_id"]:
        return None
    return TrialCancelTarget(
        subscription_id=str(row["stripe_subscription_id"]), customer_id=str(row["stripe_customer_id"])
    )


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
    row, effective_at, plan = _check_cancel(
        session, tenant_id, reason=reason, note=note, override_effective_at=override_effective_at, lock=True
    )

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
    return {
        "status": "cancelling",
        "cancellation_effective_at": effective_at,
        "rule": plan.rule,
        "flagged": plan.flagged,
    }


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
            # Due by the database's clock, the one the sweep's claim query used
            # to pick this tenant (D-170 #1).
            "SELECT name, status, cancellation_effective_at <= now() AS due, stripe_subscription_id, "
            "stripe_customer_id FROM tenants WHERE id = :id FOR UPDATE"
        ),
        {"id": str(tenant_id)},
    ).mappings().first()
    if row is None:
        return None
    if row["status"] != "cancelling":
        return None
    if not row["due"]:
        return None

    # The database stamps the deletion date, because the database is the clock
    # that later decides the tenant is due: the sweep, the delete guard and the
    # reminder text all compare it with now() (D-170 #2).
    stamped = session.execute(
        text(
            """
            UPDATE tenants SET
                status = 'pending_deletion',
                status_changed_at = now(),
                intake_address_active = false,
                deletion_scheduled_at = now() + make_interval(days => :window),
                -- Stage 3a: Stripe is called after this commits, so the
                -- cancel is marked as owed in the same transaction. A worker
                -- killed before Stripe confirms leaves the mark, and the next
                -- sweep retries (`process_pending_cancel`).
                stripe_cancel_pending_at = CASE
                    WHEN stripe_subscription_id IS NOT NULL OR stripe_customer_id IS NOT NULL
                    THEN now() END,
                updated_at = now()
            WHERE id = :id
            RETURNING now() AS suspended_at, deletion_scheduled_at
            """
        ),
        {"id": str(tenant_id), "window": EXPORT_WINDOW_DAYS},
    ).mappings().one()
    deletion_at = stamped["deletion_scheduled_at"]

    cconsts = constants_in_effect("EXPORT_WINDOW_DAYS", "REMINDER_DAYS")
    # Nobody clicked anything: the sweep did it, and says so (D-165).
    _lifecycle_event(
        session, tenant_id, "suspended", actor_user_id=system_actors.LIFECYCLE_SWEEP, constants=cconsts
    )
    _lifecycle_event(
        session,
        tenant_id,
        "pending_deletion_entered",
        actor_user_id=system_actors.LIFECYCLE_SWEEP,
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
        _schedule_reminder(session, tenant_id, day=day)

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


def _schedule_reminder(session: Session, tenant_id: UUID, *, day: int) -> None:
    # run_at, and the date in its dedupe key, are the database's, computed in
    # this statement: the job sweep compares run_at with now() (D-170 #4). In
    # the suspend transaction, now() is the instant the export window began.
    session.execute(
        text(
            """
            INSERT INTO scheduled_jobs (id, tenant_id, job_type, run_at, dedupe_key)
            VALUES (
                :id, CAST(:tenant_id AS uuid), 'pending_deletion_reminder',
                now() + make_interval(days => :day),
                'pending_deletion_reminder:' || CAST(:tenant_id AS text) || ':'
                    || ((now() + make_interval(days => :day)) AT TIME ZONE 'UTC')::date::text
            )
            ON CONFLICT (dedupe_key) DO NOTHING
            """
        ),
        {"id": str(uuid4()), "tenant_id": str(tenant_id), "day": day},
    )


def pending_cancels(session: Session, *, limit: int = 100) -> list[UUID]:
    """Tenants whose Stripe cancel is still owed (`lifecycle_session()` only
    -- read-only, cross-tenant), oldest first. Each is then handled in its own
    tenant_session by `process_pending_cancel`."""
    rows = session.execute(
        text(
            """
            SELECT id FROM tenants
            WHERE stripe_cancel_pending_at IS NOT NULL
            ORDER BY stripe_cancel_pending_at
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).all()
    return [UUID(str(r[0])) for r in rows]


def process_pending_cancel(tenant_id: UUID) -> str:
    """
    Cancel a suspended tenant's Stripe billing, if it is still owed. One of:
    "none" (nothing owed), "skipped" (the tenant is no longer suspended --
    reactivated, say -- so the mark is cleared and Stripe is not called),
    "cancelled", or "failed" (the mark stays, the founder is alerted, and
    the next sweep tries again).

    The tenant row is locked and its state re-checked immediately before the
    Stripe calls, and the lock is HELD across them (founder, Stage 3a). That
    is deliberate, and the opposite of the webhook's fetch-outside-a-
    transaction rule (D-173): releasing it would let a reactivation commit
    between the check and the call, and the cancel would then hit the
    tenant's live subscription. A reactivation that arrives meanwhile waits
    for the lock. The wait is bounded by the Stripe timeouts (at most three
    calls of TIMEOUT_SECONDS each), well inside the 5-minute idle-transaction
    cap on docflow_app (0028).
    """
    from docflow_core.db import tenant_session
    from docflow_core.external_services import (
        ExternalServiceError,
        cancel_subscription,
        void_pending_setup_fee,
    )

    with tenant_session(tenant_id) as session:
        row = session.execute(
            text(
                "SELECT status, stripe_subscription_id, stripe_customer_id, stripe_cancel_pending_at "
                "FROM tenants WHERE id = :id FOR UPDATE"
            ),
            {"id": str(tenant_id)},
        ).mappings().first()
        if row is None or row["stripe_cancel_pending_at"] is None:
            return "none"
        if row["status"] not in REACTIVATABLE:
            _clear_pending_cancel(session, tenant_id)
            return "skipped"
        try:
            if row["stripe_subscription_id"]:
                cancel_subscription(row["stripe_subscription_id"])
            if row["stripe_customer_id"]:
                # A tenant cancelled mid-trial (D-125) may still have a
                # pending, never-invoiced setup-fee item -- clear it so it
                # can't land on some unrelated future invoice.
                void_pending_setup_fee(customer_id=row["stripe_customer_id"], tenant_id=tenant_id)
        except ExternalServiceError as exc:
            logger.error("stripe_cancel_failed tenant_id=%s error=%s", tenant_id, type(exc).__name__)
            founder_alerts.raise_alert(
                session,
                alert_type="stripe_cancel_failed",
                severity="high",
                tenant_id=tenant_id,
                payload={"subscription_id": row["stripe_subscription_id"]},
                dedupe_key=f"stripe_cancel_failed:{tenant_id}",
            )
            return "failed"
        _clear_pending_cancel(session, tenant_id)
        return "cancelled"


def _clear_pending_cancel(session: Session, tenant_id: UUID) -> None:
    session.execute(
        text("UPDATE tenants SET stripe_cancel_pending_at = NULL, updated_at = now() WHERE id = :id"),
        {"id": str(tenant_id)},
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


def tenants_for_intake_housekeeping(session: Session, *, limit: int = 1000) -> list[UUID]:
    """Every tenant that still exists, for the periodic intake housekeeping
    (retiring grace addresses, held-document retention). `lifecycle_session()`
    only -- read-only; the work happens in each tenant's own session."""
    rows = session.execute(
        text("SELECT id FROM tenants WHERE deleted_at IS NULL AND status <> 'deleted' LIMIT :limit"),
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
    # When the tenant's status last changed (it was suspended, or entered
    # pending deletion). Unchanged until this reactivation commits, so it
    # names this reactivation in the Stripe idempotency key: a retried click
    # reuses the key, a later reactivation gets a new one (Stage 3a).
    status_changed_at: datetime | None = None
    # When the current suspension began (its `suspended` lifecycle event):
    # the line between the founder's two invoice sections (Stage 3a).
    suspended_at: datetime | None = None
    # 'card' | 'invoice' (migration 0031). A card-billed tenant is charged at
    # once on reactivation, and a declined card refuses it (D5).
    billing_method: str = "invoice"

    @property
    def idempotency_scope(self) -> str:
        changed = self.status_changed_at
        return f"reactivate-{int(changed.timestamp() * 1_000_000) if changed else 0}"


def plan_reactivate(session: Session, tenant_id: UUID) -> ReactivatePlan:
    row = session.execute(
        text(
            """
            SELECT t.name, t.status, t.stripe_customer_id, t.status_changed_at, t.billing_method,
                   tr.id AS tier_id, tr.name AS tier_name, tr.monthly_price, tr.document_allowance,
                   (SELECT max(e.created_at) FROM tenant_lifecycle_events e
                     WHERE e.tenant_id = t.id AND e.event_type = 'suspended') AS suspended_at
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
        status_changed_at=row["status_changed_at"],
        suspended_at=row["suspended_at"],
        billing_method=row["billing_method"],
    )


def _invoice_section(heading: str, invoices: list[Any]) -> tuple[str, str]:
    """One section of the reactivation invoice alert, as text (the email and
    the Console show payload values as plain strings), and its total."""
    total = sum((i.amount for i in invoices), Decimal("0.00"))
    lines = "; ".join(
        f"{i.invoice_id}"
        + (f" #{i.number}" if i.number else "")
        + f" {i.status} ${i.amount}"
        + f" (created {datetime.fromtimestamp(i.created, tz=UTC):%Y-%m-%d})"
        for i in invoices
    )
    count = f"{len(invoices)} invoice{'' if len(invoices) == 1 else 's'}"
    return f"{heading}: {count}, total ${total}" + (f" -- {lines}" if lines else ""), f"{total}"


def raise_reactivation_invoice_alert(
    session: Session,
    tenant_id: UUID,
    *,
    subscription_id: str,
    subscription_status: str,
    suspended_at: datetime,
    review: Any | None,
) -> bool:
    """
    Reactivation kept the old subscription because Stripe still held it live
    after the suspension (Stage 3a, option C; founder 2026-09-29). Lists what
    the founder has to act on at Stripe, in two sections with a total each:
    open invoices from before the suspension (service delivered: collect),
    and draft, open or paid ones since (void, or refund). `review` is None
    when Stripe couldn't be asked -- the alert then says so rather than
    staying quiet. Nothing to act on: no alert. Returns whether one was raised.
    """
    from docflow_core.external_services import BEFORE_SUSPENSION_HEADING, DURING_SUSPENSION_HEADING

    payload: dict[str, Any] = {
        "stripe_subscription_id": subscription_id,
        "subscription_status": subscription_status,
        "suspended_at": suspended_at.astimezone(UTC).isoformat(timespec="seconds"),
    }
    if review is None:
        payload["invoices"] = "Stripe's invoice list could not be read -- check this subscription in Stripe"
    else:
        if not review.before_suspension and not review.during_suspension:
            return False
        before, before_total = _invoice_section(BEFORE_SUSPENSION_HEADING, review.before_suspension)
        during, during_total = _invoice_section(DURING_SUSPENSION_HEADING, review.during_suspension)
        payload.update(
            before_suspension=before,
            before_suspension_total=before_total,
            during_suspension=during,
            during_suspension_total=during_total,
        )
    return founder_alerts.raise_alert(
        session,
        alert_type="reactivation_invoices_to_review",
        severity="high",
        tenant_id=tenant_id,
        payload=payload,
        dedupe_key=f"reactivation_invoices:{tenant_id}:{subscription_id}:{int(suspended_at.timestamp())}",
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
                -- D-139: the founding price is a go-live perk; a reactivated
                -- subscription is at list price.
                founding_price_ends_at = NULL,
                -- Stage 3a: in the same transaction as the status change, so
                -- a sweep retrying an owed cancel can never cancel the
                -- reactivated tenant's billing.
                stripe_cancel_pending_at = NULL,
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
