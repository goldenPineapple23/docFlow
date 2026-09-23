"""
Go-live billing against a fake Stripe (DECISIONS.md D-113). The function must
be safe to call again after a partial failure -- a retried go-live must never
bill a customer twice -- and must bill exactly the tier's price.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from docflow_core import external_services as es

TENANT = uuid4()
TIER = uuid4()


class FakeStripe:
    def __init__(self):
        self.objects: dict[str, dict] = {}
        self.subscriptions: list[dict] = []
        self.invoice_items: list[dict] = []
        self.requests: list[tuple[str, str, dict]] = []

    def __call__(self, method, url, *, auth, data=None, params=None, headers=None, timeout=None):
        path = url.removeprefix("https://api.stripe.com/v1/")
        self.requests.append((method, path, dict(data or params or {})))
        if method == "POST" and path in ("products", "coupons"):
            if data["id"] in self.objects:
                return httpx.Response(400, json={"error": {"code": "resource_already_exists"}})
            self.objects[data["id"]] = data
            return httpx.Response(200, json={"id": data["id"]})
        if method == "GET" and path == "subscriptions":
            return httpx.Response(200, json={"data": self.subscriptions})
        if method == "GET" and path == "invoiceitems":
            return httpx.Response(200, json={"data": self.invoice_items})
        if method == "POST" and path == "invoiceitems":
            item = {
                "id": f"ii_{len(self.invoice_items) + 1}",
                "metadata": {"docflow_setup_fee_for": data["metadata[docflow_setup_fee_for]"]},
                **data,
            }
            self.invoice_items.append(item)
            return httpx.Response(200, json=item)
        if method == "DELETE" and path.startswith("invoiceitems/"):
            item_id = path.removeprefix("invoiceitems/")
            before = len(self.invoice_items)
            self.invoice_items = [i for i in self.invoice_items if i["id"] != item_id]
            if len(self.invoice_items) == before:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"id": item_id, "deleted": True})
        if method == "POST" and path == "subscriptions":
            sub = {
                "id": f"sub_{len(self.subscriptions) + 1}",
                "status": "active",
                "metadata": {"docflow_tenant_id": data["metadata[docflow_tenant_id]"]},
                "items": {"data": [{"current_period_end": 1_790_000_000}]},
                "request": data,
            }
            self.subscriptions.append(sub)
            return httpx.Response(200, json=sub)
        return httpx.Response(500, json={})


@pytest.fixture
def stripe(monkeypatch):
    fake = FakeStripe()
    monkeypatch.setattr(es.httpx, "request", fake)
    monkeypatch.setattr(es, "_stripe_key", lambda: "sk_test_fake")
    return fake


def _start(**overrides):
    kwargs = dict(
        tenant_id=TENANT,
        customer_id="cus_test",
        tier_id=TIER,
        tier_name="Starter",
        monthly_price=Decimal("299.00"),
        promo_monthly_price=Decimal("199.00"),
        promo_months=3,
        setup_fee=Decimal("750.00"),
        days_until_due=14,
    )
    kwargs.update(overrides)
    return es.start_subscription(**kwargs)


def test_bills_the_tier_price_by_invoice_with_the_setup_fee_and_founding_coupon(stripe):
    result = _start()

    assert result.subscription_id == "sub_1" and result.status == "active"
    assert result.current_period_end == 1_790_000_000
    (sub,) = stripe.subscriptions
    request = sub["request"]
    assert request["collection_method"] == "send_invoice"
    assert request["days_until_due"] == 14
    assert request["items[0][price_data][unit_amount]"] == 29900
    assert request["items[0][price_data][product]"] == f"docflow_tier_{TIER.hex}"
    assert request["discounts[0][coupon]"] == f"docflow_founding_{TIER.hex}"
    coupon = stripe.objects[f"docflow_founding_{TIER.hex}"]
    assert coupon["amount_off"] == 10000 and coupon["duration_in_months"] == 3
    (fee,) = stripe.invoice_items
    assert fee["amount"] == 75000


def test_no_trial_by_default(stripe):
    _start()
    assert "trial_end" not in stripe.subscriptions[0]["request"]


def test_a_trial_end_delays_billing_without_splitting_the_invoice(stripe):
    """D-125: the setup fee is still added as a pending item, same as
    always -- Stripe (not this code) is what holds it until the trial ends
    and sweeps it onto the first real invoice alongside month one."""
    result = _start(trial_end=1_795_000_000)

    assert result.status == "active"  # the fake always returns "active"; Stripe itself would say "trialing"
    assert stripe.subscriptions[0]["request"]["trial_end"] == 1_795_000_000
    (fee,) = stripe.invoice_items
    assert fee["amount"] == 75000  # unchanged: still one pending item, not billed twice


def test_a_retry_after_success_reuses_the_subscription_and_never_bills_twice(stripe):
    first = _start()
    second = _start()

    assert second.subscription_id == first.subscription_id
    assert len(stripe.subscriptions) == 1
    assert len(stripe.invoice_items) == 1


def test_a_retry_after_the_fee_but_before_the_subscription_does_not_add_the_fee_again(stripe):
    stripe.invoice_items.append({"metadata": {"docflow_setup_fee_for": str(TENANT)}, "amount": 75000})
    _start()
    assert len(stripe.invoice_items) == 1
    assert len(stripe.subscriptions) == 1


def test_no_setup_fee_and_no_promo_means_neither_is_sent(stripe):
    _start(setup_fee=None, promo_monthly_price=None, promo_months=None)
    assert stripe.invoice_items == []
    assert "discounts[0][coupon]" not in stripe.subscriptions[0]["request"]
    assert not any(path == "coupons" for _, path, _ in stripe.requests)


def test_a_zero_setup_fee_is_not_an_invoice_line(stripe):
    _start(setup_fee=Decimal("0.00"))
    assert stripe.invoice_items == []


def test_a_cancelled_subscription_is_not_reused(stripe):
    stripe.subscriptions.append(
        {"id": "sub_old", "status": "canceled", "metadata": {"docflow_tenant_id": str(TENANT)}}
    )
    result = _start()
    assert result.subscription_id != "sub_old"


def test_fractions_of_a_cent_are_refused_never_rounded(stripe):
    with pytest.raises(es.ExternalServiceError):
        _start(setup_fee=Decimal("750.005"))
    assert stripe.subscriptions == []


def test_a_stripe_error_is_raised_as_a_service_error_without_its_text(stripe, monkeypatch):
    def refuse(*args, **kwargs):
        return httpx.Response(402, json={"error": {"message": "card details echo here"}})

    monkeypatch.setattr(es.httpx, "request", refuse)
    with pytest.raises(es.ExternalServiceError) as raised:
        _start()
    assert "card details" not in str(raised.value)


# ── cancel_subscription (D-123: "on entering suspended") ────────────────────


def test_cancel_subscription_deletes_it(monkeypatch):
    monkeypatch.setattr(es, "_stripe_key", lambda: "sk_test_fake")
    calls: list[tuple[str, str]] = []

    def fake(method, url, **kwargs):
        calls.append((method, url))
        return httpx.Response(200, json={})

    monkeypatch.setattr(es.httpx, "request", fake)
    es.cancel_subscription("sub_1")
    assert calls == [("DELETE", "https://api.stripe.com/v1/subscriptions/sub_1")]


def test_cancelling_an_already_gone_subscription_is_not_an_error(monkeypatch):
    monkeypatch.setattr(es, "_stripe_key", lambda: "sk_test_fake")
    monkeypatch.setattr(es.httpx, "request", lambda *a, **k: httpx.Response(404, json={}))
    es.cancel_subscription("sub_gone")  # must not raise


def test_a_failed_cancel_raises(monkeypatch):
    monkeypatch.setattr(es, "_stripe_key", lambda: "sk_test_fake")
    monkeypatch.setattr(es.httpx, "request", lambda *a, **k: httpx.Response(500, json={}))
    with pytest.raises(es.ExternalServiceError):
        es.cancel_subscription("sub_1")


# ── void_pending_setup_fee (D-125: cancelled mid-trial, never invoiced) ─────


def test_void_pending_setup_fee_deletes_only_this_tenants_item(stripe):
    _start(setup_fee=Decimal("750.00"))  # pending, this tenant
    other_tenant = uuid4()
    stripe.invoice_items.append(
        {"id": "ii_other", "metadata": {"docflow_setup_fee_for": str(other_tenant)}, "amount": 150000}
    )

    es.void_pending_setup_fee(customer_id="cus_test", tenant_id=TENANT)

    remaining = {i["metadata"]["docflow_setup_fee_for"] for i in stripe.invoice_items}
    assert remaining == {str(other_tenant)}


def test_void_pending_setup_fee_with_nothing_pending_is_not_an_error(stripe):
    es.void_pending_setup_fee(customer_id="cus_test", tenant_id=TENANT)  # must not raise
    assert stripe.invoice_items == []


def test_void_pending_setup_fee_list_failure_raises(monkeypatch):
    monkeypatch.setattr(es, "_stripe_key", lambda: "sk_test_fake")
    monkeypatch.setattr(es.httpx, "request", lambda *a, **k: httpx.Response(500, json={}))
    with pytest.raises(es.ExternalServiceError):
        es.void_pending_setup_fee(customer_id="cus_test", tenant_id=TENANT)


# ── verify_webhook_signature (Section 7.12) ─────────────────────────────────


def _signed(payload: bytes, secret: str, *, timestamp: int | None = None) -> str:
    import hashlib
    import hmac
    import time

    ts = timestamp if timestamp is not None else int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_a_correctly_signed_payload_is_parsed():
    payload = b'{"id": "evt_1", "type": "customer.subscription.updated"}'
    header = _signed(payload, "whsec_test")
    event = es.verify_webhook_signature(payload, header, "whsec_test")
    assert event["id"] == "evt_1"


def test_a_wrong_secret_is_refused():
    payload = b'{"id": "evt_1"}'
    header = _signed(payload, "whsec_test")
    with pytest.raises(es.ExternalServiceError):
        es.verify_webhook_signature(payload, header, "whsec_other")


def test_a_tampered_payload_is_refused():
    payload = b'{"id": "evt_1"}'
    header = _signed(payload, "whsec_test")
    with pytest.raises(es.ExternalServiceError):
        es.verify_webhook_signature(payload + b" ", header, "whsec_test")


def test_an_old_timestamp_is_refused_even_with_a_correct_signature():
    """Prevents a captured event from being replayed later (Section 7.12:
    webhook content is untrusted until proven otherwise)."""
    import time

    payload = b'{"id": "evt_1"}'
    header = _signed(payload, "whsec_test", timestamp=int(time.time()) - 3600)
    with pytest.raises(es.ExternalServiceError):
        es.verify_webhook_signature(payload, header, "whsec_test")


def test_a_malformed_header_is_refused():
    with pytest.raises(es.ExternalServiceError):
        es.verify_webhook_signature(b"{}", "not-a-valid-header", "whsec_test")
