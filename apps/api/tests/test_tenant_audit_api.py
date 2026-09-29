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


# ── One clock for the whole trail (D-164) ───────────────────────────────────


def test_the_order_holds_when_the_app_clock_runs_behind_the_database(client, monkeypatch):
    """Console actions were stamped with the application server's clock and
    lifecycle events with the database's. A server clock 0.66 s behind (as
    measured on the development machine) was enough to list a Console action
    before the lifecycle event it caused. Here the app clock is an hour
    behind -- deterministic, whatever the real skew -- and the order must
    still be exactly newest first.

    Since D-170 #2 the module reads no app clock at all, so there is nothing
    to skew; the skew stays so that one brought back is caught here."""
    from datetime import timedelta

    import docflow_core.admin_data_access as ada

    from tests.app_clock import skew_app_clock

    skew_app_clock(monkeypatch, ada, by=-timedelta(hours=1))

    with _Console() as console, _Tenant("Acme Test Audit Clock") as t:
        url = f"/admin/tenants/{t.tenant_id}/example-prompting"
        client.put(url, headers=console.headers(), json={"enabled": True, "golden_run_confirmed": True})
        client.put(url, headers=console.headers(), json={"enabled": False})

        entries = client.get(_url(t), headers=console.headers()).json()["entries"]
        assert [(e["source"], e["event"]) for e in entries[:4]] == [
            ("lifecycle", "example_prompting_disabled"),
            ("console", "example_prompting_set"),
            ("lifecycle", "example_prompting_enabled"),
            ("console", "example_prompting_set"),
        ]
        # Both kinds of row carry the database's time: no hour between them.
        from datetime import datetime as real_datetime

        stamps = [real_datetime.fromisoformat(e["at"]) for e in entries[:4]]
        assert max(stamps) - min(stamps) < timedelta(minutes=5)


def test_rows_with_the_same_timestamp_come_back_in_the_same_order_every_time(client):
    """Ties are ordered by id, so a page never shuffles between two reads."""
    with _Console() as console, _Tenant("Acme Test Audit Ties") as t:
        ids = sorted(str(uuid4()) for _ in range(3))
        with platform_session() as session:
            for action_id in ids:
                session.execute(
                    text(
                        "INSERT INTO admin_actions (id, platform_admin_user_id, action, target_tenant_id, "
                        "target_type, payload, created_at) VALUES (:id, :by, 'tie_test', :t, 'tenant', "
                        "'{}'::jsonb, '2026-01-01T00:00:00Z')"
                    ),
                    {"id": action_id, "by": str(console.user_id), "t": str(t.tenant_id)},
                )

        def ties():
            entries = client.get(_url(t) + "?limit=200", headers=console.headers()).json()["entries"]
            return [e["id"] for e in entries if e["event"] == "tie_test"]

        first = ties()
        assert first == sorted(ids, reverse=True)
        assert ties() == first
