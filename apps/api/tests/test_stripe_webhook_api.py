# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
The inbound Stripe webhook (CLAUDE.md Section 7.12; D-123), against the real
database and RLS.

What this proves: a signature is required and verified; the same event id
processed twice only syncs once (idempotency); the tenant is found by
stripe_customer_id, never trusted from anywhere else; the subscription
status and period end land on that tenant; the first past-due timestamp is
recorded once and cleared on recovery; a past_due/unpaid status raises the
founder alert.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from docflow_core.config import get_settings
from docflow_core.db import platform_session
from sqlalchemy import text

from tests.test_console_api import _Console, _environment, _scalar, stripe  # noqa: F401
from tests.test_lifecycle_api import requires_lifecycle_schema  # noqa: F401

WEBHOOK_SECRET = "whsec_test_fixed"


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _post(client, event: dict):
    payload = json.dumps(event).encode()
    ts = int(time.time())
    sig = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/stripe", content=payload, headers={"Stripe-Signature": f"t={ts},v1={sig}"}
    )


def _event_id(label: str) -> str:
    """`stripe_webhook_events` is global and idempotency-keyed on this id
    (Section 7.12) -- there's no tenant to scope a cleanup to, and it's
    never wiped between runs against the real staging database, so a fixed
    literal id would collide with the row its own previous run left behind.
    Unique per call instead; the tiny leftover rows are harmless, exactly
    like admin_actions or tenant_lifecycle_events already accumulating."""
    return f"evt_test_{label}_{uuid4().hex[:12]}"


def _subscription_event(*, event_id: str, customer: str, status: str, period_end: int | None = None) -> dict:
    return {
        "id": event_id,
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "object": "subscription",
                "id": "sub_evt",
                "customer": customer,
                "status": status,
                "current_period_end": period_end,
            }
        },
    }


def test_a_request_with_no_signature_is_refused(client, _environment):
    response = client.post("/webhooks/stripe", content=b"{}")
    assert response.status_code == 400


def test_an_incorrectly_signed_request_is_refused(client, _environment):
    response = client.post(
        "/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "t=1,v1=deadbeef"}
    )
    assert response.status_code == 400


@requires_lifecycle_schema
def test_an_event_for_an_unknown_customer_is_processed_but_matches_nothing(client, _environment):
    response = _post(
        client, _subscription_event(event_id=_event_id("unmatched"), customer="cus_nobody", status="active")
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "unmatched"


@requires_lifecycle_schema
def test_the_same_event_id_twice_only_syncs_once(client, stripe, _environment):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        customer = _scalar("SELECT stripe_customer_id FROM tenants WHERE id = :t", t=tenant_id)

        event = _subscription_event(event_id=_event_id("dup"), customer=customer, status="past_due")
        first = _post(client, event)
        assert first.status_code == 200 and first.json()["outcome"] == "processed"
        second = _post(client, event)
        assert second.status_code == 200 and second.json()["outcome"] == "duplicate"

        alerts = _scalar(
            "SELECT count(*) FROM founder_alerts WHERE tenant_id = :t "
            "AND type = 'stripe_subscription_past_due'",
            t=tenant_id,
        )
        assert alerts == 1  # not raised twice


@requires_lifecycle_schema
def test_past_due_records_the_first_notice_once_and_clears_it_on_recovery(client, stripe, _environment):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        customer = _scalar("SELECT stripe_customer_id FROM tenants WHERE id = :t", t=tenant_id)

        _post(client, _subscription_event(event_id=_event_id("pd1"), customer=customer, status="past_due"))
        first_notice = _scalar("SELECT first_past_due_at FROM tenants WHERE id = :t", t=tenant_id)
        assert first_notice is not None

        # A second past_due event (still failing) does not move the clock.
        _post(client, _subscription_event(event_id=_event_id("pd2"), customer=customer, status="past_due"))
        assert _scalar("SELECT first_past_due_at FROM tenants WHERE id = :t", t=tenant_id) == first_notice

        # Recovery clears it.
        period_end = int((datetime.now(UTC) + timedelta(days=30)).timestamp())
        _post(
            client,
            _subscription_event(event_id=_event_id("pd3"), customer=customer, status="active", period_end=period_end),
        )
        assert _scalar("SELECT first_past_due_at FROM tenants WHERE id = :t", t=tenant_id) is None
        assert _scalar("SELECT stripe_subscription_status FROM tenants WHERE id = :t", t=tenant_id) == "active"
