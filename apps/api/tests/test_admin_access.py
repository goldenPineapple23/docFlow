"""
CLAUDE.md Section 7.15.1 required tests: /admin/* is unreachable (404, not
401/403) to anyone without an active platform_admins row, including
unauthenticated requests. The "authenticated admin" and "authenticated
non-admin" cases additionally need a real users/platform_admins row, so
they're gated on requires_database (see conftest.py).
"""

from __future__ import annotations

from uuid import uuid4

import jwt
from docflow_core.config import get_settings
from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import requires_database


def test_admin_route_404_without_any_auth_header(client):
    response = client.get("/admin/tenants")
    assert response.status_code == 404


def test_admin_route_404_with_malformed_bearer_token(client, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-only-secret-for-ci")
    get_settings.cache_clear()
    try:
        response = client.get("/admin/tenants", headers={"Authorization": "Bearer not-a-real-jwt"})
        assert response.status_code == 404
    finally:
        get_settings.cache_clear()


@requires_database
def test_admin_route_404_with_validly_signed_but_unknown_user_token(client, monkeypatch):
    """
    A token that verifies cleanly (right signature, right claims shape) but
    whose subject has no matching `users` row (and therefore no
    platform_admins row) must still 404, not 401/403/500. Needs a real DB
    connection because resolving "does this user exist" is itself a lookup.
    """
    secret = "test-only-secret-for-ci"
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    get_settings.cache_clear()
    try:
        claims = {
            "sub": "00000000-0000-0000-0000-000000000000",
            "email": "nobody@example.com",
            "aud": "authenticated",
        }
        token = jwt.encode(claims, secret, algorithm="HS256")
        response = client.get("/admin/tenants", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 404
    finally:
        get_settings.cache_clear()


@requires_database
def test_platform_admin_can_create_tenant_and_admin_action_is_recorded(client, monkeypatch):
    """
    Full-stack version of the Phase 0 exit criterion: "the founder can
    create a tenant from the Console... a cross-tenant read by a platform
    admin succeeds and writes an admin_actions row."

    Uses its own throwaway platform_admin fixture (obviously-fake email, per
    CLAUDE.md Section 0 rule 4) rather than any real seeded account, and
    cleans up everything it inserts -- this test must be safe to re-run
    against docflow-staging without accumulating rows.
    """
    secret = "test-only-secret-for-ci"
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    get_settings.cache_clear()

    admin_auth_user_id = str(uuid4())
    admin_local_user_id = uuid4()
    tenant_id: str | None = None

    # The whole test body, including this fixture setup, is inside try/finally:
    # if setup itself ever fails partway (e.g. a prior run's cleanup didn't
    # complete), skipping cleanup here would leave a row behind under this
    # fixed email, permanently failing every future run until manually fixed.
    try:
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO users (id, tenant_id, auth_user_id, email, role, is_active) "
                    "VALUES (:id, NULL, :auth_user_id, :email, 'owner', true)"
                ),
                {
                    "id": str(admin_local_user_id),
                    "auth_user_id": admin_auth_user_id,
                    "email": "test-platform-admin@example.test",
                },
            )
            session.execute(
                text("INSERT INTO platform_admins (user_id, granted_by) VALUES (:id, :id)"),
                {"id": str(admin_local_user_id)},
            )

        token = jwt.encode(
            {"sub": admin_auth_user_id, "email": "test-platform-admin@example.test", "aud": "authenticated"},
            secret,
            algorithm="HS256",
        )
        response = client.post(
            "/admin/tenants/new",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Acme Test Distributor", "owner_email": "owner@example.com"},
        )
        assert response.status_code == 200
        tenant_id = response.json()["tenant_id"]

        with platform_session() as session:
            action_row = session.execute(
                text(
                    "SELECT platform_admin_user_id FROM admin_actions "
                    "WHERE action = 'tenant_create' AND target_tenant_id = :tenant_id"
                ),
                {"tenant_id": tenant_id},
            ).mappings().first()
        assert action_row is not None
        assert str(action_row["platform_admin_user_id"]) == str(admin_local_user_id)
    finally:
        with platform_session() as session:
            if tenant_id:
                session.execute(
                    text("DELETE FROM admin_actions WHERE target_tenant_id = :tid"), {"tid": tenant_id}
                )
                session.execute(
                    text("DELETE FROM tenant_lifecycle_events WHERE tenant_id = :tid"), {"tid": tenant_id}
                )
                session.execute(
                    text("DELETE FROM intake_addresses WHERE tenant_id = :tid"), {"tid": tenant_id}
                )
                session.execute(text("DELETE FROM users WHERE tenant_id = :tid"), {"tid": tenant_id})
                session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
            session.execute(
                text("DELETE FROM platform_admins WHERE user_id = :uid"), {"uid": str(admin_local_user_id)}
            )
            session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": str(admin_local_user_id)})
        get_settings.cache_clear()
