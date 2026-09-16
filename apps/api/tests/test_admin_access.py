"""
CLAUDE.md Section 7.15.1 required tests: /admin/* is unreachable (404, not
401/403) to anyone without an active platform_admins row, including
unauthenticated requests. The "authenticated admin" and "authenticated
non-admin" cases additionally need a real users/platform_admins row, so
they're gated on requires_database (see conftest.py).
"""

from __future__ import annotations

import jwt
from docflow_core.config import get_settings

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
def test_platform_admin_can_create_tenant_and_admin_action_is_recorded(client):
    """
    Full-stack version of the Phase 0 exit criterion: "the founder can
    create a tenant from the Console... a cross-tenant read by a platform
    admin succeeds and writes an admin_actions row." Requires a seeded
    platform_admins row for the signing key used -- wired up once
    docflow-staging exists (SETUP.md).
    """
    # Intentionally left as a documented gap until docflow-staging exists and
    # a seeding script for a test platform_admin is added in Phase 0's
    # remaining work -- see the Phase 0 checkpoint summary.
    import pytest

    pytest.skip("Needs a seeded platform_admin in docflow-staging -- see Phase 0 checkpoint summary.")
