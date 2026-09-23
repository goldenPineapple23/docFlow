"""
Stripe webhook processing (CLAUDE.md Section 7.12: "Stripe webhooks verify
signatures and are idempotent on event ID"). The router
(apps/api/app/routers/stripe_webhooks.py) is a thin adapter -- signature
verification and event handling live here, exactly like email_intake's split
between the router and docflow_core.email_intake.

Nothing kept `stripe_subscription_status` / `stripe_current_period_end` in
sync after go-live before this slice -- they were set once and never
touched again (config.py's STRIPE_WEBHOOK_SECRET had sat unused since
5.3 for exactly this gap; see D-123). This also tracks the first past-due
timestamp Section 7.15.4's non-payment cancellation rule needs.

Handles every event whose payload carries a subscription object
(customer.subscription.created/updated/deleted all do) uniformly, by its
current `status` -- there's no need to branch on the event type name.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from docflow_core import founder_alerts
from docflow_core.db import stripe_webhook_session, tenant_session

PAST_DUE_ALERT_STATUSES = ("past_due", "unpaid")


def process_event(event: dict[str, Any]) -> str:
    """Returns an outcome string for logging: 'duplicate', 'ignored',
    'unmatched' or 'processed'. Never raises for a well-formed but
    irrelevant event -- only a malformed payload is the caller's problem."""
    event_id = event.get("id")
    event_type = event.get("type", "")
    if not event_id:
        return "ignored"

    obj = (event.get("data") or {}).get("object") or {}
    with stripe_webhook_session() as session:
        inserted = session.execute(
            text(
                "INSERT INTO stripe_webhook_events (id, type) VALUES (:id, :type) "
                "ON CONFLICT (id) DO NOTHING RETURNING id"
            ),
            {"id": event_id, "type": event_type},
        ).first()
        if inserted is None:
            return "duplicate"
        if obj.get("object") != "subscription":
            return "ignored"
        customer_id = obj.get("customer")
        tenant_row = (
            session.execute(
                text("SELECT id FROM tenants WHERE stripe_customer_id = :cust"),
                {"cust": customer_id},
            ).first()
            if customer_id
            else None
        )

    if tenant_row is None:
        return "unmatched"
    tenant_id = UUID(str(tenant_row[0]))
    _sync_subscription(tenant_id, obj)
    return "processed"


def _period_end(obj: dict[str, Any]) -> datetime | None:
    # Newer Stripe API versions moved the period onto the subscription item
    # (see external_services._subscription_result, the same fallback).
    period_end = obj.get("current_period_end")
    if period_end is None:
        items = (obj.get("items") or {}).get("data") or []
        period_end = items[0].get("current_period_end") if items else None
    return datetime.fromtimestamp(period_end, tz=UTC) if period_end else None


def _sync_subscription(tenant_id: UUID, obj: dict[str, Any]) -> None:
    status = obj.get("status")
    period_end = _period_end(obj)
    with tenant_session(tenant_id) as session:
        row = session.execute(
            text("SELECT first_past_due_at FROM tenants WHERE id = :id FOR UPDATE"),
            {"id": str(tenant_id)},
        ).mappings().first()
        if row is None:
            return
        first_past_due_at = row["first_past_due_at"]
        if status == "past_due" and first_past_due_at is None:
            first_past_due_at = datetime.now(UTC)
        elif status != "past_due":
            first_past_due_at = None  # cured, cancelled, or otherwise resolved
        session.execute(
            text(
                """
                UPDATE tenants SET
                    stripe_subscription_status = :status,
                    stripe_current_period_end = :period_end,
                    first_past_due_at = :first_past_due_at,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": str(tenant_id),
                "status": status,
                "period_end": period_end,
                "first_past_due_at": first_past_due_at,
            },
        )
        if status in PAST_DUE_ALERT_STATUSES:
            founder_alerts.raise_alert(
                session,
                alert_type="stripe_subscription_past_due",
                severity="high",
                tenant_id=tenant_id,
                payload={"status": status},
                dedupe_key=f"stripe_subscription_past_due:{tenant_id}",
            )
