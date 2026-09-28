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
starts the non-payment cure clock (D-170).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from docflow_core import external_services, founder_alerts
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
    Raises only when the database or Stripe cannot be reached -- the router
    then answers 500 and Stripe retries, and nothing has been recorded.
    """
    event_id = event.get("id")
    event_type = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    if not event_id or obj.get("object") != "subscription":
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

    outcome = _record(
        tenant_id,
        event_id=event_id,
        event_type=event_type,
        created=created,
        customer_id=customer_id,
        status=obj.get("status"),
        period_end=_period_end(obj),
        mode="event",
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
    )
    return "refetched" if outcome == "applied" else outcome


def _tenant_for_customer(customer_id: str) -> UUID | None:
    with stripe_webhook_session() as session:
        row = session.execute(
            text("SELECT id FROM tenants WHERE stripe_customer_id = :cust"),
            {"cust": customer_id},
        ).first()
    return UUID(str(row[0])) if row is not None else None


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
) -> str:
    with tenant_session(tenant_id) as session:
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
    return str(outcome)


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
