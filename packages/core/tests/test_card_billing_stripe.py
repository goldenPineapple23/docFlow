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
        tenant_id=TENANT,
        customer_id="cus_1",
        setup_fee=fee,
        success_url="https://a/ok",
        cancel_url="https://a/no",
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
        {
            "mode": "payment",
            "payment_status": "unpaid",
            "metadata": {"docflow_card_page": "setup_fee"},
            "payment_intent": {"payment_method": "pm_3"},
        },
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
        days_until_due=15,
        idempotency_scope="golive",
        **{"setup_fee": None, **kw},
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
    assert (
        es.create_card_update_page(customer_id="cus_1", return_url="https://a/billing")
        == "https://portal.example/s"
    )
    (sent,) = stripe.posted("billing_portal/sessions")
    assert sent["flow_data[type]"] == "payment_method_update"


# ── Charging the open invoice after a card update (founder, 2026-09-29) ──────


class InvoiceStripe:
    """A fake with open invoices whose pay requests answer as scripted."""

    def __init__(self, answers: dict[str, tuple[int, dict]], status_after: dict[str, str]):
        self.answers = answers
        self.status_after = status_after
        self.paid_requests: list[str] = []

    def __call__(self, method, url, *, auth, data=None, params=None, headers=None, timeout=None):
        path = url.removeprefix("https://api.stripe.com/v1/")
        if method == "GET" and path == "invoices":
            return httpx.Response(200, json={"data": [{"id": i} for i in self.answers]})
        if method == "POST" and path.endswith("/pay"):
            invoice_id = path.split("/")[1]
            self.paid_requests.append(invoice_id)
            status, body = self.answers[invoice_id]
            return httpx.Response(status, json=body)
        if method == "GET" and path.startswith("invoices/"):
            return httpx.Response(200, json={"status": self.status_after[path.split("/")[1]]})
        raise AssertionError(f"unexpected Stripe call {method} {path}")


def _pay(monkeypatch, answers, status_after=None):
    fake = InvoiceStripe(answers, status_after or {})
    monkeypatch.setattr(es.httpx, "request", fake)
    monkeypatch.setattr(es, "_stripe_key", lambda: "sk_test_fake")
    return es.pay_open_invoices("sub_1"), fake


def test_paying_the_open_invoice_charges_the_new_card(monkeypatch):
    result, fake = _pay(monkeypatch, {"in_1": (200, {"status": "paid"})})
    assert result == es.OpenInvoicesPaid(paid=1, declined=0, needs_customer=0)
    assert fake.paid_requests == ["in_1"]


def test_an_invoice_stripes_own_retry_already_paid_counts_as_success(monkeypatch):
    # Stripe's real answer, test mode 2026-09-29: a plain 400, no error code.
    already = (400, {"error": {"type": "invalid_request_error", "message": "Invoice is already paid"}})
    result, _ = _pay(monkeypatch, {"in_1": already}, {"in_1": "paid"})
    assert result == es.OpenInvoicesPaid(paid=1, declined=0, needs_customer=0)


@pytest.mark.parametrize("status", [409, 429])
def test_an_invoice_being_paid_by_another_request_counts_as_success(monkeypatch, status):
    result, _ = _pay(monkeypatch, {"in_1": (status, {"error": {"code": "lock_timeout"}})})
    assert result == es.OpenInvoicesPaid(paid=1, declined=0, needs_customer=0)


def test_a_declined_new_card_is_an_outcome_not_an_error(monkeypatch):
    declined = (402, {"error": {"type": "card_error", "code": "card_declined"}})
    result, _ = _pay(monkeypatch, {"in_1": declined}, {"in_1": "open"})
    assert result == es.OpenInvoicesPaid(paid=0, declined=1, needs_customer=0)


def test_a_card_needing_the_customer_is_an_outcome_not_an_error(monkeypatch):
    action = (402, {"error": {"type": "card_error", "code": "authentication_required"}})
    result, _ = _pay(monkeypatch, {"in_1": action}, {"in_1": "open"})
    assert result == es.OpenInvoicesPaid(paid=0, declined=0, needs_customer=1)


def test_stripe_failing_otherwise_raises_so_the_event_is_redelivered(monkeypatch):
    with pytest.raises(es.ExternalServiceError):
        _pay(monkeypatch, {"in_1": (500, {})}, {"in_1": "open"})


# ── When the setup fee is added (test mode, 2026-09-29) ──────────────────────
# A charge_automatically subscription on a trial gets a $0 first invoice at
# once, and Stripe sweeps pending items onto it and charges them then: added
# before, a founding customer's fee was charged at go-live. Added after and
# tied to the subscription, it comes with month one at the trial's end.


def _calls(stripe) -> list[tuple[str, dict]]:
    return [(p, d) for m, p, d in stripe.requests if m == "POST" and p in ("subscriptions", "invoiceitems")]


def test_by_card_the_fee_is_added_after_the_subscription_and_tied_to_it(stripe):
    _subscribe(charge_card=True, setup_fee=Decimal("750.00"), trial_end=1_900_000_000)
    (first, _), (second, item) = _calls(stripe)
    assert (first, second) == ("subscriptions", "invoiceitems")
    assert item["subscription"] == "sub_1" and item["amount"] == 75000


def test_by_invoice_the_fee_is_still_added_before_the_subscription(stripe):
    _subscribe(setup_fee=Decimal("750.00"), trial_end=1_900_000_000)
    (first, item), (second, _) = _calls(stripe)
    assert (first, second) == ("invoiceitems", "subscriptions")
    assert "subscription" not in item


def test_a_retried_card_go_live_adds_a_missing_fee_to_the_existing_subscription(stripe, monkeypatch):
    live = {
        "id": "sub_live",
        "status": "trialing",
        "metadata": {"docflow_tenant_id": str(TENANT)},
        "items": {"data": []},
    }
    original = stripe.__call__

    def with_live_subscription(method, url, **kw):
        if method == "GET" and url.endswith("/subscriptions"):
            return httpx.Response(200, json={"data": [live]})
        return original(method, url, **kw)

    monkeypatch.setattr(es.httpx, "request", with_live_subscription)
    result = _subscribe(charge_card=True, setup_fee=Decimal("750.00"))
    assert result.reused
    ((path, item),) = _calls(stripe)
    assert path == "invoiceitems" and item["subscription"] == "sub_live"


def test_a_fee_already_invoiced_is_never_added_again(stripe, monkeypatch):
    original = stripe.__call__

    def with_invoiced_fee(method, url, **kw):
        if method == "GET" and url.endswith("/invoiceitems"):
            invoiced = {"id": "ii_1", "invoice": "in_1", "metadata": {"docflow_setup_fee_for": str(TENANT)}}
            return httpx.Response(200, json={"data": [invoiced]})
        return original(method, url, **kw)

    monkeypatch.setattr(es.httpx, "request", with_invoiced_fee)
    _subscribe(charge_card=True, setup_fee=Decimal("750.00"))
    assert [p for p, _ in _calls(stripe)] == ["subscriptions"]
