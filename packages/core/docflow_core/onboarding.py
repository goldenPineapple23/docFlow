"""
Onboarding Steps 6-9 (CLAUDE.md Section 7.15.2): the test batch and go-live.

Everything here runs in the tenant's own session. The Console reaches it
through audited /admin routes that record the founder as the actor and the
tenant as acting_as (Section 7.15.1); there is no onboarding-specific
extraction, review or upload path (Step 7: "No onboarding-specific
extraction path exists").

`onboarding_status` is a state machine that only moves forward, and every
move is a `tenant_lifecycle_events` row:

    tenant_created -> catalog_loaded -> test_batch_uploaded
        -> test_batch_running -> test_batch_complete -> live
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

from docflow_core import document_status, email_outbox
from docflow_core.config import get_settings
from docflow_core.constants import (
    FIRST_WEEK_CHECKIN_DAYS,
    TRIAL_ENDING_REMINDER_DAYS_BEFORE,
    TRIAL_PERIOD_DAYS,
    constants_in_effect,
)
from docflow_core.db import rowcount
from docflow_core.model_provider import PROVIDER

ORDER = (
    "tenant_created",
    "catalog_loaded",
    "test_batch_uploaded",
    "test_batch_running",
    "test_batch_complete",
    "live",
)

# While the test batch may still take files and runs.
TEST_BATCH_OPEN = ("catalog_loaded", "test_batch_uploaded", "test_batch_running")
# Resolved states a test document can finish in. Rejected is not one: the
# step says "when every test-batch document is approved".
TEST_DOCUMENT_DONE = ("approved", "exported")


class OnboardingError(Exception):
    """A refusal with an error-catalog code (ONB-0xx)."""

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
    # clock_timestamp(), not now() -- see docflow_core.lifecycle._lifecycle_event:
    # now() is frozen for the whole transaction in Postgres, which would tie
    # created_at for any two events logged in one transaction.
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
            "payload": json.dumps(payload or {}),
        },
    )


def advance(
    session: Session,
    tenant_id: UUID,
    to: str,
    *,
    actor_user_id: UUID,
    payload: dict[str, Any] | None = None,
    constants: dict[str, Any] | None = None,
) -> bool:
    """Move onboarding_status forward to `to`, from any earlier state. Never
    backward. Returns whether it moved (and so whether an event was written)."""
    earlier = list(ORDER[: ORDER.index(to)])
    moved = rowcount(
        session.execute(
            text(
                "UPDATE tenants SET onboarding_status = :to, updated_at = now() "
                "WHERE id = :id AND onboarding_status = ANY(string_to_array(:earlier, ','))"
            ),
            {"to": to, "id": str(tenant_id), "earlier": ",".join(earlier)},
        )
    )
    if moved:
        _lifecycle_event(
            session,
            tenant_id,
            f"onboarding_{to}",
            actor_user_id=actor_user_id,
            payload=payload,
            constants=constants,
        )
    return bool(moved)


def _status(session: Session, tenant_id: UUID, *, lock: bool = False) -> str:
    row = session.execute(
        text("SELECT onboarding_status FROM tenants WHERE id = :id" + (" FOR UPDATE" if lock else "")),
        {"id": str(tenant_id)},
    ).first()
    if row is None:
        raise LookupError(tenant_id)
    return str(row[0])


# ── Step 6: upload ──────────────────────────────────────────────────────────


def check_test_batch_open(session: Session, tenant_id: UUID) -> None:
    """Before a test-batch upload: a catalog exists, and the batch isn't closed."""
    status = _status(session, tenant_id)
    if status == "tenant_created":
        raise OnboardingError("ONB-001")
    if status not in TEST_BATCH_OPEN:
        raise OnboardingError("ONB-002")


def record_test_batch_upload(
    session: Session, tenant_id: UUID, document_ids: list[str], *, actor_user_id: UUID
) -> None:
    advance(
        session,
        tenant_id,
        "test_batch_uploaded",
        actor_user_id=actor_user_id,
        payload={"document_ids": document_ids, "acting_as_tenant_id": str(tenant_id)},
    )


# ── Step 7: run ─────────────────────────────────────────────────────────────


def start_test_batch_run(session: Session, tenant_id: UUID, *, actor_user_id: UUID) -> list[UUID]:
    """
    Release every staged test document to the normal pipeline and return
    their ids, oldest first, for the caller to enqueue after commit.
    """
    status = _status(session, tenant_id, lock=True)
    if status not in ("test_batch_uploaded", "test_batch_running"):
        raise OnboardingError("ONB-002" if status in ("test_batch_complete", "live") else "ONB-003")
    staged = session.execute(
        text(
            """
            SELECT id, created_at FROM documents
            WHERE is_test_batch AND status = 'staged' AND deleted_at IS NULL
            ORDER BY created_at, id
            FOR UPDATE
            """
        )
    ).all()
    moved = set(
        document_status.transition_many(
            session, tenant_id, [UUID(str(r[0])) for r in staged], from_statuses=["staged"], to="pending"
        )
    )
    released = [r for r in staged if UUID(str(r[0])) in moved]
    if not released:
        raise OnboardingError("ONB-003")
    ids = [UUID(str(row[0])) for row in sorted(released, key=lambda r: (r[1], str(r[0])))]
    advance(
        session,
        tenant_id,
        "test_batch_running",
        actor_user_id=actor_user_id,
        payload={"document_ids": [str(i) for i in ids], "acting_as_tenant_id": str(tenant_id)},
    )
    return ids


def list_test_batch_documents(session: Session) -> list[dict[str, Any]]:
    """The tenant page's per-document status and cost list (Steps 7-8)."""
    rows = (
        session.execute(
            text(
                """
            SELECT d.id, d.original_filename, d.status, d.created_at, d.approved_at,
                   d.est_cost_usd, d.overall_confidence, d.input_tokens, d.output_tokens,
                   h.po_number, h.buyer_name,
                   -- Stage 3d: "Delayed" (DOC-023), as on the tenant surface.
                   (d.status = 'pending' AND d.wait_cause = 'model_provider'
                    AND (SELECT p.status FROM public.provider_state(:provider) p) = 'down'
                   ) AS delayed
            FROM documents d
            LEFT JOIN document_headers h ON h.document_id = d.id AND h.deleted_at IS NULL
            WHERE d.is_test_batch AND d.deleted_at IS NULL
            ORDER BY d.created_at, d.id
            """
            ),
            {"provider": PROVIDER},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


# ── Step 8: complete ────────────────────────────────────────────────────────


def mark_test_batch_complete(session: Session, tenant_id: UUID, *, actor_user_id: UUID) -> None:
    status = _status(session, tenant_id, lock=True)
    if status in ("test_batch_complete", "live"):
        raise OnboardingError("ONB-002")
    if status != "test_batch_running":
        raise OnboardingError("ONB-004")
    counts = (
        session.execute(
            text(
                "SELECT count(*) AS total, "
                "count(*) FILTER (WHERE status = ANY(string_to_array(:done, ','))) AS done "
                "FROM documents WHERE is_test_batch AND deleted_at IS NULL"
            ),
            {"done": ",".join(TEST_DOCUMENT_DONE)},
        )
        .mappings()
        .one()
    )
    if counts["total"] == 0 or counts["done"] < counts["total"]:
        raise OnboardingError("ONB-004", {"approved": counts["done"], "total": counts["total"]})
    session.execute(
        text("UPDATE tenants SET test_batch_completed_at = now(), updated_at = now() WHERE id = :id"),
        {"id": str(tenant_id)},
    )
    advance(
        session,
        tenant_id,
        "test_batch_complete",
        actor_user_id=actor_user_id,
        payload={"documents": counts["total"], "acting_as_tenant_id": str(tenant_id)},
    )


# ── Step 9: go live ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GoLivePlan:
    """What go-live will bill: the deal recorded on the tenant (D-117) and its
    tier -- never typed in at go-live."""

    tenant_name: str
    customer_id: str | None
    tier_id: UUID
    tier_name: str
    monthly_price: Decimal
    promo_monthly_price: Decimal | None
    promo_days: int | None
    document_allowance: int
    invite_sent: bool
    setup_fee_amount: Decimal
    setup_fee_billing: str  # 'stripe' | 'invoiced_manually'
    setup_fee_note: str | None
    setup_fee_preset_name: str | None
    founding_price: bool
    # Card billing (founder, 2026-09-29). The "Founding customer" box as
    # ticked at Create tenant (D-117) -- `founding_price` above is that box
    # AND a tier with a promo; the setup fee's timing follows the box alone.
    founding_customer: bool = False
    card_on_file: bool = False
    setup_fee_paid: bool = False
    billing_method: str = "invoice"  # 'card' | 'invoice' -- what this go-live will set

    @property
    def fee_at_signing(self) -> bool:
        """A standard customer billed by card pays the setup fee when they add
        their card, not with month one (D2)."""
        return (
            self.billing_method == "card"
            and not self.founding_customer
            and self.setup_fee_billing == "stripe"
            and self.setup_fee_amount > 0
        )

    @property
    def setup_fee_on_first_invoice(self) -> Decimal | None:
        """The fee go-live adds to the first invoice: billed through Stripe,
        not zero, and not already paid at signing."""
        if self.setup_fee_billing != "stripe" or self.setup_fee_amount <= 0 or self.setup_fee_paid:
            return None
        return self.setup_fee_amount

    @property
    def promo_months(self) -> int | None:
        # A promo stated in days, billed monthly: whole months, rounded up so
        # the customer never gets less than the promised period.
        return -(-self.promo_days // 30) if self.promo_days else None


def plan_go_live(session: Session, tenant_id: UUID, *, billing_method: str | None = None) -> GoLivePlan:
    """What go-live will bill. With `billing_method` (the go-live request) the
    card-billing gates apply: a card on file (D1, ONB-015) and, for a fee paid
    at signing, that payment (D2, ONB-016). Without it (the summary screen)
    nothing is refused for billing, and the plan says what each method needs."""
    row = (
        session.execute(
            text(
                """
            SELECT t.name, t.onboarding_status, t.stripe_customer_id, t.invite_sent_at,
                   t.card_on_file_at, t.setup_fee_paid_at,
                   t.setup_fee_amount, t.setup_fee_billing, t.setup_fee_note, t.founding_price,
                   sp.name AS setup_fee_preset_name,
                   tr.id AS tier_id, tr.name AS tier_name, tr.monthly_price,
                   tr.promo_monthly_price, tr.promo_days, tr.document_allowance
            FROM tenants t
            LEFT JOIN tiers tr ON tr.id = t.tier_id
            LEFT JOIN setup_fee_presets sp ON sp.id = t.setup_fee_preset_id
            WHERE t.id = :id
            """
            ),
            {"id": str(tenant_id)},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise LookupError(tenant_id)
    if row["onboarding_status"] == "live":
        raise OnboardingError("ONB-006")
    if row["onboarding_status"] != "test_batch_complete":
        raise OnboardingError("ONB-005")
    if row["tier_id"] is None:
        raise OnboardingError("ONB-009")
    if row["setup_fee_amount"] is None or row["setup_fee_billing"] is None:
        raise OnboardingError("ONB-010")
    promo = row["promo_monthly_price"]
    plan = GoLivePlan(
        tenant_name=row["name"],
        customer_id=row["stripe_customer_id"],
        tier_id=UUID(str(row["tier_id"])),
        tier_name=row["tier_name"],
        monthly_price=Decimal(row["monthly_price"]),
        promo_monthly_price=(
            Decimal(row["promo_monthly_price"]) if row["promo_monthly_price"] is not None else None
        ),
        promo_days=row["promo_days"],
        document_allowance=row["document_allowance"],
        invite_sent=row["invite_sent_at"] is not None,
        setup_fee_amount=Decimal(row["setup_fee_amount"]),
        setup_fee_billing=row["setup_fee_billing"],
        setup_fee_note=row["setup_fee_note"],
        setup_fee_preset_name=row["setup_fee_preset_name"],
        # Only a tier with a promo has a founding price to give.
        founding_price=bool(row["founding_price"]) and promo is not None,
        founding_customer=bool(row["founding_price"]),
        card_on_file=row["card_on_file_at"] is not None,
        setup_fee_paid=row["setup_fee_paid_at"] is not None,
        billing_method=billing_method or "invoice",
    )
    if billing_method == "card":
        if not plan.card_on_file:
            raise OnboardingError("ONB-015")
        if plan.fee_at_signing and not plan.setup_fee_paid:
            raise OnboardingError("ONB-016")
        if not get_settings().support_email.strip():
            # The go-live email and the trial-ending email say how to cancel,
            # and with no support address they can't (as "Ask for a card").
            raise OnboardingError("ONB-018")
    return plan


# The go-live email's line about billing, by method (card billing, founder
# 2026-09-29). Kept beside the template's other parameters, not in it, because
# the template can't branch.
GO_LIVE_BILLING_LINES = {
    "invoice": "Your first invoice comes from Stripe by email.",
    # {trial_end_date} and {first_charge_amount} are filled at go-live
    # (founder, 2026-09-29): the first month at the rate they pay, plus the
    # setup fee when it comes with month one (founding customers). The cancel
    # sentence names the last day to ask, the day before the trial ends; its
    # ending is CANCEL_OUTCOMES' (founder, 2026-09-29).
    "card": (
        "Your 7-day free trial starts today. On {trial_end_date}, the card on file is charged "
        "{first_charge_amount}. To cancel, email {support_email} by {cancel_by_date}, and "
        "{cancel_outcome}."
    ),
}

# How the cancel sentence ends (founder, 2026-09-29): a standard customer's
# fee was paid at signing, so only the monthly charge is avoided; for a
# founding customer (fee still to come with month one) and one with no fee,
# nothing at all is charged -- the same words in all three no-fee emails.
CANCEL_OUTCOMES = {
    "founding": "nothing is charged",
    "at_signing": "no monthly charge is made",
    "no_fee": "nothing is charged",
}


def card_trial_case(plan: GoLivePlan) -> str:
    """Which of the three card-billing cases a go-live is: the setup fee comes
    with month one ('founding'), was paid at signing ('at_signing'), or there
    is none through Stripe ('no_fee'). Picks the cancel sentence and the
    trial-ending email, so the two always agree."""
    if plan.setup_fee_on_first_invoice:
        return "founding"
    if plan.setup_fee_paid:
        return "at_signing"
    return "no_fee"


def first_charge_amount(plan: GoLivePlan) -> Decimal:
    """What a card-billed customer's card is charged when the trial ends: the
    first month (the founding rate if they have one) plus the setup fee if it
    goes on that first invoice. From the plan, never typed in (Section 10)."""
    month = (
        plan.promo_monthly_price if plan.founding_price and plan.promo_monthly_price else plan.monthly_price
    )
    return month + (plan.setup_fee_on_first_invoice or Decimal("0"))


@dataclass(frozen=True)
class GoLiveBilling:
    """What Stripe returned. The fee and founding price are the plan's."""

    subscription_id: str | None
    subscription_status: str | None
    current_period_end: int | None
    # From Stripe's answer when it includes it (D-139); otherwise computed
    # below from the coupon's length, which is what Stripe itself applies.
    founding_ends_at: int | None = None
    # When the trial ends (unix seconds), for the card-billed go-live email.
    trial_end: int | None = None


def complete_go_live(
    session: Session,
    tenant_id: UUID,
    *,
    actor_user_id: UUID,
    plan: GoLivePlan,
    billing: GoLiveBilling,
    app_url: str,
) -> None:
    """
    The database half of go-live, in one transaction, after Stripe has
    succeeded: the intake address goes live, the go-live email is queued,
    the first-week check-in is scheduled, and the tenant is marked live.
    Re-checks the state under a row lock, so two go-lives can't both land.
    """
    if _status(session, tenant_id, lock=True) != "test_batch_complete":
        raise OnboardingError("ONB-006" if _status(session, tenant_id) == "live" else "ONB-005")

    period_end = (
        datetime.fromtimestamp(billing.current_period_end, tz=UTC) if billing.current_period_end else None
    )
    session.execute(
        text(
            """
            UPDATE tenants SET
                intake_address_active = true,
                went_live_at = now(),
                stripe_subscription_id = :sub_id,
                stripe_subscription_status = :sub_status,
                stripe_current_period_end = :period_end,
                billing_method = :billing_method,
                -- When the founding price ends (D-139), so MRR can count
                -- what a founding customer actually pays.
                founding_price_ends_at = CASE WHEN :founding THEN coalesce(
                    to_timestamp(CAST(:founding_end AS double precision)),
                    now() + make_interval(months => CAST(:promo_months AS integer))
                ) END,
                updated_at = now()
            WHERE id = :id
            """
        ),
        {
            "id": str(tenant_id),
            "sub_id": billing.subscription_id,
            "sub_status": billing.subscription_status,
            "period_end": period_end,
            "billing_method": plan.billing_method,
            "founding": bool(plan.founding_price and plan.promo_months and billing.subscription_id),
            "founding_end": billing.founding_ends_at,
            "promo_months": plan.promo_months or 0,
        },
    )

    owner = session.execute(
        text(
            "SELECT email FROM users WHERE tenant_id = :id AND role = 'owner' AND deleted_at IS NULL "
            "ORDER BY created_at LIMIT 1"
        ),
        {"id": str(tenant_id)},
    ).first()
    address = session.execute(
        text("SELECT address FROM intake_addresses WHERE tenant_id = :id AND status = 'active'"),
        {"id": str(tenant_id)},
    ).scalar()
    outbox_id = None
    if owner is not None:
        outbox_id = email_outbox.enqueue(
            session,
            tenant_id=tenant_id,
            to_address=owner[0],
            template="go_live",
            params={
                "tenant_name": plan.tenant_name,
                "intake_address": address or "(ask DocFlow for your address)",
                "app_url": app_url,
                "tier_name": plan.tier_name,
                "document_allowance": f"{plan.document_allowance:,}",
                "billing_line": _billing_line(session, tenant_id, plan, billing),
            },
            related_type="tenant",
            related_id=tenant_id,
        )

    job_id = schedule_first_week_checkin(session, tenant_id)
    trial_job_id = schedule_trial_ending_reminder(session, tenant_id, plan, billing)
    advance(
        session,
        tenant_id,
        "live",
        actor_user_id=actor_user_id,
        payload={
            "acting_as_tenant_id": str(tenant_id),
            "tier_id": str(plan.tier_id),
            "setup_fee_amount": str(plan.setup_fee_amount),
            "setup_fee_billing": plan.setup_fee_billing,
            "founding_price": plan.founding_price,
            "billing_method": plan.billing_method,
            "setup_fee_paid_at_signing": plan.setup_fee_paid,
            "stripe_subscription_id": billing.subscription_id,
            "go_live_email_outbox_id": str(outbox_id) if outbox_id else None,
            "first_week_checkin_job_id": str(job_id),
            "trial_ending_reminder_job_id": str(trial_job_id) if trial_job_id else None,
        },
        constants=constants_in_effect(
            "FIRST_WEEK_CHECKIN_DAYS",
            "INVOICE_DAYS_UNTIL_DUE",
            "TRIAL_PERIOD_DAYS",
            "TRIAL_ENDING_REMINDER_DAYS_BEFORE",
        ),
    )


def _billing_line(session: Session, tenant_id: UUID, plan: GoLivePlan, billing: GoLiveBilling) -> str:
    line = GO_LIVE_BILLING_LINES[plan.billing_method]
    if plan.billing_method != "card":
        return line
    from docflow_core import card_billing

    timezone = session.execute(
        text("SELECT timezone FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
    ).scalar()
    trial_end = (
        datetime.fromtimestamp(billing.trial_end, tz=UTC)
        if billing.trial_end
        else datetime.now(UTC) + timedelta(days=TRIAL_PERIOD_DAYS)
    )
    return line.format(
        trial_end_date=card_billing.local_date(trial_end, timezone),
        first_charge_amount=card_billing.money(int(first_charge_amount(plan) * 100)),
        support_email=get_settings().support_email.strip(),
        cancel_by_date=card_billing.cancel_by_date(trial_end, timezone),
        cancel_outcome=CANCEL_OUTCOMES[card_trial_case(plan)],
    )


def schedule_trial_ending_reminder(
    session: Session, tenant_id: UUID, plan: GoLivePlan, billing: GoLiveBilling
) -> UUID | None:
    """
    The card-billed owner's email TRIAL_ENDING_REMINDER_DAYS_BEFORE days
    before the trial ends (founder, 2026-09-29; migration 0032). Its run time
    is computed by the database from Stripe's trial end (D-170), falling back,
    as the go-live email does, to TRIAL_PERIOD_DAYS from now. The amounts are
    fixed here, from the plan the go-live email and Stripe's invoice used.
    None (nothing scheduled) unless the go-live is by card.
    """
    if plan.billing_method != "card":
        return None
    case = card_trial_case(plan)
    founding_rate = bool(plan.founding_price and plan.promo_monthly_price)
    month = plan.promo_monthly_price if founding_rate and plan.promo_monthly_price else plan.monthly_price
    fee = plan.setup_fee_on_first_invoice
    job_id = uuid4()
    session.execute(
        text(
            """
            WITH t AS (
                SELECT coalesce(to_timestamp(CAST(:trial_end AS double precision)),
                                now() + make_interval(days => :trial_days)) AS trial_end_at
            )
            INSERT INTO scheduled_jobs (id, tenant_id, job_type, run_at, payload, dedupe_key)
            SELECT CAST(:id AS uuid), CAST(:tenant_id AS uuid), 'trial_ending_reminder',
                   t.trial_end_at - make_interval(days => :before),
                   CAST(:payload AS jsonb) || jsonb_build_object('trial_end_at', t.trial_end_at),
                   'trial_ending_reminder:' || CAST(:tenant_id AS text)
              FROM t
            ON CONFLICT (dedupe_key) DO NOTHING
            """
        ),
        {
            "id": str(job_id),
            "tenant_id": str(tenant_id),
            "trial_end": billing.trial_end,
            "trial_days": TRIAL_PERIOD_DAYS,
            "before": TRIAL_ENDING_REMINDER_DAYS_BEFORE,
            # Whole cents, never a float (Section 3).
            "payload": json.dumps(
                {
                    "template": f"trial_ending_{case}",
                    "first_charge_cents": int(first_charge_amount(plan) * 100),
                    "month_cents": int(month * 100),
                    "standard_month_cents": int(plan.monthly_price * 100),
                    "founding_rate": founding_rate,
                    "promo_days": plan.promo_days,
                    "setup_fee_cents": int(fee * 100) if fee else None,
                }
            ),
        },
    )
    return job_id


def schedule_first_week_checkin(session: Session, tenant_id: UUID) -> UUID:
    job_id = uuid4()
    session.execute(
        text(
            """
            INSERT INTO scheduled_jobs (id, tenant_id, job_type, run_at, dedupe_key)
            VALUES (:id, :tenant_id, 'first_week_checkin', now() + make_interval(days => :days), :dedupe)
            ON CONFLICT (dedupe_key) DO NOTHING
            """
        ),
        # run_at is the database's: the job sweep compares it with now()
        # (D-170 #4).
        {
            "id": str(job_id),
            "tenant_id": str(tenant_id),
            "days": FIRST_WEEK_CHECKIN_DAYS,
            "dedupe": f"first_week_checkin:{tenant_id}",
        },
    )
    return job_id
