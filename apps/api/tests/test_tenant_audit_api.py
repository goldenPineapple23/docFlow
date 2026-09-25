"""
The Console's per-tenant Audit tab (Section 7.15.3 lists "Audit" among the
tenant page's tabs; slice 5.10, D-143): lifecycle events and Console actions
for one tenant, newest first, read-only, 404 to anyone but a platform admin,
and the read itself audited.
"""

from __future__ import annotations

from uuid import uuid4

from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import requires_database
from tests.test_console_api import _Console, _environment, stripe  # noqa: F401
from tests.test_quarantine_api import _Tenant

pytestmark = [requires_database]


def _url(tenant: _Tenant) -> str:
    return f"/admin/tenants/{tenant.tenant_id}/audit"


def test_the_audit_tab_is_404_to_a_tenant_owner_and_to_a_missing_tenant(client):
    with _Console() as console, _Tenant("Acme Test Audit") as t:
        assert client.get(_url(t), headers=t.headers()).status_code == 404
        missing = client.get(f"/admin/tenants/{uuid4()}/audit", headers=console.headers())
        assert missing.status_code == 404


def test_changes_are_listed_newest_first_and_views_only_when_asked(client):
    with _Console() as console, _Tenant("Acme Test Audit") as t:
        # A real Console change: example prompting on, then off (D-141).
        url = f"/admin/tenants/{t.tenant_id}/example-prompting"
        client.put(url, headers=console.headers(), json={"enabled": True, "golden_run_confirmed": True})
        client.put(url, headers=console.headers(), json={"enabled": False})

        body = client.get(_url(t), headers=console.headers()).json()
        events = [(e["source"], e["event"]) for e in body["entries"]]

        assert events[:4] == [
            ("lifecycle", "example_prompting_disabled"),
            ("console", "example_prompting_set"),
            ("lifecycle", "example_prompting_enabled"),
            ("console", "example_prompting_set"),
        ]
        assert not any(event in ("read", "acting_as_read", "quarantine_read") for _, event in events)
        lifecycle = body["entries"][0]
        assert lifecycle["actor_is_docflow"] is True
        assert lifecycle["actor_email"].startswith("console-")
        assert "EXAMPLE_MIN_APPROVED_DOCS" in lifecycle["constants"]

        with_views = client.get(_url(t) + "?include_views=true", headers=console.headers()).json()
        assert with_views["total"] > body["total"]
        assert any(e["event"] == "read" for e in with_views["entries"])


def test_reading_the_audit_tab_is_itself_audited_and_pages(client):
    with _Console() as console, _Tenant("Acme Test Audit") as t:
        url = f"/admin/tenants/{t.tenant_id}/example-prompting"
        for _ in range(3):
            client.put(url, headers=console.headers(), json={"enabled": False})

        first = client.get(_url(t) + "?limit=2", headers=console.headers()).json()
        second = client.get(_url(t) + "?limit=2&offset=2", headers=console.headers()).json()
        assert len(first["entries"]) == 2 and len(second["entries"]) >= 1
        assert first["entries"][0]["at"] >= second["entries"][0]["at"]

        with platform_session() as session:
            reads = session.execute(
                text(
                    "SELECT count(*) FROM admin_actions WHERE target_tenant_id = :t "
                    "AND action = 'read' AND target_type = 'audit'"
                ),
                {"t": str(t.tenant_id)},
            ).scalar_one()
        assert reads == 2
