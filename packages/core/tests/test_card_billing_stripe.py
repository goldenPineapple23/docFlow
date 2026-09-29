"""
Card billing's Stripe calls against a fake Stripe (founder, 2026-09-29;
decisions D1-D6 in docs/BUILD-STATUS.md). The same calls were run against
Stripe test mode before these tests were written; these keep them honest in CI.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from docflow_core import external_services as es

TENANT = uuid4()


class FakeStripe:
    def __init__(self):
        self.requests: list[tuple[str, str, dict]] = []
        self.open_pages: list[dict] = []
        self.expired: list[str] = []
        self.sessions: dict[str, dict] = {}
        self.subscription_status = 200

    def __call__(self, method, url, *, auth, data=None, params=None, headers=None, timeout=None):
        path = url.removeprefix("https://api.stripe.com/v1/")
        self.requests.append((method, path, dict(data or params or {})))
        if method == "POST" and path == "checkout/sessions":
            page = {"id": "cs_new", "url": "https://checkout.example/cs_new", "mode": data["mode"]}
            return httpx.Response(200, json=page)
        if method == "GET" and path == "checkout/sessions":
            return httpx.Response(200, json={"data": self.open_pages})
        if method == "POST" and path.endswith("/expire"):
            self.expired.append(path.split("/")[2])
            return httpx.Response(200, json={})
        if method == "GET" and path.startswith("checkout/sessions/"):
            return httpx.Response(200, json=self.sessions[path.split("/")[-1]])
        if method == "POST" and path.startswith("customers/"):
            return httpx.Response(200, json={})
        if method == "POST" and path == "billing_portal/sessions":
            return httpx.Response(200, json={"url": "https://portal.example/s"})
        if method == "GET" and path in ("subscriptions", "invoiceitems"):
            return httpx.Response(200, json={"data": []})
        if method == "POST" and path in ("products", "coupons", "invoiceitems"):
            return httpx.Response(200, json={"id": "x"})
        if method == "POST" and path == "subscriptions":
            if self.subscription_status != 200:
                return httpx.Response(self.subscription_status, json={"error": {"type": "card_error"}})
            return httpx.Response(200, json={"id": "sub_1", "status": "trialing", "items": {"data": []}})
        raise AssertionError(f"unexpected Stripe call {method} {path}")

    def posted(self, path: str) -> list[dict]:
        return [d for m, p, d in self.requests if m == "POST" and p == path]


@pytest.fixture()
def stripe(monkeypatch):
    fake = FakeStripe()
    monkeypatch.setattr(es.httpx, "request", fake)
    monkeypatch.setattr(es, "_stripe_key", lambda: "sk_test_fake")
    return fake


def _page(stripe, fee):
    return es.create_card_page(
        tenant_id=TENANT, customer_id="cus_1", setup_fee=fee, success_url="https://a/ok", cancel_url="https://a/no"
    )


# ── The card page (D1, D2) ───────────────────────────────────────────────────


@pytest.mark.parametrize("fee", [None, Decimal("0.00")])
def test_no_fee_to_charge_saves_the_card_and_charges_nothing(stripe, fee):
    page = _page(stripe, fee)
    (sent,) = stripe.posted("checkout/sessions")
    assert page.mode == "setup" and sent["mode"] == "setup"
    assert sent["metadata[docflow_card_page]"] == "card"
    assert not any(k.startswith("line_items") for k in sent)
    # A setup page charges nothing, so older pages are left alone.
    assert not any(p == "checkout/sessions" and m == "GET" for m, p, _ in stripe.requests)


def test_a_fee_at_signing_charges_exactly_the_fee_and_saves_the_card(stripe):
    page = _page(stripe, Decimal("1500.00"))
    (sent,) = stripe.posted("checkout/sessions")
    assert page.mode == "payment"
    assert sent["line_items[0][price_data][unit_amount]"] == 150000
    assert sent["payment_intent_data[setup_future_usage]"] == "off_session"
    assert sent["metadata[docflow_card_page]"] == "setup_fee"
    assert sent["metadata[docflow_tenant_id]"] == str(TENANT)


def test_a_new_fee_page_expires_every_docflow_page_still_open(stripe):
    stripe.open_pages = [
        {"id": "cs_old_fee", "metadata": {"docflow_card_page": "setup_fee"}},
        {"id": "cs_old_card", "metadata": {"docflow_card_page": "card"}},
        {"id": "cs_not_ours", "metadata": {}},
    ]
    _page(stripe, Decimal("1500.00"))
    assert stripe.expired == ["cs_old_fee", "cs_old_card"]


def test_a_fraction_of_a_cent_is_refused_not_rounded(stripe):
    with pytest.raises(es.ExternalServiceError):
        _page(stripe, Decimal("1500.005"))


# ── Reading a completed page ─────────────────────────────────────────────────


def _session(**over):
    base = {
        "status": "complete",
        "mode": "setup",
        "customer": "cus_1",
        "metadata": {"docflow_card_page": "card", "docflow_tenant_id": str(TENANT)},
        "setup_intent": {"payment_method": "pm_1"},
    }
    base.update(over)
    return base


def test_a_completed_card_page_makes_its_card_the_customer_default(stripe):
    stripe.sessions["cs_1"] = _session()
    done = es.complete_card_page("cs_1")
    assert done is not None and done.kind == "card" and done.amount_paid_cents is None
    assert stripe.posted("customers/cus_1") == [{"invoice_settings[default_payment_method]": "pm_1"}]


def test_a_paid_fee_page_reports_what_stripe_collected(stripe):
    stripe.sessions["cs_2"] = _session(
        mode="payment",
        payment_status="paid",
        amount_total=150000,
        metadata={"docflow_card_page": "setup_fee", "docflow_tenant_id": str(TENANT)},
        payment_intent={"payment_method": {"id": "pm_2"}},
    )
    done = es.complete_card_page("cs_2")
    assert done is not None and done.kind == "setup_fee" and done.amount_paid_cents == 150000
    assert done.payment_method_id == "pm_2"


@pytest.mark.parametrize(
    "over",
    [
        {"status": "open"},
        {"metadata": {}},
        {"mode": "payment", "payment_status": "unpaid", "metadata": {"docflow_card_page": "setup_fee"},
         "payment_intent": {"payment_method": "pm_3"}},
    ],
    ids=["not completed", "not a DocFlow page", "fee not paid"],
)
def test_anything_else_changes_nothing(stripe, over):
    stripe.sessions["cs_3"] = _session(**over)
    assert es.complete_card_page("cs_3") is None
    assert stripe.posted("customers/cus_1") == []


# ── The subscription (D5, D6) ────────────────────────────────────────────────


def _subscribe(**kw):
    return es.start_subscription(
        tenant_id=TENANT,
        customer_id="cus_1",
        tier_id=uuid4(),
        tier_name="Starter",
        monthly_price=Decimal("299.00"),
        promo_monthly_price=None,
        promo_months=None,
        setup_fee=None,
        days_until_due=15,
        idempotency_scope="golive",
        **kw,
    )


def test_invoice_billing_is_unchanged(stripe):
    _subscribe()
    (sent,) = stripe.posted("subscriptions")
    assert sent["collection_method"] == "send_invoice" and sent["days_until_due"] == 15
    assert "payment_behavior" not in sent


def test_card_billing_charges_the_default_card_automatically(stripe):
    _subscribe(charge_card=True)
    (sent,) = stripe.posted("subscriptions")
    assert sent["collection_method"] == "charge_automatically"
    assert "days_until_due" not in sent and "payment_behavior" not in sent


def test_a_reactivation_with_a_declined_card_is_refused(stripe):
    stripe.subscription_status = 402
    with pytest.raises(es.CardDeclined):
        _subscribe(charge_card=True, refuse_if_declined=True)
    (sent,) = stripe.posted("subscriptions")
    assert sent["payment_behavior"] == "error_if_incomplete"


def test_the_update_card_page_opens_on_the_card_step(stripe):
    assert es.create_card_update_page(customer_id="cus_1", return_url="https://a/billing") == "https://portal.example/s"
    (sent,) = stripe.posted("billing_portal/sessions")
    assert sent["flow_data[type]"] == "payment_method_update"
