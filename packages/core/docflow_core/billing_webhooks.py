"""
Stripe webhook processing (CLAUDE.md Section 7.12: "Stripe webhooks verify
signatures and are idempotent on event ID"). The router
(apps/api/app/routers/stripe_webhooks.py) is a thin adapter -- signature
verification and event handling live here, exactly like email_intake's split
between the router and docflow_core.email_intake.

Handles every event whose payload carries a subscription object
(customer.subscription.created/updated/deleted all do) uniformly, by its
current `status` -- there's no need to branch on the event type name.

**One transaction per decision, and the event id recorded with its effect**
(review H11, D-173). Before Stage 2c the event id was committed as seen in one
transaction and the update applied in another, so a database blip between
them turned Stripe's retry into a "duplicate" and lost the update for good;
and event order was ignored. Now:

1. A SELECT-only lookup (`stripe_webhook_session`) finds the tenant by its
   Stripe customer id.
2. Inside that tenant's own `tenant_session()`, the database function
   `record_stripe_subscription_event()` (migration 0029) checks the event id,
   applies the ordering guard and saves the status -- recording the event id
   in the same transaction as the write. The past-due alert is raised in that
   same transaction.
3. An event in the same `created` second as the saved state cannot be ordered
   (Stripe's timestamps have one-second resolution). The first transaction
   records nothing; the subscription is then fetched from Stripe **with no
   transaction open** -- holding the tenant row locked across a network call is
   the `change_tier` bug, not repeated -- and a second short transaction saves
   what Stripe said. The function re-checks the ordering guard there: if a
   newer event was saved while the fetch was in flight, the fetched state is
   not written over it.

The event's own time, never this server's clock, is what is compared and what
starts the non-payment cure clock (D-170). The ordering guard compares Stripe's
time only with Stripe's time, so it needs no tolerance. Where Stripe's time
meets ours -- the cure clock, and a stamp from the future -- the tolerance is
STRIPE_CLOCK_TOLERANCE_SECONDS, the same bound the signature check already
enforces (D-176).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import card_billing, external_services, founder_alerts
from docflow_core.constants import STRIPE_CLOCK_TOLERANCE_SECONDS
from docflow_core.db import stripe_webhook_session, tenant_session

logger = logging.getLogger(__name__)

PAST_DUE_ALERT_STATUSES = ("past_due", "unpaid")


def process_event(event: dict[str, Any]) -> str:
    """
    Returns an outcome word for logging and the response:
      applied     the event's state was saved
      refetched   same-second event: Stripe's current state was fetched and saved
      duplicate   this event id was already recorded
      stale       older than the saved state; recorded as seen, not applied
      superseded  same-second event whose fetch was overtaken by a newer event
      unmatched   no tenant has this Stripe customer
      ignored     not a subscription event, or missing an id or timestamp
      future_dated  stamped further into the future than STRIPE_CLOCK_TOLERANCE_SECONDS
                  by the database's clock; not applied or recorded, founder alerted
    Raises only when the database or Stripe cannot be reached -- the router
    then answers 500 and Stripe retries, and nothing has been recorded.
    """
    event_id = event.get("id")
    event_type = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    if not event_id:
        return "ignored"
    # Card billing (founder, 2026-09-29; migration 0031).
    if event_type == "checkout.session.completed":
        return _card_page_completed(event_id, event_type, obj)
    if event_type == "customer.updated":
        return _card_updated(event_id, event_type, obj, (event.get("data") or {}).get("previous_attributes"))
    if obj.get("object") != "subscription":
        return "ignored"
    created = _event_created(event)
    if created is None:
        # Every real Stripe event carries `created`. Without it the event
        # cannot be ordered, and it is not guessed at.
        logger.warning("stripe_webhook_ignored event=%s reason=no_created_time", event_id)
        return "ignored"

    customer_id = obj.get("customer")
    if not isinstance(customer_id, str) or not customer_id:
        return "unmatched"
    tenant_id = _tenant_for_customer(customer_id)
    if tenant_id is None:
        return "unmatched"

    # The amount a failed charge was for, named in a card-billed owner's
    # past-due email. Fetched here, with no transaction open (never hold one
    # across a Stripe call, D-173); a failure answers 500 and Stripe retries.
    # Only a card-billed owner gets that email, so only then is Stripe asked.
    amount_cents = None
    if obj.get("status") == "past_due" and obj.get("id") and _billed_by_card(tenant_id):
        amount_cents = external_services.latest_invoice_amount_cents(str(obj["id"]))

    outcome = _record(
        tenant_id,
        event_id=event_id,
        event_type=event_type,
        created=created,
        customer_id=customer_id,
        status=obj.get("status"),
        period_end=_period_end(obj),
        mode="event",
        amount_cents=amount_cents,
    )
    if outcome != "same_second":
        return outcome

    # No transaction is open here, by construction: _record's has committed.
    fetched = external_services.retrieve_subscription(str(obj.get("id")))
    outcome = _record(
        tenant_id,
        event_id=event_id,
        event_type=event_type,
        created=created,
        customer_id=customer_id,
        status=fetched.status,
        period_end=_timestamp(fetched.current_period_end),
        mode="fetched",
        amount_cents=amount_cents,
    )
    return "refetched" if outcome == "applied" else outcome


def _tenant_for_customer(customer_id: str) -> UUID | None:
    with stripe_webhook_session() as session:
        row = session.execute(
            text("SELECT id FROM tenants WHERE stripe_customer_id = :cust"),
            {"cust": customer_id},
        ).first()
    return UUID(str(row[0])) if row is not None else None


def _billed_by_card(tenant_id: UUID) -> bool:
    with tenant_session(tenant_id) as session:
        method = session.execute(
            text("SELECT billing_method FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
        ).scalar()
    return method == "card"


def _record(
    tenant_id: UUID,
    *,
    event_id: str,
    event_type: str,
    created: datetime,
    customer_id: str,
    status: str | None,
    period_end: datetime | None,
    mode: str,
    amount_cents: int | None = None,
) -> str:
    with tenant_session(tenant_id) as session:
        if mode == "event" and _beyond_clock_tolerance(session, created):
            # Not a real Stripe event (every accepted delivery proves Stripe's
            # clock is within the tolerance of ours), and saving its time would
            # make every genuine event "stale" until our clock caught up. Not
            # applied, not recorded -- so a corrected redelivery still applies
            # -- and the founder is told, because the tenant's billing state is
            # now waiting on someone.
            logger.warning("stripe_webhook_future_dated event=%s tenant=%s", event_id, tenant_id)
            founder_alerts.raise_alert(
                session,
                alert_type="stripe_event_future_dated",
                severity="high",
                tenant_id=tenant_id,
                payload={"event_id": event_id},
                dedupe_key=f"stripe_event_future_dated:{tenant_id}",
            )
            return "future_dated"
        was_past_due = session.execute(
            text("SELECT first_past_due_at IS NOT NULL FROM tenants WHERE id = :id"),
            {"id": str(tenant_id)},
        ).scalar()
        outcome = session.execute(
            text(
                "SELECT record_stripe_subscription_event("
                ":event_id, :event_type, :created, :customer_id, :status, :period_end, :mode)"
            ),
            {
                "event_id": event_id,
                "event_type": event_type,
                "created": created,
                "customer_id": customer_id,
                "status": status,
                "period_end": period_end,
                "mode": mode,
            },
        ).scalar_one()
        if outcome == "applied" and status in PAST_DUE_ALERT_STATUSES:
            founder_alerts.raise_alert(
                session,
                alert_type="stripe_subscription_past_due",
                severity="high",
                tenant_id=tenant_id,
                payload={"status": status},
                dedupe_key=f"stripe_subscription_past_due:{tenant_id}",
            )
        if outcome == "applied" and status == "past_due" and not was_past_due:
            # The first past_due of this episode: a card-billed owner is
            # emailed now and reminded before the date they may be paused from
            # (same transaction as the status, so neither is lost or doubled).
            card_billing.on_newly_past_due(session, tenant_id, amount_cents=amount_cents)
    return str(outcome)


def _card_page_completed(event_id: str, event_type: str, obj: dict[str, Any]) -> str:
    """A customer finished DocFlow's card page: record the saved card, and a
    setup fee paid at signing (D1, D2). The session is read back from Stripe
    rather than trusted from the event body (external_services.
    complete_card_page), with no transaction open."""
    if obj.get("object") != "checkout.session" or not (obj.get("metadata") or {}).get("docflow_card_page"):
        return "ignored"
    customer_id = obj.get("customer")
    if not isinstance(customer_id, str) or not customer_id:
        return "unmatched"
    tenant_id = _tenant_for_customer(customer_id)
    if tenant_id is None:
        return "unmatched"
    completed = external_services.complete_card_page(str(obj.get("id")))
    if completed is None:
        return "ignored"
    if completed.tenant_id != str(tenant_id):
        # The page was made for another tenant than the customer's owner:
        # never recorded against either.
        logger.warning("stripe_card_page_tenant_mismatch event=%s tenant=%s", event_id, tenant_id)
        return "unmatched"
    return card_billing.record_card_event(
        tenant_id,
        event_id=event_id,
        event_type=event_type,
        customer_id=customer_id,
        kind=completed.kind,
        amount_cents=completed.amount_paid_cents,
    )


def _card_updated(
    event_id: str, event_type: str, obj: dict[str, Any], previous: dict[str, Any] | None
) -> str:
    """The customer's default card changed (the portal's "update payment
    method", or DocFlow's own card page). A card-billed tenant that is past due
    has its open invoices charged to the new card now (founder, 2026-09-29):
    Stripe itself stops retrying after its final attempt. Paying comes first
    and the event is recorded after, so a failure part-way leaves the event
    unrecorded and Stripe's re-delivery pays again -- a paid invoice counts as
    success (external_services.pay_open_invoices)."""
    if obj.get("object") != "customer" or "invoice_settings" not in (previous or {}):
        return "ignored"
    new_card = (obj.get("invoice_settings") or {}).get("default_payment_method")
    customer_id = obj.get("id")
    if not new_card or not isinstance(customer_id, str):
        return "ignored"
    tenant_id = _tenant_for_customer(customer_id)
    if tenant_id is None:
        return "unmatched"
    with tenant_session(tenant_id) as session:
        tenant = session.execute(
            text(
                "SELECT billing_method, stripe_subscription_status, stripe_subscription_id "
                "FROM tenants WHERE id = :id"
            ),
            {"id": str(tenant_id)},
        ).mappings().one()
    if (
        tenant["billing_method"] == "card"
        and tenant["stripe_subscription_status"] in card_billing.PAST_DUE_STATUSES
        and tenant["stripe_subscription_id"]
    ):
        result = external_services.pay_open_invoices(str(tenant["stripe_subscription_id"]))
        logger.info(
            "stripe_card_updated_paid tenant=%s paid=%s declined=%s needs_customer=%s",
            tenant_id,
            result.paid,
            result.declined,
            result.needs_customer,
        )
    return card_billing.record_card_event(
        tenant_id,
        event_id=event_id,
        event_type=event_type,
        customer_id=customer_id,
        kind="card",
        amount_cents=None,
    )


def _beyond_clock_tolerance(session: Session, created: datetime) -> bool:
    # Compared by the database, on the database's clock (D-170).
    return bool(
        session.execute(
            text("SELECT CAST(:created AS timestamptz) > now() + make_interval(secs => :tolerance)"),
            {"created": created, "tolerance": STRIPE_CLOCK_TOLERANCE_SECONDS},
        ).scalar_one()
    )


def _event_created(event: dict[str, Any]) -> datetime | None:
    created = event.get("created")
    return _timestamp(created) if isinstance(created, int) else None


def _timestamp(unix_seconds: int | None) -> datetime | None:
    return datetime.fromtimestamp(unix_seconds, tz=UTC) if unix_seconds else None


def _period_end(obj: dict[str, Any]) -> datetime | None:
    # Newer Stripe API versions moved the period onto the subscription item
    # (see external_services._subscription_result, the same fallback).
    period_end = obj.get("current_period_end")
    if period_end is None:
        items = (obj.get("items") or {}).get("data") or []
        period_end = items[0].get("current_period_end") if items else None
    return _timestamp(period_end)
