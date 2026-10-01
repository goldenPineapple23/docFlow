"""
Stage 3b (Q3): what a tenant, Postmark and the founder see when Supabase
Storage can't be reached.

* A web upload answers DOC-025 with a 503, and nothing was received: no
  document row, nothing enqueued.
* The founder gets ONE `storage_unavailable` alert an hour for the whole
  platform, however many tenants hit the outage (the global open-alert
  dedupe index, 0011, with no new migration).
* The inbound-mail webhook answers 503 -- never 403, which Postmark treats as
  final -- so Postmark sends the email again later.

Storage is replaced by the in-memory fake set to fail; the database is the
real one (these run against staging from the founder's machine, and against
the local stack in CI). All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import pytest
from docflow_core import storage
from docflow_core.config import get_settings
from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import requires_documents_schema
from tests.test_documents_upload import JWT_SECRET, _FakeCeleryClient, _TestTenant


def _fake_storage():
    """The one fake lives with the core tests (packages/core/tests), so it is
    loaded by path rather than duplicated."""
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[3] / "packages/core/tests/storage_fake.py"
    spec = importlib.util.spec_from_file_location("docflow_storage_fake", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FakeStorage()


def _open_storage_alerts_this_hour() -> list[dict]:
    with platform_session() as session:
        return [
            dict(r)
            for r in session.execute(
                text(
                    "SELECT id, tenant_id FROM founder_alerts "
                    "WHERE type = 'storage_unavailable' AND acknowledged_at IS NULL "
                    "AND dedupe_key = 'storage_unavailable:' || "
                    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24')"
                )
            ).mappings()
        ]


def _delete_alerts_for(*tenant_ids) -> None:
    with platform_session() as session:
        for tid in tenant_ids:
            session.execute(
                text("DELETE FROM founder_alerts WHERE tenant_id = :t AND type = 'storage_unavailable'"),
                {"t": str(tid)},
            )
            session.execute(text("DELETE FROM email_outbox WHERE tenant_id = :t"), {"t": str(tid)})


@requires_documents_schema
def test_an_upload_during_an_outage_is_doc_025_and_nothing_is_received(client, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    get_settings.cache_clear()
    fake_celery = _FakeCeleryClient()
    monkeypatch.setattr("app.celery_client.celery_client", fake_celery)
    fake = _fake_storage()
    fake.fail_with = "unavailable"
    try:
        with storage.set_backend_for_tests(fake), _TestTenant(
            "Acme Test Outage", "owner-outage@example.test"
        ) as tenant:
            try:
                response = client.post(
                    "/documents/upload",
                    headers={"Authorization": f"Bearer {tenant.token()}"},
                    files={"file": ("po.txt", b"PO Number: TEST-0025\n", "text/plain")},
                )
                assert response.status_code == 503
                assert response.json()["detail"]["code"] == "DOC-025"
                assert fake_celery.sent == []
                with platform_session() as session:
                    documents = session.execute(
                        text("SELECT count(*) FROM documents WHERE tenant_id = :t"),
                        {"t": str(tenant.tenant_id)},
                    ).scalar_one()
                assert documents == 0
            finally:
                _delete_alerts_for(tenant.tenant_id)
    finally:
        get_settings.cache_clear()


@requires_documents_schema
def test_one_outage_is_one_alert_an_hour_for_the_whole_platform(client, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    get_settings.cache_clear()
    monkeypatch.setattr("app.celery_client.celery_client", _FakeCeleryClient())
    fake = _fake_storage()
    fake.fail_with = "unavailable"
    before = _open_storage_alerts_this_hour()
    if before:
        pytest.skip("a real storage_unavailable alert is already open this hour on this database")
    try:
        with storage.set_backend_for_tests(fake), _TestTenant(
            "Acme Test Outage One", "owner-outage-1@example.test"
        ) as first, _TestTenant("Acme Test Outage Two", "owner-outage-2@example.test") as second:
            try:
                for tenant in (first, second, first):
                    response = client.post(
                        "/documents/upload",
                        headers={"Authorization": f"Bearer {tenant.token()}"},
                        files={"file": ("po.txt", b"PO Number: TEST-0026\n", "text/plain")},
                    )
                    assert response.status_code == 503
                alerts = _open_storage_alerts_this_hour()
                assert len(alerts) == 1
                assert str(alerts[0]["tenant_id"]) == str(first.tenant_id)  # the first to hit it
            finally:
                _delete_alerts_for(first.tenant_id, second.tenant_id)
    finally:
        get_settings.cache_clear()


def test_the_inbound_webhook_answers_503_during_an_outage_so_postmark_retries(client, monkeypatch):
    """Offline: every step before processing is replaced; what's tested is
    the status Postmark sees and that the alert is raised."""
    from uuid import uuid4

    from docflow_core import email_intake, founder_alerts

    import app.routers.email_intake as route

    tenant_id = uuid4()
    raised: list[dict] = []
    monkeypatch.setattr(route, "check_inbound_webhook_credentials", lambda authorization: None)
    monkeypatch.setattr(route, "inbound_source_ip_note", lambda host: None)
    monkeypatch.setattr(email_intake, "resolve_tenant_by_token", lambda token: (tenant_id, "active"))
    monkeypatch.setattr(email_intake, "parse_postmark_payload", lambda payload: object())

    def down(tid, parsed):
        raise storage.StorageUnavailableError("fake outage")

    monkeypatch.setattr(email_intake, "process_inbound_email", down)
    def record(tid, *, where):
        raised.append({"t": tid, "w": where})

    monkeypatch.setattr(founder_alerts, "alert_storage_unavailable", record)

    response = client.post("/intake/email/some-token", json={"any": "body"})

    assert response.status_code == 503  # a 5xx, and never 403
    assert raised == [{"t": tenant_id, "w": "email_intake"}]
