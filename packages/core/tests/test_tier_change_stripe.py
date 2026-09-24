"""
A live customer's plan change against a fake Stripe (slice 5.9, D-138): the
subscription's item moves to the new tier's price with proration, and a
founding customer keeps the NEW tier's founding price for exactly what is left
of their 90 days -- the founder's option (a); docflow-pricing.docx: "first 90
days". An old tier's founding coupon never keeps discounting a new price.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from docflow_core import external_services as es

TENANT = uuid4()
GROWTH = uuid4()
NOW = 1_790_000_000
DAY = 86_400


class FakeStripe:
    def __init__(self, discount_end: int | None, coupon: str = "docflow_founding_old", shape: str = "dahlia"):
        self.discount_end = discount_end
        self.coupon = coupon
        self.shape = shape
        self.objects: dict[str, dict] = {}
        self.updates: list[dict] = []

    def __call__(self, method, url, *, auth, data=None, params=None, headers=None, timeout=None):
        path = url.removeprefix("https://api.stripe.com/v1/")
        if method == "POST" and path in ("products", "coupons"):
            # Real Stripe refuses names over 40 characters; so does this fake.
            if len(str(data.get("name", ""))) > 40:
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "code": "parameter_invalid_string",
                            "param": "name",
                            "message": "Invalid string: must be at most 40 characters",
                        }
                    },
                )
            if data["id"] in self.objects:
                return httpx.Response(400, json={"error": {"code": "resource_already_exists"}})
            self.objects[data["id"]] = data
            return httpx.Response(200, json={"id": data["id"]})
        if method == "GET" and path == "subscriptions/sub_1":
            # The next invoice is 20 days away, as on a real subscription.
            body: dict = {
                "id": "sub_1",
                "status": "active",
                "items": {"data": [{"id": "si_1", "current_period_end": NOW + 20 * DAY}]},
                "discounts": [],
            }
            if self.discount_end:
                body.update(_discount_shape(self.shape, self.coupon, self.discount_end))
            return httpx.Response(200, json=body)
        if method == "POST" and path == "subscriptions/sub_1":
            self.updates.append(dict(data))
            return httpx.Response(
                200,
                json={
                    "id": "sub_1",
                    "status": "active",
                    "items": {"data": [{"current_period_end": NOW + 30 * DAY}]},
                },
            )
        return httpx.Response(500, json={})


def _discount_shape(shape: str, coupon: str, end: int) -> dict:
    """The discount as each Stripe API version returns it. "dahlia" is copied
    from the real sandbox's answer (API 2026-08-26.dahlia): the coupon id sits
    under `source`. The fake once used only the old shape, the tests passed,
    and the real plan change missed the founding discount (D-138)."""
    if shape == "dahlia":
        return {
            "discounts": [{"object": "discount", "end": end, "source": {"coupon": coupon, "type": "coupon"}}]
        }
    if shape == "expanded_coupon":
        return {"discounts": [{"object": "discount", "end": end, "coupon": {"id": coupon}}]}
    if shape == "legacy_single":
        return {"discount": {"object": "discount", "end": end, "coupon": {"id": coupon}}}
    raise ValueError(shape)


def _stripe(monkeypatch, fake: FakeStripe) -> FakeStripe:
    monkeypatch.setattr(es.httpx, "request", fake)
    monkeypatch.setattr(es, "_stripe_key", lambda: "sk_test_fake")
    return fake


def _change(founding: bool, promo: Decimal | None = Decimal("249.00")):
    return es.change_subscription_tier(
        tenant_id=TENANT,
        subscription_id="sub_1",
        tier_id=GROWTH,
        tier_name="Growth",
        monthly_price=Decimal("399.00"),
        promo_monthly_price=promo,
        founding=founding,
        now=NOW,
    )


@pytest.mark.parametrize(
    ("days_left", "months"),
    [(0, 0), (-5, 0), (1, 1), (30, 1), (31, 2), (60, 2), (61, 3), (89, 3)],
)
def test_founding_months_left_rounds_up_so_nobody_loses_a_month(days_left, months):
    end = NOW + days_left * DAY if days_left else None
    assert es.founding_months_left(end, NOW) == months


def test_a_plain_customer_moves_to_the_new_price_with_proration(monkeypatch):
    fake = _stripe(monkeypatch, FakeStripe(discount_end=None))
    result, months = _change(founding=False)

    [update] = fake.updates
    assert update["items[0][id]"] == "si_1"  # the existing item, not a second one
    assert update["items[0][price_data][unit_amount]"] == 39900
    assert update["proration_behavior"] == "create_prorations"
    assert not any(k.startswith("discounts") for k in update)
    assert (result.status, months) == ("active", 0)


def test_a_founding_customer_keeps_the_new_tiers_founding_price_for_the_time_left(monkeypatch):
    fake = _stripe(monkeypatch, FakeStripe(discount_end=NOW + 75 * DAY))
    _, months = _change(founding=True)

    assert months == 2
    [update] = fake.updates
    coupon_id = update["discounts[0][coupon]"]
    coupon = fake.objects[coupon_id]
    # Growth's founding saving (399 - 249), not Starter's, for the months left.
    assert coupon["amount_off"] == 15000
    assert (coupon["duration"], coupon["duration_in_months"]) == ("repeating", 2)


def test_a_founding_period_that_has_ended_leaves_no_discount_behind(monkeypatch):
    fake = _stripe(monkeypatch, FakeStripe(discount_end=NOW - DAY))
    _, months = _change(founding=True)

    assert months == 0
    [update] = fake.updates
    # The old tier's coupon is cleared rather than left discounting a price it
    # was never sized for.
    assert update["discounts"] == ""


def test_a_new_tier_with_no_founding_price_clears_the_old_one(monkeypatch):
    fake = _stripe(monkeypatch, FakeStripe(discount_end=NOW + 75 * DAY))
    _, months = _change(founding=True, promo=None)

    assert months == 0
    assert fake.updates[0]["discounts"] == ""


def test_a_stripe_refusal_is_an_error_not_a_silent_success(monkeypatch):
    class Refusing(FakeStripe):
        def __call__(self, method, url, **kwargs):
            if method == "POST" and url.endswith("subscriptions/sub_1"):
                return httpx.Response(402, json={})
            return super().__call__(method, url, **kwargs)

    _stripe(monkeypatch, Refusing(discount_end=None))
    with pytest.raises(es.ExternalServiceError):
        _change(founding=False)


@pytest.mark.parametrize("shape", ["dahlia", "expanded_coupon", "legacy_single"])
def test_the_founding_discount_is_found_in_every_shape_stripe_returns(monkeypatch, shape):
    fake = _stripe(monkeypatch, FakeStripe(discount_end=NOW + 75 * DAY, shape=shape))
    _, months = _change(founding=True)
    assert months == 2
    assert fake.updates[0]["discounts[0][coupon]"].startswith("docflow_founding_")


def test_a_discount_that_is_not_docflows_founding_coupon_is_left_alone(monkeypatch):
    fake = _stripe(monkeypatch, FakeStripe(discount_end=NOW + 75 * DAY, coupon="someone_elses_coupon"))
    _, months = _change(founding=True)
    assert months == 0
    assert not any(k.startswith("discounts") for k in fake.updates[0])


def test_the_real_sandbox_answer_is_read_as_founding_until_december():
    # Verbatim fields from the sandbox subscription read on 2026-09-24.
    sub = {
        "discount": None,
        "discounts": [
            {
                "id": "di_test",
                "object": "discount",
                "end": 1797649119,
                "source": {"coupon": "docflow_founding_4116ee9fdc0b4ffc88b8bd3e97e41256", "type": "coupon"},
                "start": 1789786719,
            }
        ],
    }
    assert es._founding_discount_end(sub) == 1797649119


def test_coupon_names_fit_stripes_forty_character_limit(monkeypatch):
    """The real refusal from the 5.9 walkthrough: a 47-character coupon name."""
    fake = _stripe(monkeypatch, FakeStripe(discount_end=NOW + 80 * DAY))
    es.change_subscription_tier(
        tenant_id=TENANT,
        subscription_id="sub_1",
        tier_id=GROWTH,
        tier_name="Growth",
        monthly_price=Decimal("399.00"),
        promo_monthly_price=Decimal("249.00"),
        founding=True,
        now=NOW,
    )
    names = [o["name"] for o in fake.objects.values()]
    assert names and all(len(n) <= 40 for n in names)
    assert es.stripe_name("x" * 60) == "x" * 39 + "…"


def test_a_refusal_names_the_rule_broken_but_not_stripes_text(monkeypatch):
    class Refusing(FakeStripe):
        def __call__(self, method, url, **kwargs):
            if method == "POST" and url.endswith("subscriptions/sub_1"):
                return httpx.Response(
                    400,
                    json={
                        "error": {"code": "resource_missing", "param": "discounts", "message": "secret 4242"}
                    },
                )
            return super().__call__(method, url, **kwargs)

    _stripe(monkeypatch, Refusing(discount_end=None))
    with pytest.raises(es.ExternalServiceError) as raised:
        _change(founding=False)
    assert "resource_missing discounts" in raised.value.reason
    assert "4242" not in str(raised.value)


def test_a_plan_change_owes_only_the_founding_invoices_still_to_come():
    """Acme Test Prospect in the 5.9 walkthrough, with Stripe's own numbers:
    founding discount from 1789786719 (19 Sep 02:58:39 UTC) to 1797649119
    (19 Dec 02:58:39), already applied to the 19 Sep invoice; plan changed on
    24 Sep; next invoice one month after the start. Owed: 19 Oct and 19 Nov --
    two, not three. 19 Dec falls exactly on the end, so it is full price.
    (Rounding the time left up had added 19 Dec.)"""
    from datetime import datetime, timezone

    start, end = 1789786719, 1797649119
    changed = int(datetime(2026, 9, 24, 19, 48, tzinfo=timezone.utc).timestamp())
    next_invoice = es._add_months(start, 1)
    assert es.founding_months_left(end, changed, next_invoice) == 2
    # Straight after go-live, the first invoice is the first of three.
    assert es.founding_months_left(end, start, start) == 3
    # A period that ends on the 31st bills on the last day of shorter months.
    jan31 = int(datetime(2026, 1, 31, 12, tzinfo=timezone.utc).timestamp())
    feb28 = int(datetime(2026, 2, 28, 12, tzinfo=timezone.utc).timestamp())
    assert es._add_months(jan31, 1) == feb28
