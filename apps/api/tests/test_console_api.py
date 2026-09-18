"""
The founder Console, slice 5.1, against the real database and RLS
(CLAUDE.md Section 7.15 / 7.9; migration 0011).

What this proves:
  * Step 1 -- staging: an intake takes files that pass the Section 7.11
    checks, refuses ones that don't (and stores nothing), and belongs to no
    tenant;
  * Step 2 -- tenant creation from an intake: the current tier version, the
    files moved from staging into the tenant, the intake address on the
    configured domain and NOT live, the Stripe customer; and "any failure
    rolls back everything, including the storage move";
  * Step 3 -- the invite: the owner is linked to their sign-in account, and
    the email is held in the outbox with its link;
  * founder alerts: raised from inside a tenant session, deduplicated,
    unreadable by that tenant, acknowledged from the Console;
  * Section 7.15.1: every Console route is 404 to a tenant user.

Stripe and Supabase are never called: both are replaced at the module
boundary. All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import io
import zipfile
from uuid import UUID, uuid4

import jwt
import pytest
from docflow_core import external_services, founder_alerts
from docflow_core.config import get_settings
from docflow_core.db import platform_session, tenant_session
from sqlalchemy import text

from tests.conftest import requires_console_schema

JWT_SECRET = "test-only-secret-for-ci"
CATALOG_CSV = b"sku,description,unit_of_measure\nTEST-1001,Test Beans 5lb,CS\nTEST-1002,Test Cups,BOX\n"


@pytest.fixture(autouse=True)
def _environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("INTAKE_EMAIL_DOMAIN", "intake.example.test")
    monkeypatch.setenv("FOUNDER_ALERT_EMAIL", "founder@example.com")
    monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def stripe(monkeypatch):
    """Records Stripe customer creation; set `.fail = True` to make it refuse."""

    class _Stripe:
        created: list[str] = []
        deleted: list[str] = []
        fail = False

    def create(*, tenant_id, name, email):
        if _Stripe.fail:
            raise external_services.ExternalServiceError("stripe", "test refusal")
        customer = f"cus_test_{tenant_id.hex[:8]}"
        _Stripe.created.append(customer)
        return customer

    monkeypatch.setattr(external_services, "create_stripe_customer", create)
    monkeypatch.setattr(external_services, "delete_stripe_customer", _Stripe.deleted.append)
    _Stripe.created, _Stripe.deleted, _Stripe.fail = [], [], False
    return _Stripe


@pytest.fixture
def supabase_links(monkeypatch):
    issued: list[str] = []
    auth_ids: dict[str, UUID] = {}

    def generate(*, email, redirect_to):
        auth_ids.setdefault(email, uuid4())
        url = f"https://auth.example.test/verify?token={len(issued)}&redirect_to={redirect_to}"
        issued.append(url)
        return external_services.InviteLink(auth_user_id=auth_ids[email], url=url)

    monkeypatch.setattr(external_services, "generate_invite_link", generate)
    return issued


class _Console:
    """A throwaway platform admin, and cleanup of everything it creates."""

    def __init__(self):
        self.user_id = uuid4()
        self.auth_user_id = str(uuid4())
        self.tenants: list[str] = []
        self.intakes: list[str] = []

    def __enter__(self):
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO users (id, tenant_id, auth_user_id, email, role, is_active) "
                    "VALUES (:id, NULL, :auth, :email, 'owner', true)"
                ),
                {
                    "id": str(self.user_id),
                    "auth": self.auth_user_id,
                    "email": f"console-{self.user_id.hex[:8]}@example.com",
                },
            )
            session.execute(
                text("INSERT INTO platform_admins (user_id, granted_by) VALUES (:id, :id)"),
                {"id": str(self.user_id)},
            )
        return self

    def headers(self) -> dict:
        token = jwt.encode(
            {"sub": self.auth_user_id, "email": "c@example.com", "aud": "authenticated"},
            JWT_SECRET,
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    def __exit__(self, *exc):
        with platform_session() as session:
            # 0012's import tables, when present: imports point at intake
            # files, and items/buyers point at imports, so they go first.
            if session.execute(text("SELECT to_regclass('catalog_imports')")).scalar():
                for tid in self.tenants:
                    for table in (
                        "buyer_merge_candidates", "learned_rules", "items", "buyers",
                        "import_mapping_templates", "catalog_imports",
                    ):
                        session.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tid})
            for tid in self.tenants:
                for table in (
                    "founder_alerts", "email_outbox", "tenant_lifecycle_events", "intake_addresses"
                ):
                    session.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tid})
                session.execute(text("DELETE FROM admin_actions WHERE target_tenant_id = :t"), {"t": tid})
            # Tenants and intakes point at each other; unlink before deleting.
            for tid in self.tenants:
                session.execute(
                    text("UPDATE tenants SET onboarding_intake_id = NULL WHERE id = :t"), {"t": tid}
                )
            for iid in self.intakes:
                session.execute(text("DELETE FROM onboarding_intake_files WHERE intake_id = :i"), {"i": iid})
                session.execute(text("DELETE FROM onboarding_intakes WHERE id = :i"), {"i": iid})
            for tid in self.tenants:
                session.execute(text("DELETE FROM users WHERE tenant_id = :t"), {"t": tid})
                session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tid})
            session.execute(
                text("DELETE FROM admin_actions WHERE platform_admin_user_id = :u"), {"u": str(self.user_id)}
            )
            session.execute(text("DELETE FROM platform_admins WHERE user_id = :u"), {"u": str(self.user_id)})
            session.execute(text("DELETE FROM users WHERE id = :u"), {"u": str(self.user_id)})

    # ── helpers ─────────────────────────────────────────────────────────────

    def overview(self, client, tenant_id: str) -> dict:
        return client.get(f"/admin/tenants/{tenant_id}/overview", headers=self.headers()).json()["tenant"]

    def outbox(self, client, tenant_id: str) -> list[dict]:
        return client.get(f"/admin/outbox?tenant_id={tenant_id}", headers=self.headers()).json()["emails"]

    def intake(self, client, name="Acme Test Prospect") -> str:
        response = client.post(
            "/admin/intakes",
            headers=self.headers(),
            json={"prospect_name": name, "contact_email": "buyer@example.com"},
        )
        assert response.status_code == 201, response.text
        intake_id = response.json()["intake_id"]
        self.intakes.append(intake_id)
        return intake_id

    def upload(self, client, intake_id: str, name: str, content: bytes):
        return client.post(
            f"/admin/intakes/{intake_id}/files",
            headers=self.headers(),
            files={"file": (name, content)},
        )

    def create_tenant(self, client, **overrides):
        body = {
            "name": "Acme Test Distributor",
            "owner_email": f"owner-{uuid4().hex[:6]}@example.com",
            "tier": "starter",
            **overrides,
        }
        response = client.post("/admin/tenants/new", headers=self.headers(), json=body)
        if response.status_code == 200:
            self.tenants.append(response.json()["tenant_id"])
        return response


def _scalar(sql: str, **params):
    with platform_session() as session:
        return session.execute(text(sql), params).scalar()


# ── Step 1: staging ─────────────────────────────────────────────────────────


@requires_console_schema
def test_an_intake_stores_files_that_pass_the_intake_checks(client):
    with _Console() as console:
        intake_id = console.intake(client)
        response = console.upload(client, intake_id, "catalog.csv", CATALOG_CSV)
        assert response.status_code == 201, response.text

        intake = client.get(f"/admin/intakes/{intake_id}", headers=console.headers()).json()["intake"]
        assert [f["original_filename"] for f in intake["files"]] == ["catalog.csv"]
        assert intake["linked_tenant_id"] is None
        path = _scalar("SELECT storage_path FROM onboarding_intake_files WHERE intake_id = :i", i=intake_id)
        assert path.startswith(f"staging/{intake_id}/")


@requires_console_schema
def test_a_file_the_intake_checks_refuse_is_stored_nowhere(client, _environment):
    """"All 7.11 file-hardening rules apply to staging uploads" -- a .zip is Tier 3."""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("po.csv", "sku\n")
    with _Console() as console:
        intake_id = console.intake(client)
        response = console.upload(client, intake_id, "orders.zip", archive.getvalue())
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "CON-007"
        assert detail["detail"]["file_error_code"].startswith("DOC-")
        assert _scalar("SELECT count(*) FROM onboarding_intake_files WHERE intake_id = :i", i=intake_id) == 0
        assert not (_environment / "staging" / intake_id).exists() or not any(
            (_environment / "staging" / intake_id).iterdir()
        )


# ── Step 2: tenant creation ─────────────────────────────────────────────────


@requires_console_schema
def test_creating_a_tenant_from_an_intake_moves_its_files_and_sets_everything_up(
    client, stripe, _environment
):
    with _Console() as console:
        intake_id = console.intake(client)
        console.upload(client, intake_id, "catalog.csv", CATALOG_CSV)
        staged = _scalar("SELECT storage_path FROM onboarding_intake_files WHERE intake_id = :i", i=intake_id)

        response = console.create_tenant(client, tier="growth", intake_id=intake_id)
        assert response.status_code == 200, response.text
        tenant_id = response.json()["tenant_id"]

        tenant = console.overview(client, tenant_id)
        assert tenant["tier_code"] == "growth" and tenant["tier_version"] == 1
        assert tenant["tier_monthly_price"] == "399.00"
        assert tenant["tier_document_allowance"] == 1000
        assert tenant["onboarding_status"] == "tenant_created"
        assert tenant["intake_address"].endswith("@intake.example.test")
        assert tenant["intake_address_active"] is False
        assert tenant["stripe_customer_id"] == stripe.created[0]
        assert tenant["owner"]["has_login"] is False

        moved = _scalar("SELECT storage_path FROM onboarding_intake_files WHERE intake_id = :i", i=intake_id)
        assert moved.startswith(f"tenants/{tenant_id}/onboarding/")
        assert (_environment / moved).read_bytes() == CATALOG_CSV
        assert not (_environment / staged).exists()
        linked = _scalar("SELECT linked_tenant_id FROM onboarding_intakes WHERE id = :i", i=intake_id)
        assert str(linked) == tenant_id
        assert _scalar(
            "SELECT count(*) FROM admin_actions WHERE action = 'tenant_create' AND target_tenant_id = :t",
            t=tenant_id,
        ) == 1


@requires_console_schema
def test_a_failed_tenant_creation_rolls_back_everything_including_the_file_move(
    client, stripe, _environment
):
    """Step 2: "Any failure rolls back everything, including the storage move." """
    with _Console() as console:
        intake_id = console.intake(client)
        console.upload(client, intake_id, "catalog.csv", CATALOG_CSV)
        staged = _scalar("SELECT storage_path FROM onboarding_intake_files WHERE intake_id = :i", i=intake_id)
        tenants_before = _scalar("SELECT count(*) FROM tenants")

        stripe.fail = True
        response = console.create_tenant(client, intake_id=intake_id, name="Acme Test Rollback")

        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "CON-006"
        assert _scalar("SELECT count(*) FROM tenants") == tenants_before
        assert _scalar("SELECT linked_tenant_id FROM onboarding_intakes WHERE id = :i", i=intake_id) is None
        path = _scalar("SELECT storage_path FROM onboarding_intake_files WHERE intake_id = :i", i=intake_id)
        assert path == staged
        assert (_environment / staged).read_bytes() == CATALOG_CSV
        assert not (_environment / "tenants").exists() or not any(
            p.is_file() for p in (_environment / "tenants").rglob("*")
        )


@requires_console_schema
def test_an_intake_cannot_be_linked_to_two_tenants(client, stripe):
    with _Console() as console:
        intake_id = console.intake(client)
        assert console.create_tenant(client, intake_id=intake_id).status_code == 200
        second = console.create_tenant(client, intake_id=intake_id, name="Acme Test Second")
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "CON-002"


# ── Step 3: the invite ──────────────────────────────────────────────────────


@requires_console_schema
def test_the_invite_links_the_owner_and_is_held_in_the_outbox_with_its_link(
    client, stripe, supabase_links
):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]

        response = client.post(f"/admin/tenants/{tenant_id}/invite", headers=console.headers())
        assert response.status_code == 200, response.text
        assert response.json()["held"] is True

        tenant = console.overview(client, tenant_id)
        assert tenant["owner"]["has_login"] is True
        assert tenant["invite_sent_at"] is not None

        emails = console.outbox(client, tenant_id)
        assert len(emails) == 1
        assert emails[0]["status"] == "held"
        assert emails[0]["template"] == "invite"
        assert supabase_links[0] in emails[0]["body_text"]
        assert "/auth/accept" in supabase_links[0]

        # Re-sendable: a second invite is a second email for the same login.
        assert client.post(f"/admin/tenants/{tenant_id}/invite", headers=console.headers()).status_code == 200
        emails = console.outbox(client, tenant_id)
        assert len(emails) == 2


# ── Founder alerts ──────────────────────────────────────────────────────────


@requires_console_schema
def test_an_alert_raised_in_a_tenant_session_reaches_the_console_once(client, stripe):
    with _Console() as console:
        tenant_id = UUID(console.create_tenant(client).json()["tenant_id"])

        with tenant_session(tenant_id) as session:
            first = founder_alerts.raise_alert(
                session,
                alert_type="export_integrity_failure",
                severity="high",
                tenant_id=tenant_id,
                payload={"export_id": "e1"},
                dedupe_key="export_integrity_failure:e1",
            )
        with tenant_session(tenant_id) as session:
            again = founder_alerts.raise_alert(
                session,
                alert_type="export_integrity_failure",
                severity="high",
                tenant_id=tenant_id,
                payload={"export_id": "e1"},
                dedupe_key="export_integrity_failure:e1",
            )
            # The tenant can raise an alert about itself, never read one.
            visible = session.execute(text("SELECT count(*) FROM founder_alerts")).scalar()
        assert first is True and again is False
        assert visible == 0

        alerts = [
            a for a in client.get("/admin/alerts", headers=console.headers()).json()["alerts"]
            if a["tenant_id"] == str(tenant_id)
        ]
        assert len(alerts) == 1 and alerts[0]["severity"] == "high"
        # One row, two channels: the alert names its email.
        assert _scalar(
            "SELECT o.template FROM founder_alerts a JOIN email_outbox o ON o.id = a.email_outbox_id "
            "WHERE a.id = :a",
            a=alerts[0]["id"],
        ) == "founder_alert"

        ack = client.post(f"/admin/alerts/{alerts[0]['id']}/acknowledge", headers=console.headers())
        assert ack.json()["acknowledged"] is True
        remaining = [
            a for a in client.get("/admin/alerts", headers=console.headers()).json()["alerts"]
            if a["tenant_id"] == str(tenant_id)
        ]
        assert remaining == []

        # Acknowledged, so the next occurrence is a new alert.
        with tenant_session(tenant_id) as session:
            assert founder_alerts.raise_alert(
                session,
                alert_type="export_integrity_failure",
                severity="high",
                tenant_id=tenant_id,
                payload={"export_id": "e1"},
                dedupe_key="export_integrity_failure:e1",
            ) is True


@requires_console_schema
def test_a_tenant_session_cannot_raise_an_alert_for_another_tenant(client, stripe):
    with _Console() as console:
        a = UUID(console.create_tenant(client, name="Acme Test A").json()["tenant_id"])
        b = UUID(console.create_tenant(client, name="Beacon Test B").json()["tenant_id"])
        with pytest.raises(Exception):
            with tenant_session(a) as session:
                founder_alerts.raise_alert(
                    session, alert_type="export_integrity_failure", severity="high", tenant_id=b
                )


# ── Section 7.15.1: unreachable, not hidden ─────────────────────────────────


@requires_console_schema
@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/admin/intakes"),
        ("post", "/admin/intakes"),
        ("get", "/admin/alerts"),
        ("get", "/admin/outbox"),
        ("get", "/admin/tiers"),
    ],
)
def test_every_console_route_is_404_to_a_tenant_user(client, method, path):
    from tests.test_review_api import _ReviewTenant

    with _ReviewTenant("Acme Test Distributor -- not an admin") as tenant:
        response = client.request(method.upper(), path, headers=tenant.headers(), json={})
        assert response.status_code == 404
