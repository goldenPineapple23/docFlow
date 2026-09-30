# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
Card billing with a 7-day trial (founder, 2026-09-29; decisions D1-D6 in
docs/BUILD-STATUS.md; migration 0031; D-181), against the real database and
RLS. Stripe is faked at the external_services boundary; the calls themselves
were run against Stripe test mode and are tested in
packages/core/tests/test_card_billing_stripe.py.

What this proves:
- a finished card page records the card, and a setup fee paid at signing only
  when it is exactly the tenant's fee (a wrong amount or a second payment
  alerts the founder); the same event twice applies once; a page made for
  another tenant is never recorded;
- a card update while past due charges the open invoices before the event is
  recorded (a failure leaves it unrecorded, and the re-delivery pays), and
  only for a card-billed tenant that is past due;
- a card-billed tenant's first past_due queues the owner's email and a
  reminder three days before the date it may be paused from; an invoice-billed
  tenant gets neither, and a second past_due event doesn't repeat them; the
  reminder skips itself once the payment has gone through;
- BIL-006 (with its date) and BIL-009 (after it) reach owners and admins, not
  reviewers, and never an invoice-billed tenant;
- the card page charges a standard customer's fee at signing and nothing for
  a founding one; "Ask for a card" picks the email that matches the deal;
- go-live's card gates (ONB-015, ONB-016), card collection, and no setup-fee
  item once the fee is paid; a declined card refuses a reactivation (BIL-007);
- a setup fee paid at signing can't be edited (ONB-017).
"""

from __future__ import annotations

import time
from decimal import Decimal
from uuid import UUID

import pytest
from docflow_core import card_billing, external_services, scheduled_jobs
from docflow_core.db import get_engine, platform_session, tenant_session
from sqlalchemy import text

from tests.conftest import database_available, requires_console_schema
from tests.test_console_api import _Console, _environment, _scalar, stripe, supabase_links  # noqa: F401
from tests.test_deal_terms_api import _deal
from tests.test_onboarding_api import (  # noqa: F401 -- fixtures
    FOUNDING_DEAL,
    _ready_to_go_live,
    billing,
    queue,
    requires_deal_terms_schema,
    requires_go_live_schema,
)
from tests.test_quarantine_api import _Tenant
from tests.test_stripe_webhook_api import _event_id, _post, _subscription_event, _webhook_secret  # noqa: F401


def _card_billing_schema_available() -> bool:
    """True once migration 0031 has been applied. Applied by hand on staging
    (D-013); CI applies it itself, so these tests never skip there."""
    if not database_available():
        return False
    try:
        with get_engine().connect() as conn:
            conn.execute(
                text("SELECT billing_method, card_on_file_at, setup_fee_paid_at FROM tenants LIMIT 0")
            )
        return True
    except Exception:
        return False


requires_card_billing_schema = pytest.mark.skipif(
    not _card_billing_schema_available(),
    reason="supabase/migrations/0031_card_billing.sql has not been applied to this database yet.",
)
pytestmark = requires_card_billing_schema

STANDARD_DEAL = {"tier": "starter", "setup_fee_preset": "standard", "setup_fee_billing": "stripe"}


def _set(tenant_id, **columns) -> None:
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    with platform_session() as session:
        session.execute(
            text(f"UPDATE tenants SET {assignments} WHERE id = :id"), {"id": str(tenant_id), **columns}
        )


def _card_tenant(tenant: _Tenant, *, method: str = "card", status: str = "active", **columns) -> str:
    customer = f"cus_test_{tenant.tenant_id.hex[:10]}"
    _set(
        tenant.tenant_id,
        stripe_customer_id=customer,
        stripe_subscription_id=f"sub_test_{tenant.tenant_id.hex[:8]}",
        stripe_subscription_status=status,
        billing_method=method,
        **columns,
    )
    return customer


def _column(tenant_id, column: str):
    return _scalar(f"SELECT {column} FROM tenants WHERE id = :t", t=str(tenant_id))


def _alerts(tenant_id, alert_type: str) -> int:
    return _scalar(
        "SELECT count(*) FROM founder_alerts WHERE tenant_id = :t AND type = :ty",
        t=str(tenant_id),
        ty=alert_type,
    )


def _emails(tenant_id, template: str) -> list[dict]:
    with platform_session() as session:
        rows = session.execute(
            text(
                "SELECT to_address, subject, body_text FROM email_outbox "
                "WHERE tenant_id = :t AND template = :tpl ORDER BY created_at"
            ),
            {"t": str(tenant_id), "tpl": template},
        ).mappings()
        return [dict(r) for r in rows]


def _card_page_event(customer: str, tenant_id, *, kind: str = "card") -> dict:
    return {
        "id": _event_id("checkout"),
        "type": "checkout.session.completed",
        "created": int(time.time()),
        "data": {
            "object": {
                "object": "checkout.session",
                "id": "cs_test_1",
                "customer": customer,
                "metadata": {"docflow_card_page": kind, "docflow_tenant_id": str(tenant_id)},
            }
        },
    }


def _completed(monkeypatch, tenant_id, customer: str, *, kind: str = "card", cents: int | None = None):
    done = external_services.CompletedCardPage(
        customer_id=customer,
        tenant_id=str(tenant_id),
        payment_method_id="pm_test",
        kind=kind,
        amount_paid_cents=cents,
    )
    monkeypatch.setattr(external_services, "complete_card_page", lambda _session_id: done)


def _card_updated_event(customer: str) -> dict:
    return {
        "id": _event_id("customer"),
        "type": "customer.updated",
        "created": int(time.time()),
        "data": {
            "object": {
                "object": "customer",
                "id": customer,
                "invoice_settings": {"default_payment_method": "pm_new"},
            },
            "previous_attributes": {"invoice_settings": {"default_payment_method": "pm_old"}},
        },
    }


# ── A finished card page ──────────────────────────────────────────────────────


def test_a_saved_card_is_recorded_once(client, monkeypatch):
    with _Tenant("Acme Test Card Saved") as tenant:
        customer = _card_tenant(tenant, method="invoice")
        _completed(monkeypatch, tenant.tenant_id, customer)
        event = _card_page_event(customer, tenant.tenant_id)
        assert _post(client, event).json() == {"outcome": "applied"}
        first = _column(tenant.tenant_id, "card_on_file_at")
        assert first is not None
        assert _post(client, event).json() == {"outcome": "duplicate"}
        assert _column(tenant.tenant_id, "card_on_file_at") == first


def test_a_setup_fee_paid_at_signing_is_recorded_only_at_exactly_the_tenants_fee(client, monkeypatch):
    with _Tenant("Acme Test Fee Paid") as tenant:
        customer = _card_tenant(tenant, setup_fee_amount=Decimal("1500.00"), setup_fee_billing="stripe")
        _completed(monkeypatch, tenant.tenant_id, customer, kind="setup_fee", cents=150000)
        response = _post(client, _card_page_event(customer, tenant.tenant_id, kind="setup_fee"))
        assert response.json() == {"outcome": "applied"}
        assert _column(tenant.tenant_id, "setup_fee_paid_at") is not None
        assert _column(tenant.tenant_id, "card_on_file_at") is not None

        # A second payment for the same fee: recorded, nothing changes, the founder decides.
        paid_at = _column(tenant.tenant_id, "setup_fee_paid_at")
        again = _post(client, _card_page_event(customer, tenant.tenant_id, kind="setup_fee"))
        assert again.json() == {"outcome": "already_paid"}
        assert _column(tenant.tenant_id, "setup_fee_paid_at") == paid_at
        assert _alerts(tenant.tenant_id, "setup_fee_already_paid") == 1


def test_a_wrong_amount_saves_the_card_but_not_the_fee_and_alerts_the_founder(client, monkeypatch):
    with _Tenant("Acme Test Fee Mismatch") as tenant:
        customer = _card_tenant(tenant, setup_fee_amount=Decimal("1500.00"), setup_fee_billing="stripe")
        _completed(monkeypatch, tenant.tenant_id, customer, kind="setup_fee", cents=75000)
        response = _post(client, _card_page_event(customer, tenant.tenant_id, kind="setup_fee"))
        assert response.json() == {"outcome": "amount_mismatch"}
        assert _column(tenant.tenant_id, "setup_fee_paid_at") is None
        assert _column(tenant.tenant_id, "card_on_file_at") is not None
        assert _alerts(tenant.tenant_id, "setup_fee_amount_mismatch") == 1


def test_a_page_made_for_another_tenant_is_never_recorded(client, monkeypatch):
    with _Tenant("Acme Test Card Mine") as mine, _Tenant("Acme Test Card Other") as other:
        customer = _card_tenant(mine)
        _completed(monkeypatch, other.tenant_id, customer)
        event = _card_page_event(customer, mine.tenant_id)
        assert _post(client, event).json() == {"outcome": "unmatched"}
        assert _column(mine.tenant_id, "card_on_file_at") is None
        assert not _scalar("SELECT count(*) FROM stripe_webhook_events WHERE id = :e", e=event["id"])


# ── A card update while past due ──────────────────────────────────────────────


def _record_payments(monkeypatch) -> list[str]:
    paid: list[str] = []

    def pay(subscription_id: str) -> external_services.OpenInvoicesPaid:
        paid.append(subscription_id)
        return external_services.OpenInvoicesPaid(paid=1, declined=0, needs_customer=0)

    monkeypatch.setattr(external_services, "pay_open_invoices", pay)
    return paid


def test_a_card_update_while_past_due_charges_the_open_invoice(client, monkeypatch):
    paid = _record_payments(monkeypatch)
    with _Tenant("Acme Test Card Update") as tenant:
        customer = _card_tenant(tenant, status="past_due")
        event = _card_updated_event(customer)
        assert _post(client, event).json() == {"outcome": "applied"}
        assert paid == [f"sub_test_{tenant.tenant_id.hex[:8]}"]
        assert _scalar("SELECT count(*) FROM stripe_webhook_events WHERE id = :e", e=event["id"]) == 1


@pytest.mark.parametrize(("method", "status"), [("card", "active"), ("invoice", "past_due")])
def test_no_charge_unless_card_billed_and_past_due(client, monkeypatch, method, status):
    paid = _record_payments(monkeypatch)
    with _Tenant("Acme Test Card No Charge") as tenant:
        customer = _card_tenant(tenant, method=method, status=status)
        assert _post(client, _card_updated_event(customer)).status_code == 200
        assert paid == []


def test_a_failed_charge_leaves_the_event_unrecorded_and_the_redelivery_pays(client, monkeypatch):
    with _Tenant("Acme Test Card Redelivery") as tenant:
        customer = _card_tenant(tenant, status="past_due")
        event = _card_updated_event(customer)

        def unreachable(_subscription_id):
            raise external_services.ExternalServiceError("stripe", "test: unreachable")

        monkeypatch.setattr(external_services, "pay_open_invoices", unreachable)
        failing = client.__class__(client.app, raise_server_exceptions=False)
        assert _post(failing, event).status_code == 500
        assert not _scalar("SELECT count(*) FROM stripe_webhook_events WHERE id = :e", e=event["id"])

        paid = _record_payments(monkeypatch)
        assert _post(client, event).json() == {"outcome": "applied"}
        assert len(paid) == 1


# ── Past due: the owner's email and the reminder ─────────────────────────────


def _past_due(
    client, monkeypatch, tenant: _Tenant, customer: str, *, label: str = "pd", later: int = 0
) -> dict:
    fetched: list[str] = []

    def amount(subscription_id: str) -> int:
        fetched.append(subscription_id)
        return 29900

    monkeypatch.setattr(external_services, "latest_invoice_amount_cents", amount)
    event = _subscription_event(
        event_id=_event_id(label), customer=customer, status="past_due", created=int(time.time()) + later
    )
    response = _post(client, event)
    assert response.status_code == 200, response.text
    return {"fetched": fetched, "outcome": response.json()["outcome"]}


def _reminders(tenant_id) -> list[dict]:
    with platform_session() as session:
        rows = session.execute(
            text(
                "SELECT j.run_at, j.payload, t.first_past_due_at FROM scheduled_jobs j "
                "JOIN tenants t ON t.id = j.tenant_id "
                "WHERE j.tenant_id = :t AND j.job_type = 'past_due_reminder'"
            ),
            {"t": str(tenant_id)},
        ).mappings()
        return [dict(r) for r in rows]


def test_a_card_tenants_first_past_due_emails_the_owner_and_schedules_the_reminder(client, monkeypatch):
    with _Tenant("Acme Test Past Due Card") as tenant:
        customer = _card_tenant(tenant)
        result = _past_due(client, monkeypatch, tenant, customer)
        assert result["outcome"] == "applied" and result["fetched"] == ["sub_evt"]

        (email,) = _emails(tenant.tenant_id, "payment_failed")
        assert email["subject"] == "Your DocFlow payment didn't go through"
        assert "$299.00" in email["body_text"]
        assert "may be paused from" in email["body_text"] and "will pause" not in email["body_text"]

        (reminder,) = _reminders(tenant.tenant_id)
        # CURE_PERIOD_DAYS (14) after the first notice, less PAST_DUE_REMINDER_DAYS_BEFORE (3).
        assert (reminder["run_at"] - reminder["first_past_due_at"]).days == 11
        assert reminder["payload"]["amount_cents"] == 29900

        # A later past_due event of the same episode repeats neither. Stamped
        # seconds later: an event in the same second as the saved state is
        # re-fetched from Stripe instead (D-173), which isn't this test's subject.
        _past_due(client, monkeypatch, tenant, customer, label="pd2", later=2)
        assert len(_emails(tenant.tenant_id, "payment_failed")) == 1
        assert len(_reminders(tenant.tenant_id)) == 1


def test_an_invoice_tenant_going_past_due_gets_no_card_email_and_stripe_is_not_asked(client, monkeypatch):
    with _Tenant("Acme Test Past Due Invoice") as tenant:
        customer = _card_tenant(tenant, method="invoice")
        result = _past_due(client, monkeypatch, tenant, customer)
        assert result["outcome"] == "applied" and result["fetched"] == []
        assert _emails(tenant.tenant_id, "payment_failed") == []
        assert _reminders(tenant.tenant_id) == []


def _run_reminder(tenant_id) -> None:
    with platform_session() as session:
        row = (
            session.execute(
                text(
                    "SELECT id, payload FROM scheduled_jobs "
                    "WHERE tenant_id = :t AND job_type = 'past_due_reminder'"
                ),
                {"t": str(tenant_id)},
            )
            .mappings()
            .one()
        )
    job = scheduled_jobs.Job(
        id=UUID(str(row["id"])),
        tenant_id=tenant_id,
        job_type="past_due_reminder",
        payload=row["payload"],
        attempts=0,
    )
    with tenant_session(tenant_id) as session:
        card_billing.send_past_due_reminder(session, job)


def test_the_reminder_is_sent_while_still_past_due(client, monkeypatch):
    with _Tenant("Acme Test Reminder Sent") as tenant:
        customer = _card_tenant(tenant)
        _past_due(client, monkeypatch, tenant, customer)
        _run_reminder(tenant.tenant_id)
        (email,) = _emails(tenant.tenant_id, "payment_failed_reminder")
        assert email["subject"].startswith("Reminder: please update your card by ")
        assert "$299.00" in email["body_text"]


def test_the_reminder_skips_itself_once_the_payment_went_through(client, monkeypatch):
    with _Tenant("Acme Test Reminder Skipped") as tenant:
        customer = _card_tenant(tenant)
        _past_due(client, monkeypatch, tenant, customer)
        _set(tenant.tenant_id, stripe_subscription_status="active", first_past_due_at=None)
        _run_reminder(tenant.tenant_id)
        assert _emails(tenant.tenant_id, "payment_failed_reminder") == []


# ── The banner ────────────────────────────────────────────────────────────────


def _past_due_since(tenant: _Tenant, days: int, *, method: str = "card") -> None:
    _card_tenant(tenant, method=method, status="past_due")
    with platform_session() as session:
        session.execute(
            text("UPDATE tenants SET first_past_due_at = now() - make_interval(days => :d) WHERE id = :id"),
            {"d": days, "id": str(tenant.tenant_id)},
        )


def test_owners_see_bil_006_with_the_date_and_bil_009_after_it(client):
    with _Tenant("Acme Test Banner") as tenant:
        _past_due_since(tenant, 2)
        banner = client.get("/billing", headers=tenant.headers()).json()["banner"]
        assert banner["code"] == "BIL-006"
        assert "may be paused from" in banner["message"] and "{" not in banner["message"]
        assert client.get("/home", headers=tenant.headers()).json()["billing_banner"]["code"] == "BIL-006"

        _past_due_since(tenant, 15)  # past the 14-day cure period
        banner = client.get("/billing", headers=tenant.headers()).json()["banner"]
        assert banner["code"] == "BIL-009"
        assert banner["title"] == "Your payment is overdue"
        assert banner["message"] == "Processing may be paused."


def test_no_banner_for_an_invoice_tenant_and_no_billing_page_for_a_reviewer(client):
    with _Tenant("Acme Test Banner Invoice") as tenant:
        _past_due_since(tenant, 2, method="invoice")
        assert client.get("/billing", headers=tenant.headers()).json()["banner"] is None
        tenant.add_user("reviewer")
        refused = client.get("/billing", headers=tenant.headers("reviewer"))
        assert refused.status_code == 403
        assert refused.json()["detail"]["code"] == "AUTH-003"


# ── The card page and "Ask for a card" ───────────────────────────────────────


def _record_card_pages(monkeypatch) -> list[dict]:
    pages: list[dict] = []

    def create(**kwargs):
        pages.append(kwargs)
        return external_services.CardPage(
            session_id="cs_test", url="https://checkout.example.test/cs_test", mode="x"
        )

    monkeypatch.setattr(external_services, "create_card_page", create)
    return pages


@pytest.mark.parametrize(("founding", "expected_fee"), [(False, Decimal("1500.00")), (True, None)])
def test_the_card_page_charges_a_standard_customers_fee_and_nothing_for_a_founding_one(
    client, monkeypatch, founding, expected_fee
):
    pages = _record_card_pages(monkeypatch)
    with _Tenant("Acme Test Card Page") as tenant:
        _card_tenant(
            tenant,
            method="invoice",
            setup_fee_amount=Decimal("1500.00"),
            setup_fee_billing="stripe",
            founding_price=founding,
        )
        response = client.post("/billing/card-page", headers=tenant.headers())
        assert response.json() == {"url": "https://checkout.example.test/cs_test"}
        (page,) = pages
        assert page["setup_fee"] == expected_fee
        assert page["tenant_id"] == tenant.tenant_id


def test_stripe_not_answering_is_bil_008(client, monkeypatch):
    def refuse(**_kwargs):
        raise external_services.ExternalServiceError("stripe", "test refusal")

    monkeypatch.setattr(external_services, "create_card_page", refuse)
    with _Tenant("Acme Test Card Page Down") as tenant:
        _card_tenant(tenant)
        response = client.post("/billing/card-page", headers=tenant.headers())
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "BIL-008"


@pytest.fixture()
def support_email(monkeypatch):
    from docflow_core.config import get_settings

    monkeypatch.setenv("SUPPORT_EMAIL", "support@example.test")
    get_settings.cache_clear()
    yield "support@example.test"
    get_settings.cache_clear()


@requires_console_schema
@pytest.mark.parametrize(
    ("deal", "template", "monthly"),
    [
        (FOUNDING_DEAL, "card_request_founding", "$199.00"),
        (STANDARD_DEAL, "card_request_at_signing", "$299.00"),
        (
            {**STANDARD_DEAL, "setup_fee_preset": "waived", "setup_fee_note": "Test waiver"},
            "card_request_no_fee",
            "$299.00",
        ),
    ],
)
def test_ask_for_a_card_sends_the_email_that_matches_the_deal(
    client, stripe, support_email, deal, template, monthly
):
    with _Console() as console:
        tenant_id = console.create_tenant(client, deal=deal).json()["tenant_id"]
        response = client.post(f"/admin/tenants/{tenant_id}/card-request", headers=console.headers())
        assert response.status_code == 200, response.text
        assert response.json()["template"] == template
        (email,) = _emails(tenant_id, template)
        body = email["body_text"].replace("\n", " ")
        assert "/billing" in body
        assert monthly in body  # the monthly amount, from the tier
        assert "To cancel, email support@example.test." in body


@requires_console_schema
def test_ask_for_a_card_refuses_without_a_support_address(client, stripe, monkeypatch):
    from docflow_core.config import get_settings

    monkeypatch.setenv("SUPPORT_EMAIL", "")
    get_settings.cache_clear()
    try:
        with _Console() as console:
            tenant_id = console.create_tenant(client, deal=FOUNDING_DEAL).json()["tenant_id"]
            response = client.post(f"/admin/tenants/{tenant_id}/card-request", headers=console.headers())
            assert response.status_code == 409 and response.json()["detail"]["code"] == "ONB-018"
            assert _emails(tenant_id, "card_request_founding") == []
    finally:
        get_settings.cache_clear()


# ── Go-live and reactivation ─────────────────────────────────────────────────


def _go_live(client, console, tenant_id, method: str):
    return client.post(
        f"/admin/tenants/{tenant_id}/go-live", headers=console.headers(), json={"billing_method": method}
    )


@requires_deal_terms_schema
@requires_go_live_schema
@requires_console_schema
def test_card_billing_needs_a_card_on_file(client, stripe, supabase_links, queue, billing):
    with _Console() as console:
        tenant_id = _ready_to_go_live(client, console)
        refused = _go_live(client, console, tenant_id, "card")
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "ONB-015"
        assert billing.calls == []


@requires_deal_terms_schema
@requires_go_live_schema
@requires_console_schema
def test_a_standard_customer_billed_by_card_must_have_paid_the_fee_at_signing(
    client, stripe, supabase_links, queue, billing
):
    with _Console() as console:
        tenant_id = _ready_to_go_live(client, console, deal=STANDARD_DEAL)
        with platform_session() as session:
            session.execute(
                text("UPDATE tenants SET card_on_file_at = now() WHERE id = :id"), {"id": tenant_id}
            )
        refused = _go_live(client, console, tenant_id, "card")
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "ONB-016"

        with platform_session() as session:
            session.execute(
                text("UPDATE tenants SET setup_fee_paid_at = now() WHERE id = :id"), {"id": tenant_id}
            )
        assert _go_live(client, console, tenant_id, "card").status_code == 200
        (call,) = billing.calls
        assert call["charge_card"] is True
        assert call["setup_fee"] is None  # paid at signing, not added to the first invoice


@requires_deal_terms_schema
@requires_go_live_schema
@requires_console_schema
def test_a_founding_customer_billed_by_card_pays_the_fee_with_month_one(
    client, stripe, supabase_links, queue, billing
):
    with _Console() as console:
        tenant_id = _ready_to_go_live(client, console)
        with platform_session() as session:
            session.execute(
                text("UPDATE tenants SET card_on_file_at = now() WHERE id = :id"), {"id": tenant_id}
            )
        assert _go_live(client, console, tenant_id, "card").status_code == 200
        (call,) = billing.calls
        assert call["charge_card"] is True and call["setup_fee"] == Decimal("750.00")
        assert _column(tenant_id, "billing_method") == "card"
        (email,) = _emails(tenant_id, "go_live")
        # The $199.00 founding month plus the $750.00 setup fee, on the trial's last day.
        assert "the card on file is charged $949.00." in email["body_text"]


@requires_console_schema
def test_a_declined_card_refuses_the_reactivation_and_the_tenant_stays_suspended(client, monkeypatch):
    def declined(**_kwargs):
        raise external_services.CardDeclined()

    monkeypatch.setattr(external_services, "start_subscription", declined)
    with _Console() as console, _Tenant("Acme Test Reactivate Declined") as tenant:
        _card_tenant(tenant, status="canceled")
        with platform_session() as session:
            session.execute(
                text("UPDATE tenants SET status = 'suspended', status_changed_at = now() WHERE id = :id"),
                {"id": str(tenant.tenant_id)},
            )
        response = client.post(f"/admin/tenants/{tenant.tenant_id}/reactivate", headers=console.headers())
        assert response.status_code == 409 and response.json()["detail"]["code"] == "BIL-007"
        assert _column(tenant.tenant_id, "status") == "suspended"


# ── The setup fee, locked once paid ──────────────────────────────────────────


@requires_deal_terms_schema
@requires_console_schema
def test_a_setup_fee_paid_at_signing_cannot_be_edited(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client, deal=_deal()).json()["tenant_id"]
        with platform_session() as session:
            session.execute(
                text("UPDATE tenants SET setup_fee_paid_at = now() WHERE id = :id"), {"id": tenant_id}
            )
        path = f"/admin/tenants/{tenant_id}/deal-terms"
        refused = client.put(path, headers=console.headers(), json=_deal(setup_fee_preset="complex"))
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "ONB-017"
        # The rest of the deal stays editable until go-live.
        assert client.put(path, headers=console.headers(), json=_deal(tier="scale")).status_code == 200


# ── A cancel confirmed during the trial (founder, 2026-09-29) ────────────────


def _record_trial_ends(monkeypatch, *, fail: bool = False) -> list[dict]:
    calls: list[dict] = []

    def end(**kwargs):
        if fail:
            raise external_services.ExternalServiceError("stripe", "test: unreachable")
        calls.append(kwargs)

    monkeypatch.setattr(external_services, "end_trial_without_charge", end)
    return calls


def _trialing(console, client, *, deal: dict, **columns) -> str:
    tenant_id = console.create_tenant(client, deal=deal).json()["tenant_id"]
    _set(
        tenant_id,
        stripe_subscription_id=f"sub_test_{tenant_id[:8]}",
        stripe_subscription_status="trialing",
        billing_method="card",
        **columns,
    )
    return tenant_id


def _cancel(client, console, tenant_id, **body):
    return client.post(
        f"/admin/tenants/{tenant_id}/cancel",
        headers=console.headers(),
        json={"reason": "customer_requested", **body},
    )


@requires_deal_terms_schema
@requires_console_schema
@pytest.mark.parametrize("case", ["founding (fee pending)", "standard (fee paid at signing)"])
def test_a_cancel_during_the_trial_ends_it_at_stripe_before_it_is_recorded(client, stripe, monkeypatch, case):
    calls = _record_trial_ends(monkeypatch)
    with _Console() as console:
        if case.startswith("founding"):
            tenant_id = _trialing(console, client, deal=FOUNDING_DEAL)
        else:
            tenant_id = _trialing(console, client, deal=STANDARD_DEAL)
            with platform_session() as session:
                session.execute(
                    text("UPDATE tenants SET setup_fee_paid_at = now() WHERE id = :id"), {"id": tenant_id}
                )
        response = _cancel(client, console, tenant_id)
        assert response.status_code == 200, response.text
        assert response.json()["trial_ended_without_charge"] is True
        (call,) = calls
        assert call["subscription_id"] == f"sub_test_{tenant_id[:8]}"
        assert call["customer_id"] == _column(tenant_id, "stripe_customer_id")
        assert str(call["tenant_id"]) == tenant_id
        assert _column(tenant_id, "status") == "cancelling"


@requires_deal_terms_schema
@requires_console_schema
def test_a_cancel_after_the_trial_leaves_stripe_alone(client, stripe, monkeypatch):
    calls = _record_trial_ends(monkeypatch)
    with _Console() as console:
        tenant_id = _trialing(console, client, deal=FOUNDING_DEAL)
        _set(tenant_id, stripe_subscription_status="active")
        response = _cancel(client, console, tenant_id)
        assert response.status_code == 200 and response.json()["trial_ended_without_charge"] is False
        assert calls == []


@requires_deal_terms_schema
@requires_console_schema
def test_stripe_not_answering_changes_nothing_in_docflow(client, stripe, monkeypatch):
    _record_trial_ends(monkeypatch, fail=True)
    with _Console() as console:
        tenant_id = _trialing(console, client, deal=FOUNDING_DEAL)
        response = _cancel(client, console, tenant_id)
        assert response.status_code == 502 and response.json()["detail"]["code"] == "CON-006"
        assert _column(tenant_id, "status") == "active"


@requires_deal_terms_schema
@requires_console_schema
def test_a_refused_cancel_never_touches_stripe(client, stripe, monkeypatch):
    calls = _record_trial_ends(monkeypatch)
    with _Console() as console:
        tenant_id = _trialing(console, client, deal=FOUNDING_DEAL)
        response = _cancel(client, console, tenant_id, reason="for_cause", note="too short")
        assert response.status_code == 409 and response.json()["detail"]["code"] == "LIFE-002"
        assert calls == []
