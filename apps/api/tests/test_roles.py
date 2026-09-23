# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
Roles on the tenant surface (CLAUDE.md Section 3: "Permissions enforced at the
API layer, never only in the UI"). Slice 5.8a, D-128.

Two halves, and both matter:

  * **completeness** -- every tenant-surface route appears in `app.roles`, so a
    route nobody classified cannot ship. This is a source check, not a
    behavioural one; it fails the build when a new route is added without a
    decision about who may call it.
  * **behaviour** -- a real reviewer and a real viewer, with real tokens,
    against the real database: a reviewer cannot reach an admin-only page, and a
    viewer cannot change anything or upload.
"""

from __future__ import annotations

import pytest

from app.main import app
from app.roles import ROLES_BY_ACCESS, ROUTE_ACCESS
from tests.conftest import requires_database
from tests.test_console_api import _environment  # noqa: F401
from tests.test_quarantine_api import _Tenant, celery, requires_quarantine_schema  # noqa: F401

# Everything that is not the customer's own surface: the Console (its own gate,
# 7.15.1), the inbound webhooks (no user at all), and FastAPI's own pages.
_NOT_TENANT_SURFACE = ("/admin", "/intake", "/webhooks", "/healthz", "/docs", "/openapi", "/redoc")


def _tenant_routes() -> list[tuple[str, str]]:
    """Every route the app really serves on the customer's surface.

    Read from the generated OpenAPI schema, not `app.routes`: a router included
    with `include_router` does not appear there, which is the same trap
    tests/test_cors.py documents (D-109). A check that silently sees no routes
    would pass for ever.
    """
    found = []
    for path, methods in app.openapi()["paths"].items():
        if any(path.startswith(prefix) for prefix in _NOT_TENANT_SURFACE):
            continue
        for method in methods:
            if method.upper() in ("HEAD", "OPTIONS"):
                continue
            found.append((method.upper(), path))
    assert found, "no tenant routes found -- the scan is broken, not the app"
    return sorted(found)


def test_every_tenant_route_says_who_may_call_it():
    missing = [r for r in _tenant_routes() if r not in ROUTE_ACCESS]
    assert not missing, (
        "These routes are on the customer's surface but nobody has said which roles may "
        f"call them -- add each to app/roles.py ROUTE_ACCESS: {missing}"
    )


def test_the_table_has_no_routes_that_no_longer_exist():
    live = set(_tenant_routes())
    stale = [r for r in ROUTE_ACCESS if r not in live]
    assert not stale, f"ROUTE_ACCESS lists routes that don't exist: {stale}"


def test_the_four_roles_are_exactly_the_ones_section_3_locks():
    assert ROLES_BY_ACCESS["member"] == {"owner", "admin", "reviewer", "viewer"}
    assert ROLES_BY_ACCESS["reviewer"] == {"owner", "admin", "reviewer"}
    assert ROLES_BY_ACCESS["admin"] == {"owner", "admin"}


# ── behaviour, against the real database ────────────────────────────────────


@requires_database
@requires_quarantine_schema
def test_the_dashboard_is_the_admins_and_a_reviewer_is_told_why(client):
    with _Tenant("Acme Test Roles") as t:
        t.add_user("reviewer")
        t.add_user("viewer")

        assert client.get("/home", headers=t.headers("owner")).status_code == 200

        for role in ("reviewer", "viewer"):
            response = client.get("/home", headers=t.headers(role))
            # 403 with the reason, not a 404: they are signed in to this account,
            # so pretending the page doesn't exist would only confuse them.
            assert response.status_code == 403, role
            detail = response.json()["detail"]
            assert detail["code"] == "AUTH-003"
            assert detail["action"]  # what to do next


@requires_database
@requires_quarantine_schema
def test_a_viewer_can_read_but_not_upload_or_change_anything(client, celery):
    with _Tenant("Acme Test Roles Viewer") as t:
        t.add_user("viewer")
        headers = t.headers("viewer")

        assert client.get("/review/documents", headers=headers).status_code == 200
        assert client.get("/allowance", headers=headers).status_code == 200
        assert client.get("/held", headers=headers).status_code == 200

        upload = client.post(
            "/documents/upload", headers=headers, files={"file": ("po.txt", b"PO 1", "text/plain")}
        )
        assert upload.status_code == 403
        assert upload.json()["detail"]["code"] == "AUTH-002"
        assert celery.sent == []
        assert not t.docs()  # nothing was stored either


@requires_database
@requires_quarantine_schema
def test_a_reviewer_may_upload(client, celery):
    with _Tenant("Acme Test Roles Reviewer") as t:
        t.add_user("reviewer")
        response = client.post(
            "/documents/upload",
            headers=t.headers("reviewer"),
            files={"file": ("po.txt", b"PURCHASE ORDER\nPO Number: R-1\n", "text/plain")},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "pending"
        assert len(celery.sent) == 1


@pytest.mark.parametrize("path", ["/home", "/review/documents", "/allowance", "/held"])
def test_none_of_it_answers_without_a_session(client, path):
    assert client.get(path).status_code == 401
