"""
Console building blocks that need no database (Phase 5, slice 5.1):
email templates, alert validation, the Stripe/Supabase helpers with the
network replaced, and staging storage paths.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from docflow_core import email_outbox, external_services, founder_alerts, storage
from docflow_core.config import get_settings

# ── Email templates ─────────────────────────────────────────────────────────

TEMPLATE_PARAMS = {
    "invite": {"tenant_name": "Acme Test Distributor", "invite_link": "https://example.test/x"},
    "founder_alert": {
        "severity": "high",
        "title": "t",
        "tenant": "t",
        "raised_at": "r",
        "alert_id": "a",
        "details": "d",
        "console_link": "c",
    },
    "go_live": {
        "tenant_name": "Acme Test Distributor",
        "intake_address": "orders-x@intake.example.test",
        "app_url": "https://app.example.test",
        "tier_name": "Starter",
        "document_allowance": "300",
    },
    "intake_not_active": {"tenant_name": "Acme Test Distributor"},
    "first_week_checkin": {
        "tenant_name": "Acme Test Distributor",
        "documents_received": 12,
        "documents_approved": 10,
        "zero_edit_approvals": 7,
        "awaiting_review": 2,
    },
}


def test_every_template_is_covered_here():
    """A new template must be added to TEMPLATE_PARAMS, so it gets rendered below."""
    assert set(email_outbox.template_names()) == set(TEMPLATE_PARAMS)


@pytest.mark.parametrize("template", sorted(TEMPLATE_PARAMS))
def test_every_template_renders_with_no_placeholder_left(template):
    subject, body = email_outbox.render(template, TEMPLATE_PARAMS[template])
    assert subject and body
    assert "$" not in subject and "$" not in body


def test_a_missing_template_field_fails_loudly_instead_of_sending_a_placeholder():
    with pytest.raises(KeyError):
        email_outbox.render("invite", {"tenant_name": "Acme Test Distributor"})


def test_the_invite_carries_the_link_and_the_tenant_name():
    subject, body = email_outbox.render("invite", TEMPLATE_PARAMS["invite"])
    assert "Acme Test Distributor" in subject
    assert "https://example.test/x" in body


def test_templates_ship_with_the_package():
    """Read from disk at runtime, so they must be package data (pyproject)."""
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert "email_templates/*.txt" in pyproject["tool"]["setuptools"]["package-data"]["docflow_core"]


# ── Alerts ──────────────────────────────────────────────────────────────────


def test_an_unknown_alert_type_or_severity_is_refused_before_anything_is_written():
    class _NoSession:
        def begin_nested(self):
            raise AssertionError("nothing may be written for an invalid alert")

    with pytest.raises(ValueError):
        founder_alerts.raise_alert(_NoSession(), alert_type="made_up", severity="high", tenant_id=None)
    with pytest.raises(ValueError):
        founder_alerts.raise_alert(
            _NoSession(), alert_type="export_integrity_failure", severity="urgent", tenant_id=None
        )


def test_alert_titles_render_in_the_alert_email():
    for alert_type, title in founder_alerts.ALERT_TYPES.items():
        subject, _ = email_outbox.render(
            "founder_alert", {**TEMPLATE_PARAMS["founder_alert"], "title": title}
        )
        assert title in subject, alert_type


# ── Stripe and Supabase, network replaced ───────────────────────────────────


class _Response:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


@pytest.fixture
def _keys(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_not_a_real_key")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-not-real")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_stripe_customer_creation_is_idempotent_per_tenant(_keys, monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(200, {"id": "cus_test_123"})

    monkeypatch.setattr(external_services.httpx, "post", fake_post)
    tenant_id = uuid4()
    assert external_services.create_stripe_customer(
        tenant_id=tenant_id, name="Acme Test Distributor", email="owner@example.test"
    ) == "cus_test_123"
    url, kwargs = calls[0]
    assert url == "https://api.stripe.com/v1/customers"
    assert kwargs["headers"]["Idempotency-Key"] == f"docflow-customer-{tenant_id}"
    assert kwargs["data"]["metadata[docflow_tenant_id]"] == str(tenant_id)


def test_a_stripe_failure_raises_without_the_response_text(_keys, monkeypatch):
    refusal = _Response(402, {"error": {"message": "secret detail"}})
    monkeypatch.setattr(external_services.httpx, "post", lambda url, **kw: refusal)
    with pytest.raises(external_services.ExternalServiceError) as excinfo:
        external_services.create_stripe_customer(tenant_id=uuid4(), name="n", email="e@example.test")
    assert "secret detail" not in str(excinfo.value)


def test_a_first_invite_uses_an_invite_link(_keys, monkeypatch):
    auth_id = uuid4()
    sent = []

    def fake_post(url, **kwargs):
        sent.append(kwargs["json"]["type"])
        return _Response(
            200, {"id": str(auth_id), "action_link": "https://project.example.test/verify?t=1"}
        )

    monkeypatch.setattr(external_services.httpx, "post", fake_post)
    link = external_services.generate_invite_link(
        email="owner@example.test", redirect_to="http://app/auth/accept"
    )
    assert sent == ["invite"]
    assert link.auth_user_id == auth_id
    assert link.url.startswith("https://project.example.test/verify")


def test_a_resend_to_an_existing_user_falls_back_to_a_recovery_link(_keys, monkeypatch):
    auth_id = uuid4()
    sent = []

    def fake_post(url, **kwargs):
        sent.append(kwargs["json"]["type"])
        if kwargs["json"]["type"] == "invite":
            return _Response(422, {"msg": "already registered"})
        return _Response(
            200, {"user": {"id": str(auth_id)}, "properties": {"action_link": "https://l"}}
        )

    monkeypatch.setattr(external_services.httpx, "post", fake_post)
    link = external_services.generate_invite_link(email="owner@example.test", redirect_to="r")
    assert sent == ["invite", "recovery"]
    assert link.auth_user_id == auth_id and link.url == "https://l"


def test_supabase_refusing_for_another_reason_is_an_error_not_a_retry(_keys, monkeypatch):
    sent = []

    def fake_post(url, **kwargs):
        sent.append(kwargs["json"]["type"])
        return _Response(500)

    monkeypatch.setattr(external_services.httpx, "post", fake_post)
    with pytest.raises(external_services.ExternalServiceError):
        external_services.generate_invite_link(email="owner@example.test", redirect_to="r")
    assert sent == ["invite"]


# ── Staging storage ─────────────────────────────────────────────────────────


def test_staging_files_live_outside_every_tenant_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    try:
        intake_id, tenant_id = uuid4(), uuid4()
        staged = storage.save_staging_file(intake_id, "../../catalog.csv", b"sku,description\n")
        assert staged.startswith(f"staging/{intake_id}/") and staged.endswith(".csv")
        assert ".." not in staged

        copied = storage.copy_into_tenant(staged, tenant_id, area="onboarding")
        assert copied.startswith(f"tenants/{tenant_id}/onboarding/")
        assert storage.read_file(copied) == b"sku,description\n"

        storage.delete_file(staged)
        storage.delete_file(staged)  # absent is fine
        assert not (tmp_path / staged).exists()
    finally:
        get_settings.cache_clear()
