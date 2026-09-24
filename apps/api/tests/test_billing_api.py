# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
Billing in the Console (slice 5.9) against the real database.

MRR counts paying customers only (founder's decision, D-135): a customer in
their free week (Stripe "trialing", D-125) is shown beside it, never in it.
Section 7.15.3 requires every dashboard figure to equal an independent
hand-written query; MRR had no such test until now.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import requires_console_schema
from tests.test_console_api import _Console, _environment, stripe  # noqa: F401 -- fixtures
from tests.test_lifecycle_api import _activate, requires_lifecycle_schema


def _founding_schema_available() -> bool:
    try:
        with platform_session() as session:
            session.execute(text("SELECT founding_price_ends_at FROM tenants LIMIT 0"))
        return True
    except Exception:
        return False


requires_founding_schema = pytest.mark.skipif(
    not _founding_schema_available(),
    reason="supabase/migrations/0024_founding_price_end.sql has not been applied yet -- see D-139.",
)


def _hand_written_money() -> tuple[Decimal, Decimal]:
    """MRR and the trial figure, written independently of the Console's query:
    what each customer pays now -- the founding price while it lasts (D-139)."""
    with platform_session() as session:
        rows = session.execute(
            text(
                "SELECT t.stripe_subscription_status AS s, tr.monthly_price AS p, "
                "tr.promo_monthly_price AS promo, t.founding_price_ends_at > now() AS founding_now "
                "FROM tenants t, tiers tr WHERE tr.id = t.tier_id "
                "AND t.status = 'active' AND t.deleted_at IS NULL"
            )
        ).all()

    def pays(price, promo, founding_now) -> Decimal:
        return Decimal(promo) if founding_now and promo is not None else Decimal(price)

    paying = sum((pays(p, promo, f) for s, p, promo, f in rows if s == "active"), Decimal("0"))
    trial = sum((pays(p, promo, f) for s, p, promo, f in rows if s == "trialing"), Decimal("0"))
    return paying, trial


def _money(client, console) -> dict:
    response = client.get("/admin/dashboard", headers=console.headers())
    assert response.status_code == 200, response.text
    return response.json()["money"]


@requires_console_schema
@requires_lifecycle_schema
@requires_founding_schema
def test_mrr_counts_paying_customers_and_shows_trials_beside_it(client, stripe):
    with _Console() as console:
        paying = console.create_tenant(client, name="Acme Test Paying").json()["tenant_id"]
        trial = console.create_tenant(client, name="Acme Test In Trial").json()["tenant_id"]
        _activate(paying, subscription_id="sub_paying", status="active")
        _activate(trial, subscription_id="sub_trial", status="trialing")

        money = _money(client, console)
        expected_mrr, expected_trial = _hand_written_money()
        assert Decimal(str(money["mrr"])) == expected_mrr
        assert Decimal(str(money["mrr_in_trial"])) == expected_trial

        # The trial moves into MRR the moment Stripe says it is paying.
        before = Decimal(str(money["mrr"]))
        _activate(trial, subscription_id="sub_trial", status="active")
        after = _money(client, console)
        with platform_session() as session:
            price = session.execute(
                text(
                    "SELECT tr.monthly_price FROM tenants t "
                    "JOIN tiers tr ON tr.id = t.tier_id WHERE t.id = :t"
                ),
                {"t": trial},
            ).scalar_one()
        assert Decimal(str(after["mrr"])) == before + Decimal(price)
        assert Decimal(str(after["mrr_in_trial"])) == expected_trial - Decimal(price)


@requires_console_schema
@requires_lifecycle_schema
def test_the_billing_card_shows_the_free_week_and_links_to_the_right_stripe(client, stripe, monkeypatch):
    from docflow_core import external_services
    from docflow_core.config import get_settings

    # A sandbox is its own account; the link must name it (5.9 walkthrough).
    monkeypatch.setattr(external_services, "_stripe_account_id", lambda key: "acct_test123")

    with _Console() as console:
        tenant_id = console.create_tenant(client, name="Acme Test Billing Card").json()["tenant_id"]
        _activate(tenant_id, subscription_id="sub_card", status="trialing")
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE tenants SET went_live_at = '2026-09-20T12:00:00Z', "
                    "first_past_due_at = NULL WHERE id = :t"
                ),
                {"t": tenant_id},
            )

        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
        get_settings.cache_clear()
        billing = client.get(f"/admin/tenants/{tenant_id}/overview", headers=console.headers()).json()[
            "tenant"
        ]["billing"]
        assert billing["status"] == "trialing"
        assert billing["subscription_id"] == "sub_card"
        # D-125: seven days from go-live.
        assert billing["trial_ends_at"].startswith("2026-09-27")
        assert billing["stripe_dashboard_url"].startswith(
            "https://dashboard.stripe.com/acct_test123/test/customers/cus_test_"
        )

        # A live key links to the live dashboard; a paying customer has no trial date.
        _activate(tenant_id, subscription_id="sub_card", status="active")
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_placeholder")
        get_settings.cache_clear()
        billing = client.get(f"/admin/tenants/{tenant_id}/overview", headers=console.headers()).json()[
            "tenant"
        ]["billing"]
        assert billing["trial_ends_at"] is None
        assert billing["stripe_dashboard_url"].startswith(
            "https://dashboard.stripe.com/acct_test123/customers/"
        )
        get_settings.cache_clear()


# ── Plan changes (Section 7.16.1; D-138) ────────────────────────────────────


class _FakePlanChange:
    """Stands in for `external_services.change_subscription_tier`."""

    def __init__(self):
        self.calls: list[dict] = []
        self.fail = False
        self.months = 0

    def __call__(self, **kwargs):
        from docflow_core import external_services

        if self.fail:
            raise external_services.ExternalServiceError("stripe", "test refusal")
        self.calls.append(kwargs)
        return external_services.SubscriptionResult(
            subscription_id=kwargs["subscription_id"], status="active", current_period_end=None
        ), self.months


def _plan_change(monkeypatch) -> _FakePlanChange:
    from docflow_core import external_services

    fake = _FakePlanChange()
    monkeypatch.setattr(external_services, "change_subscription_tier", fake)
    return fake


def _live(tenant_id: str, *, founding: bool = False) -> None:
    _activate(tenant_id, subscription_id="sub_plan", status="active")
    with platform_session() as session:
        session.execute(
            text(
                "UPDATE tenants SET onboarding_status = 'live', went_live_at = now(), "
                "setup_fee_billing = 'invoiced_manually', founding_price = :f WHERE id = :t"
            ),
            {"t": tenant_id, "f": founding},
        )


def _tier_of(tenant_id: str) -> str:
    with platform_session() as session:
        return session.execute(
            text("SELECT tr.code FROM tenants t JOIN tiers tr ON tr.id = t.tier_id WHERE t.id = :t"),
            {"t": tenant_id},
        ).scalar_one()


@requires_console_schema
@requires_lifecycle_schema
def test_a_plan_change_moves_the_subscription_and_the_allowance_together(client, stripe, monkeypatch):
    from docflow_core import usage
    from docflow_core.db import tenant_session

    fake = _plan_change(monkeypatch)
    fake.months = 2
    with _Console() as console:
        tenant_id = console.create_tenant(client, name="Acme Test Plan Change").json()["tenant_id"]
        _live(tenant_id, founding=True)

        response = client.post(
            f"/admin/tenants/{tenant_id}/tier", headers=console.headers(), json={"tier": "growth"}
        )
        assert response.status_code == 200, response.text
        change = response.json()["change"]
        assert (change["before"]["tier"], change["after"]["tier"]) == ("starter", "growth")
        assert change["founding_months_carried"] == 2
        # Prices are strings, never floats (Section 7).
        assert isinstance(change["after"]["monthly_price"], str)

        [call] = fake.calls
        assert (call["subscription_id"], call["founding"]) == ("sub_plan", True)
        assert call["monthly_price"] == Decimal(change["after"]["monthly_price"])

        # The allowance follows at once (7.16.1).
        assert _tier_of(tenant_id) == "growth"
        with tenant_session(tenant_id) as session:
            assert usage.allowance_for(session, tenant_id).allowance == change["after"]["document_allowance"]

        with platform_session() as session:
            events = session.execute(
                text(
                    "SELECT count(*) FROM tenant_lifecycle_events "
                    "WHERE tenant_id = :t AND event_type = 'tier_changed'"
                ),
                {"t": tenant_id},
            ).scalar_one()
            actions = session.execute(
                text(
                    "SELECT count(*) FROM admin_actions "
                    "WHERE target_tenant_id = :t AND action = 'tier_change'"
                ),
                {"t": tenant_id},
            ).scalar_one()
        assert (events, actions) == (1, 1)


@requires_console_schema
@requires_lifecycle_schema
def test_a_refused_plan_change_changes_nothing(client, stripe, monkeypatch):
    fake = _plan_change(monkeypatch)
    with _Console() as console:
        not_live = console.create_tenant(client, name="Acme Test Not Live").json()["tenant_id"]
        live = console.create_tenant(client, name="Acme Test Live").json()["tenant_id"]
        _live(live)

        def change(tenant_id, tier):
            return client.post(
                f"/admin/tenants/{tenant_id}/tier", headers=console.headers(), json={"tier": tier}
            )

        cases = [
            (not_live, "growth", "BIL-001"),  # before go-live the plan is a deal term
            (live, "platinum", "BIL-003"),
            (live, "starter", "BIL-004"),  # already on the current Starter
        ]
        for tenant_id, tier, code in cases:
            response = change(tenant_id, tier)
            assert response.json()["detail"]["code"] == code, (tier, response.text)

        fake.fail = True
        failed = change(live, "growth")
        assert failed.json()["detail"]["code"] == "BIL-005"
        assert "test refusal" not in failed.text  # Stripe's own words never reach a person

        with platform_session() as session:
            session.execute(text("UPDATE tenants SET status = 'cancelling' WHERE id = :t"), {"t": live})
        fake.fail = False
        assert change(live, "growth").json()["detail"]["code"] == "BIL-002"

        assert _tier_of(live) == "starter" and _tier_of(not_live) == "starter"
        assert fake.calls == []


def _set_founding_end(tenant_id: str, interval: str | None) -> None:
    with platform_session() as session:
        session.execute(
            text(
                "UPDATE tenants SET founding_price = true, founding_price_ends_at = "
                "CASE WHEN CAST(:i AS text) IS NULL THEN NULL ELSE now() + CAST(:i AS interval) END "
                "WHERE id = :t"
            ),
            {"t": tenant_id, "i": interval},
        )


@requires_console_schema
@requires_lifecycle_schema
@requires_founding_schema
def test_mrr_counts_the_founding_price_until_it_ends(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client, name="Acme Test Founding MRR").json()["tenant_id"]
        _activate(tenant_id, subscription_id="sub_founding", status="active")
        with platform_session() as session:
            price, promo = session.execute(
                text(
                    "SELECT tr.monthly_price, tr.promo_monthly_price FROM tenants t "
                    "JOIN tiers tr ON tr.id = t.tier_id WHERE t.id = :t"
                ),
                {"t": tenant_id},
            ).one()

        _set_founding_end(tenant_id, None)
        at_list = Decimal(str(_money(client, console)["mrr"]))

        _set_founding_end(tenant_id, "40 days")
        during = _money(client, console)
        assert Decimal(str(during["mrr"])) == at_list - Decimal(price) + Decimal(promo)
        assert (Decimal(str(during["mrr"])), Decimal(str(during["mrr_in_trial"]))) == _hand_written_money()

        _set_founding_end(tenant_id, "-1 day")  # the 90 days are over
        assert Decimal(str(_money(client, console)["mrr"])) == at_list

        # The card says until when.
        _set_founding_end(tenant_id, "40 days")
        overview = client.get(f"/admin/tenants/{tenant_id}/overview", headers=console.headers()).json()
        assert overview["tenant"]["billing"]["founding_price_ends_at"] is not None


@requires_console_schema
@requires_lifecycle_schema
@requires_founding_schema
def test_a_plan_change_records_when_the_carried_founding_price_ends(client, stripe, monkeypatch):
    fake = _plan_change(monkeypatch)
    with _Console() as console:
        founding = console.create_tenant(client, name="Acme Test Founding Change").json()["tenant_id"]
        _live(founding, founding=True)

        fake.months = 2
        assert (
            client.post(
                f"/admin/tenants/{founding}/tier", headers=console.headers(), json={"tier": "growth"}
            ).status_code
            == 200
        )
        with platform_session() as session:
            ends = session.execute(
                text("SELECT founding_price_ends_at - now() FROM tenants WHERE id = :t"),
                {"t": founding},
            ).scalar_one()
        # Stripe's answer carried no date in this fake, so two months from now.
        assert 58 <= ends.days <= 62

        # No months carried (the 90 days were over): no founding price recorded.
        fake.months = 0
        assert (
            client.post(
                f"/admin/tenants/{founding}/tier", headers=console.headers(), json={"tier": "starter"}
            ).status_code
            == 200
        )
        with platform_session() as session:
            assert (
                session.execute(
                    text("SELECT founding_price_ends_at FROM tenants WHERE id = :t"), {"t": founding}
                ).scalar_one()
                is None
            )
