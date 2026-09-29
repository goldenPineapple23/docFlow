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
        self.invoices: list[dict] = []
        # Stripe keeps an idempotency key for 24 hours: the same key with the
        # same parameters replays the first answer; with different parameters
        # it is refused (400 idempotency_error).
        self.idempotency: dict[str, tuple[dict, httpx.Response]] = {}
        self.keys_used: list[str] = []

    def __call__(self, method, url, *, auth, data=None, params=None, headers=None, timeout=None):
        path = url.removeprefix("https://api.stripe.com/v1/")
        self.requests.append((method, path, dict(data or params or {})))
        key = (headers or {}).get("Idempotency-Key")
        if key:
            self.keys_used.append(key)
            if key in self.idempotency:
                first_data, first_response = self.idempotency[key]
                if first_data != data:
                    return httpx.Response(400, json={"error": {"type": "idempotency_error"}})
                return first_response
            response = self._handle(method, path, data, params)
            self.idempotency[key] = (data, response)
            return response
        return self._handle(method, path, data, params)

    def _handle(self, method, path, data, params):
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
        if method == "GET" and path == "invoices":
            mine = [i for i in self.invoices if i.get("subscription") == params["subscription"]]
            start = 0
            if "starting_after" in params:
                start = next(n for n, i in enumerate(mine) if i["id"] == params["starting_after"]) + 1
            page = mine[start : start + params["limit"]]
            return httpx.Response(200, json={"data": page, "has_more": start + len(page) < len(mine)})
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
        idempotency_scope="golive",
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


@pytest.mark.parametrize("status", ["canceled", "incomplete_expired"])
def test_an_ended_subscription_is_not_reused(stripe, status):
    stripe.subscriptions.append(
        {"id": "sub_old", "status": status, "metadata": {"docflow_tenant_id": str(TENANT)}}
    )
    result = _start()
    assert result.subscription_id != "sub_old"
    assert result.reused is False


REACTIVATION = dict(
    setup_fee=None, promo_monthly_price=None, promo_months=None, idempotency_scope="reactivate-1"
)


@pytest.mark.parametrize("status", ["active", "trialing", "past_due", "unpaid"])
def test_any_subscription_stripe_has_not_ended_is_reused_and_no_second_one_is_created(stripe, status):
    """Founder, 2026-09-29: reuse any status Stripe hasn't ended. A second
    subscription beside a live one would bill the customer twice."""
    stripe.subscriptions.append(
        {
            "id": "sub_old",
            "status": status,
            "metadata": {"docflow_tenant_id": str(TENANT)},
            "items": {"data": [{"current_period_end": 1_790_000_000}]},
        }
    )
    result = _start(**REACTIVATION)
    assert (result.subscription_id, result.status, result.reused) == ("sub_old", status, True)
    assert not any(method == "POST" and path == "subscriptions" for method, path, _ in stripe.requests)


def test_a_reactivation_within_24_hours_of_go_live_gets_its_own_key_and_succeeds(stripe):
    """Stage 3a. Go-live and reactivation used one fixed key per tenant;
    within Stripe's 24 hours, the reactivation's different parameters were
    refused and reactivation failed. Each action now has its own key."""
    went_live = _start(trial_end=1_795_000_000)
    stripe.subscriptions[0]["status"] = "canceled"  # the suspension's cancel went through

    reactivated = _start(**REACTIVATION)

    assert reactivated.subscription_id != went_live.subscription_id
    assert reactivated.reused is False
    subscription_keys = [k for k in stripe.keys_used if k.startswith("docflow-subscription-")]
    assert subscription_keys == [
        f"docflow-subscription-{TENANT}-golive",
        f"docflow-subscription-{TENANT}-reactivate-1",
    ]


def test_one_key_for_both_actions_is_what_failed(stripe):
    """The fake refuses a reused key with different parameters, as Stripe
    does -- so the test above would fail with one key for both actions."""
    _start(trial_end=1_795_000_000, idempotency_scope="same")
    stripe.subscriptions[0]["status"] = "canceled"
    with pytest.raises(es.ExternalServiceError):
        _start(**{**REACTIVATION, "idempotency_scope": "same"})


def test_a_retried_reactivation_reuses_its_key_and_bills_once(stripe):
    first = _start(**REACTIVATION)
    stripe.subscriptions.clear()  # even if the listing missed it, the key still protects
    second = _start(**REACTIVATION)
    assert second.subscription_id == first.subscription_id


def test_a_reactivation_key_names_the_suspension_it_ends():
    from datetime import UTC, datetime

    from docflow_core.lifecycle import ReactivatePlan

    def plan(changed):
        return ReactivatePlan(
            tenant_name="Acme Test Co",
            customer_id="cus_test",
            tier_id=TIER,
            tier_name="Starter",
            monthly_price=Decimal("299.00"),
            document_allowance=300,
            status_changed_at=changed,
        )

    one = datetime(2026, 9, 29, 12, 0, 0, 123456, tzinfo=UTC)
    assert plan(one).idempotency_scope == plan(one).idempotency_scope
    assert plan(one).idempotency_scope != plan(one.replace(microsecond=123457)).idempotency_scope
    assert plan(one).idempotency_scope.startswith("reactivate-")


# -- invoices_to_review (Stage 3a, option C) ----------------------------------

SUSPENDED_AT = 1_790_000_000
DAY = 86_400


def _invoice(n, status, created, *, due=0, remaining=0, paid=0, sub="sub_old"):
    return {
        "id": f"in_{n}",
        "number": f"ACME-{n:04d}",
        "subscription": sub,
        "status": status,
        "created": created,
        "amount_due": due,
        "amount_remaining": remaining,
        "amount_paid": paid,
    }


def test_invoices_are_sorted_into_before_and_during_the_suspension(stripe):
    stripe.invoices = [
        _invoice(1, "paid", SUSPENDED_AT - 60 * DAY, due=29900, paid=29900),  # settled: not listed
        _invoice(2, "open", SUSPENDED_AT - 30 * DAY, due=29900, remaining=29900),  # collect
        _invoice(3, "void", SUSPENDED_AT - 20 * DAY, due=29900),  # nothing owed: not listed
        _invoice(4, "draft", SUSPENDED_AT + 1 * DAY, due=29900),
        _invoice(5, "open", SUSPENDED_AT + 2 * DAY, due=29900, remaining=14950),
        _invoice(6, "paid", SUSPENDED_AT + 3 * DAY, due=29900, paid=29900),
        _invoice(7, "uncollectible", SUSPENDED_AT + 4 * DAY, due=29900),  # nothing owed: not listed
        _invoice(8, "open", SUSPENDED_AT + DAY, due=100, remaining=100, sub="sub_other"),  # another sub
    ]

    review = es.invoices_to_review("sub_old", SUSPENDED_AT)

    assert [(i.invoice_id, i.status, i.amount) for i in review.before_suspension] == [
        ("in_2", "open", Decimal("299.00"))
    ]
    assert [(i.invoice_id, i.status, i.amount) for i in review.during_suspension] == [
        ("in_4", "draft", Decimal("299.00")),
        ("in_5", "open", Decimal("149.50")),  # what is still owed on it
        ("in_6", "paid", Decimal("299.00")),
    ]


def test_invoices_to_review_reads_every_page(stripe):
    stripe.invoices = [_invoice(n, "draft", SUSPENDED_AT + n, due=100) for n in range(1, 251)]
    review = es.invoices_to_review("sub_old", SUSPENDED_AT)
    assert len(review.during_suspension) == 250


def test_an_invoice_list_failure_raises(monkeypatch):
    monkeypatch.setattr(es, "_stripe_key", lambda: "sk_test_fake")
    monkeypatch.setattr(es.httpx, "request", lambda *a, **k: httpx.Response(500, json={}))
    with pytest.raises(es.ExternalServiceError):
        es.invoices_to_review("sub_old", SUSPENDED_AT)


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
