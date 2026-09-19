# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
Deal terms (DECISIONS.md D-117), against the real database (migration 0014).

The price is agreed before onboarding and recorded at Create tenant, so it
is never discussed on screen at go-live. What this proves:
  * The presets are docflow-pricing.docx's setup fees, from the table.
  * A deal is validated against its preset: Founding and Standard are fixed,
    Complex is $2,000-$2,500, Waived and Custom need a written reason.
  * The deal can be edited until go-live, each edit leaving a before/after
    lifecycle event and an admin_actions row; editing a note never moves a
    tenant to a newer price version; after go-live it is locked.
  * The new routes are 404 to anyone but a platform admin.

Stripe and Supabase are replaced at their boundaries. All data is fictional
(CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import UUID

import pytest
from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import requires_console_schema
from tests.test_console_api import (  # noqa: F401 -- fixtures
    _Console,
    _environment,
    _scalar,
    stripe,
    supabase_links,
)
from tests.test_onboarding_api import requires_deal_terms_schema


def _deal(**overrides) -> dict:
    return {"tier": "growth", "setup_fee_preset": "standard", "setup_fee_billing": "stripe", **overrides}


@requires_deal_terms_schema
@requires_console_schema
def test_the_presets_are_the_pricing_documents_setup_fees(client):
    with _Console() as console:
        response = client.get("/admin/setup-fee-presets", headers=console.headers())
        assert response.status_code == 200
        presets = {
            p["code"]: (p["default_amount"], p["min_amount"], p["max_amount"], p["note_required"])
            for p in response.json()["presets"]
        }
    assert presets == {
        "founding": ("750.00", "750.00", "750.00", False),
        "standard": ("1500.00", "1500.00", "1500.00", False),
        "complex": ("2000.00", "2000.00", "2500.00", False),
        "waived": ("0.00", "0.00", "0.00", True),
        "custom": (None, "0.00", None, True),
    }


@pytest.mark.parametrize(
    ("deal", "expected_fee"),
    [
        (_deal(setup_fee_preset="founding", founding_price=True), "750.00"),
        (_deal(), "1500.00"),
        (_deal(setup_fee_preset="complex"), "2000.00"),
        (_deal(setup_fee_preset="complex", setup_fee_amount="2,350"), "2350.00"),
        (_deal(setup_fee_preset="waived", setup_fee_note="Test pilot"), "0.00"),
        (_deal(setup_fee_preset="custom", setup_fee_amount="1234.56", setup_fee_note="Test"), "1234.56"),
    ],
)
@requires_deal_terms_schema
@requires_console_schema
def test_create_tenant_records_the_deal(client, stripe, deal, expected_fee):
    with _Console() as console:
        response = console.create_tenant(client, tier="starter", deal=deal)
        assert response.status_code == 200, response.text
        tenant = console.overview(client, response.json()["tenant_id"])
    assert tenant["tier_code"] == "growth"  # the deal's tier wins
    assert tenant["setup_fee_amount"] == expected_fee
    assert tenant["setup_fee_preset"] == deal["setup_fee_preset"]
    assert tenant["setup_fee_billing"] == "stripe"
    assert tenant["founding_price"] is deal.get("founding_price", False)
    assert tenant["onboarding_status"] == "tenant_created"  # nothing billed yet


@pytest.mark.parametrize(
    ("deal", "code"),
    [
        (_deal(setup_fee_preset="founding", setup_fee_amount="800"), "ONB-012"),
        (_deal(setup_fee_preset="complex", setup_fee_amount="2600"), "ONB-012"),
        (_deal(setup_fee_preset="complex", setup_fee_amount="1999.99"), "ONB-012"),
        (_deal(setup_fee_preset="waived"), "ONB-013"),
        (_deal(setup_fee_preset="waived", setup_fee_note="   "), "ONB-013"),
        (_deal(setup_fee_preset="custom", setup_fee_amount="900"), "ONB-013"),
        (_deal(setup_fee_preset="custom", setup_fee_note="Test"), "ONB-007"),
        (_deal(setup_fee_amount="abc"), "ONB-007"),
        (_deal(setup_fee_amount="-5"), "ONB-007"),
        (_deal(setup_fee_amount="10.005"), "ONB-007"),
        (_deal(setup_fee_amount="NaN"), "ONB-007"),
    ],
)
@requires_deal_terms_schema
@requires_console_schema
def test_a_deal_outside_its_preset_is_refused_and_no_tenant_is_created(client, stripe, deal, code):
    with _Console() as console:
        before = _scalar("SELECT count(*) FROM tenants")
        response = console.create_tenant(client, deal=deal)
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == code
        assert _scalar("SELECT count(*) FROM tenants") == before


@requires_deal_terms_schema
@requires_console_schema
def test_the_deal_can_change_until_go_live_and_every_change_is_logged(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client, deal=_deal()).json()["tenant_id"]
        tier_before = _scalar("SELECT tier_id FROM tenants WHERE id = :t", t=tenant_id)

        changed = client.put(
            f"/admin/tenants/{tenant_id}/deal-terms",
            headers=console.headers(),
            json=_deal(tier="scale", setup_fee_preset="complex", setup_fee_amount="2200",
                       setup_fee_billing="invoiced_manually", setup_fee_note="Test invoice 7"),
        )
        assert changed.status_code == 200, changed.text
        tenant = console.overview(client, tenant_id)
        assert (tenant["tier_code"], tenant["setup_fee_preset"], tenant["setup_fee_amount"]) == (
            "scale", "complex", "2200.00",
        )
        assert tenant["setup_fee_note"] == "Test invoice 7"
        assert _scalar("SELECT tier_id FROM tenants WHERE id = :t", t=tenant_id) != tier_before

        with platform_session() as session:
            event = session.execute(
                text(
                    "SELECT payload FROM tenant_lifecycle_events "
                    "WHERE tenant_id = :t AND event_type = 'deal_terms_changed'"
                ),
                {"t": tenant_id},
            ).scalar_one()
            audited = session.execute(
                text(
                    "SELECT count(*) FROM admin_actions "
                    "WHERE target_tenant_id = :t AND action = 'deal_terms_update'"
                ),
                {"t": tenant_id},
            ).scalar_one()
        assert event["before"]["setup_fee_amount"] == "1500.00"
        assert event["after"]["setup_fee_amount"] == "2200.00" and event["after"]["tier"] == "scale"
        assert audited == 1


@requires_deal_terms_schema
@requires_console_schema
def test_saving_the_same_tier_keeps_the_tenants_price_version(client, stripe):
    """A newer tier version must not reach a tenant because the founder
    edited a note: "existing tenants keep their version until the founder
    explicitly moves them" (Section 7.15.2)."""
    with _Console() as console:
        tenant_id = console.create_tenant(client, deal=_deal()).json()["tenant_id"]
        with platform_session() as session:
            old = session.execute(
                text("SELECT tier_id FROM tenants WHERE id = :t"), {"t": tenant_id}
            ).scalar()
            # Stand-in for an older version: point the tenant at a copy that
            # is not current. Removed again below.
            copy = session.execute(
                text(
                    "INSERT INTO tiers (code, version, name, monthly_price, promo_monthly_price, "
                    "promo_days, document_allowance, is_current) "
                    "SELECT code, 900 + floor(random() * 99)::int, name, monthly_price, promo_monthly_price, "
                    "promo_days, document_allowance, false FROM tiers WHERE id = :old RETURNING id"
                ),
                {"old": str(old)},
            ).scalar()
            session.execute(
                text("UPDATE tenants SET tier_id = :c WHERE id = :t"), {"c": str(copy), "t": tenant_id}
            )
        try:
            response = client.put(
                f"/admin/tenants/{tenant_id}/deal-terms",
                headers=console.headers(),
                json=_deal(setup_fee_billing="invoiced_manually", setup_fee_note="Test note only"),
            )
            assert response.status_code == 200, response.text
            assert str(_scalar("SELECT tier_id FROM tenants WHERE id = :t", t=tenant_id)) == str(copy)
        finally:
            with platform_session() as session:
                session.execute(
                    text("UPDATE tenants SET tier_id = :o WHERE id = :t"), {"o": str(old), "t": tenant_id}
                )
                session.execute(text("DELETE FROM tiers WHERE id = :c"), {"c": str(copy)})


@requires_deal_terms_schema
@requires_console_schema
def test_the_deal_is_locked_once_live(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client, deal=_deal()).json()["tenant_id"]
        with platform_session() as session:
            session.execute(
                text("UPDATE tenants SET onboarding_status = 'live' WHERE id = :t"), {"t": tenant_id}
            )
        response = client.put(
            f"/admin/tenants/{tenant_id}/deal-terms",
            headers=console.headers(),
            json=_deal(setup_fee_preset="waived", setup_fee_note="Test"),
        )
        assert response.status_code == 409 and response.json()["detail"]["code"] == "ONB-011"
        assert console.overview(client, tenant_id)["setup_fee_amount"] == "1500.00"


@requires_deal_terms_schema
@requires_console_schema
def test_the_deal_routes_are_404_to_everyone_else(client):
    assert client.get("/admin/setup-fee-presets").status_code == 404
    assert client.put(f"/admin/tenants/{UUID(int=1)}/deal-terms", json=_deal()).status_code == 404
