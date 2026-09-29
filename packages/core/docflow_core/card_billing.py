"""
Card billing with a 7-day trial (founder, 2026-09-29; decisions D1-D6 in
docs/BUILD-STATUS.md; D-181): what happens in DocFlow's database when Stripe
reports a card saved, a setup fee paid, or a card-billed subscription gone
past due. The Stripe calls themselves are in external_services; the webhook
routing is in billing_webhooks.

Nothing here pauses anything. A past-due tenant keeps working; the date in
the owner's email and banner is the earliest date the founder may confirm a
non-payment cancel (first past-due notice + CURE_PERIOD_DAYS), so the wording
says processing "may be paused from" that date, never that it will (founder,
2026-09-29). Every date is computed by the database, from Stripe's event time
already saved (D-170), and shown in the tenant's own timezone.

Only card-billed tenants get the banner, the two emails and the charge after a
card update: invoice billing has no card on file, so none of it applies there.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import email_outbox, founder_alerts
from docflow_core.config import get_settings
from docflow_core.constants import CURE_PERIOD_DAYS, PAST_DUE_REMINDER_DAYS_BEFORE
from docflow_core.db import tenant_session
from docflow_core.errors import ErrorCatalogEntry, render_error

logger = logging.getLogger(__name__)

PAST_DUE_STATUSES = ("past_due", "unpaid")


def _local_date(moment: datetime, timezone: str | None) -> str:
    """'October 20, 2026', in the tenant's own timezone."""
    try:
        zone = ZoneInfo(timezone or "UTC")
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    local = moment.astimezone(zone)
    return f"{local:%B} {local.day}, {local.year}"


def _money(cents: int | None) -> str:
    if cents is None:
        return "your subscription payment"
    return f"${Decimal(cents) / 100:,.2f}"


def _billing_url() -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/billing"


def _owner_email(session: Session, tenant_id: UUID) -> str | None:
    row = session.execute(
        text(
            "SELECT email FROM users WHERE tenant_id = :id AND role = 'owner' AND deleted_at IS NULL "
            "ORDER BY created_at LIMIT 1"
        ),
        {"id": str(tenant_id)},
    ).first()
    return str(row[0]) if row is not None else None


def _past_due_state(session: Session, tenant_id: UUID) -> dict[str, Any] | None:
    """The tenant's past-due facts, with the date it may be paused from and the
    reminder's run time -- both computed here, on the database's clock."""
    row = session.execute(
        text(
            """
            SELECT name, timezone, billing_method, stripe_subscription_status, first_past_due_at,
                   first_past_due_at + make_interval(days => :cure) AS may_pause_at,
                   first_past_due_at + make_interval(days => :cure)
                       - make_interval(days => :before) AS remind_at,
                   first_past_due_at + make_interval(days => :cure) <= now() AS date_passed
              FROM tenants WHERE id = :id
            """
        ),
        {"id": str(tenant_id), "cure": CURE_PERIOD_DAYS, "before": PAST_DUE_REMINDER_DAYS_BEFORE},
    ).mappings().first()
    return dict(row) if row is not None else None


def on_newly_past_due(session: Session, tenant_id: UUID, *, amount_cents: int | None) -> None:
    """
    Called in the webhook's transaction, right after it saved a card-billed
    subscription's first `past_due` of this episode: the owner's email now, and
    the reminder PAST_DUE_REMINDER_DAYS_BEFORE days before the date the tenant
    may be paused from. Invoice-billed tenants get neither (see module docstring).
    """
    state = _past_due_state(session, tenant_id)
    if state is None or state["billing_method"] != "card" or state["first_past_due_at"] is None:
        return
    owner = _owner_email(session, tenant_id)
    if owner is not None:
        email_outbox.enqueue(
            session,
            tenant_id=tenant_id,
            to_address=owner,
            template="payment_failed",
            params={
                "amount": _money(amount_cents),
                "failed_date": _local_date(state["first_past_due_at"], state["timezone"]),
                "billing_url": _billing_url(),
                "suspension_date": _local_date(state["may_pause_at"], state["timezone"]),
            },
            related_type="tenant",
            related_id=tenant_id,
        )
    session.execute(
        text(
            """
            INSERT INTO scheduled_jobs (id, tenant_id, job_type, run_at, payload, dedupe_key)
            SELECT :id, t.id, 'past_due_reminder',
                   t.first_past_due_at + make_interval(days => :cure) - make_interval(days => :before),
                   jsonb_build_object('amount_cents', CAST(:amount AS bigint),
                                      'first_past_due_at', t.first_past_due_at),
                   'past_due_reminder:' || CAST(t.id AS text) || ':'
                       || CAST(extract(epoch FROM t.first_past_due_at) AS text)
              FROM tenants t WHERE t.id = :tenant_id
            ON CONFLICT (dedupe_key) DO NOTHING
            """
        ),
        {
            "id": str(uuid4()),
            "tenant_id": str(tenant_id),
            "cure": CURE_PERIOD_DAYS,
            "before": PAST_DUE_REMINDER_DAYS_BEFORE,
            "amount": amount_cents,
        },
    )


def send_past_due_reminder(session: Session, job: Any) -> None:
    """The `past_due_reminder` job. Sends nothing if the payment has gone
    through, the tenant is no longer card-billed, or this reminder belongs to
    an earlier past-due episode (its first notice differs from the saved one)."""
    assert job.tenant_id is not None
    state = _past_due_state(session, job.tenant_id)
    if (
        state is None
        or state["billing_method"] != "card"
        or state["stripe_subscription_status"] not in PAST_DUE_STATUSES
        or state["first_past_due_at"] is None
    ):
        return
    same_episode = session.execute(
        text("SELECT CAST(:saved AS timestamptz) = CAST(:scheduled AS timestamptz)"),
        {"saved": state["first_past_due_at"], "scheduled": job.payload.get("first_past_due_at")},
    ).scalar_one()
    if not same_episode:
        return
    owner = _owner_email(session, job.tenant_id)
    if owner is None:
        return
    amount = job.payload.get("amount_cents")
    email_outbox.enqueue(
        session,
        tenant_id=job.tenant_id,
        to_address=owner,
        template="payment_failed_reminder",
        params={
            "amount": _money(int(amount) if amount is not None else None),
            "billing_url": _billing_url(),
            "suspension_date": _local_date(state["may_pause_at"], state["timezone"]),
        },
        related_type="scheduled_job",
        related_id=job.id,
    )


def past_due_banner(session: Session, tenant_id: UUID) -> ErrorCatalogEntry | None:
    """BIL-006 before the date the tenant may be paused from, BIL-009 on and
    after it (or when no first notice was saved to count from). None unless
    the tenant is card-billed and past due."""
    state = _past_due_state(session, tenant_id)
    if (
        state is None
        or state["billing_method"] != "card"
        or state["stripe_subscription_status"] not in PAST_DUE_STATUSES
    ):
        return None
    if state["first_past_due_at"] is None or state["date_passed"]:
        return render_error("BIL-009")
    return render_error("BIL-006", suspension_date=_local_date(state["may_pause_at"], state["timezone"]))


def record_card_event(
    tenant_id: UUID,
    *,
    event_id: str,
    event_type: str,
    customer_id: str,
    kind: str,
    amount_cents: int | None,
) -> str:
    """Migration 0031's record_stripe_card_event(), in the tenant's own session,
    with the founder alert its outcome calls for in the same transaction."""
    with tenant_session(tenant_id) as session:
        outcome = str(
            session.execute(
                text("SELECT record_stripe_card_event(:event_id, :event_type, :customer_id, :kind, :amount)"),
                {
                    "event_id": event_id,
                    "event_type": event_type,
                    "customer_id": customer_id,
                    "kind": kind,
                    "amount": amount_cents,
                },
            ).scalar_one()
        )
        if outcome in ("already_paid", "amount_mismatch"):
            alert_type = f"setup_fee_{outcome}"
            founder_alerts.raise_alert(
                session,
                alert_type=alert_type,
                severity="high",
                tenant_id=tenant_id,
                payload={"event_id": event_id, "amount_paid": _money(amount_cents)},
                dedupe_key=f"{alert_type}:{event_id}",
            )
    logger.info("stripe_card_event tenant=%s kind=%s outcome=%s", tenant_id, kind, outcome)
    return outcome


def fee_due_at_signing(tenant: Mapping[str, Any]) -> Decimal | None:
    """The setup fee DocFlow's card page charges now, if any (D2): a standard
    customer -- not the Founding box (D-117) -- whose fee is billed through
    Stripe, isn't zero, isn't paid yet, and who isn't live (after go-live the
    fee is already on an invoice, or was paid here). Shared by the owner's
    Billing page and the Console's "Ask for a card", so they can't disagree."""
    fee = tenant["setup_fee_amount"]
    if (
        tenant["founding_price"]
        or tenant["setup_fee_billing"] != "stripe"
        or fee is None
        or Decimal(fee) <= 0
        or tenant["setup_fee_paid_at"] is not None
        or tenant["onboarding_status"] == "live"
    ):
        return None
    return Decimal(fee)


def request_card(session: Session, tenant_id: UUID) -> tuple[UUID | None, str]:
    """The Console's "Ask for a card": email the owner a link to their Billing
    page, worded for when the setup fee is charged. Returns the outbox row (None
    if the tenant has no owner yet) and the template used."""
    tenant = session.execute(
        text(
            """
            SELECT name, onboarding_status, setup_fee_amount, setup_fee_billing,
                   setup_fee_paid_at, founding_price
              FROM tenants WHERE id = :id
            """
        ),
        {"id": str(tenant_id)},
    ).mappings().one()
    at_signing = fee_due_at_signing(dict(tenant))
    fee = tenant["setup_fee_amount"]
    if at_signing is not None:
        template, setup_fee = "card_request_at_signing", at_signing
    elif (
        tenant["founding_price"]
        and tenant["setup_fee_billing"] == "stripe"
        and fee is not None
        and Decimal(fee) > 0
        and tenant["setup_fee_paid_at"] is None
    ):
        template, setup_fee = "card_request_founding", Decimal(fee)
    else:
        template, setup_fee = "card_request_no_fee", None
    owner = _owner_email(session, tenant_id)
    if owner is None:
        return None, template
    params: dict[str, Any] = {"tenant_name": tenant["name"], "billing_url": _billing_url()}
    if setup_fee is not None:
        params["setup_fee"] = _money(int(setup_fee * 100))
    outbox_id = email_outbox.enqueue(
        session,
        tenant_id=tenant_id,
        to_address=owner,
        template=template,
        params=params,
        related_type="tenant",
        related_id=tenant_id,
    )
    return outbox_id, template
