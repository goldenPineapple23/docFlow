"""
The inbound webhook's own fault raises an alert (Phase 5.5 Stage 5,
blocker 4; DECISIONS.md D-210).

Before this, a failure of ours inside the webhook answered 500 and was
logged, and nothing else happened. Postmark retries ten times over about
ten hours, then lists the message as Inbound Error on its own Inbound
page, so an order could be lost with nobody told. Now, for a known tenant,
the keyed `intake_failed` alert (INT-011) is raised once per state, and the
answer is the same 500 as before, so Postmark keeps resending.

What is unchanged, and pinned here: every answer the webhook already gives
(401, 400, 404, 503, 200) raises no `intake_failed`, and an attachment that
fails on our side (PR 1b-i) is still that attachment's DOC-030 with a 200.
A fault before the tenant is known is logged only (founder, 2026-10-19,
Option A).

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from docflow_core import email_intake, founder_alerts
from docflow_core.config import get_settings
from docflow_core.db import platform_session
from docflow_core.storage import StorageUnavailableError
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from tests.conftest import intake_webhook_headers, requires_database
from tests.test_email_intake import _FakeCeleryClient, _pm_attachment, _pm_payload, _TestIntakeTenant

pytestmark = requires_database

SECRET = "Acme Test secret value 4417"
SENDER = "secret-buyer-b4@example.test"
SUBJECT = "PO TEST-B401 subject"
GOOD_PO = b"PURCHASE ORDER\nPO Number: TEST-B401\n"


@pytest.fixture(autouse=True)
def _founder_email(monkeypatch):
    """So each alert's e-mail is written too, and can be checked."""
    monkeypatch.setenv("FOUNDER_ALERT_EMAIL", "founder@example.test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def lenient_client() -> TestClient:
    # A server error comes back as a response, as Postmark would receive it.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def fake_celery(monkeypatch) -> _FakeCeleryClient:
    fake = _FakeCeleryClient()
    monkeypatch.setattr("docflow_core.email_intake.celery_client", fake)
    return fake


def _post(client: TestClient, token: str, payload: dict | None = None):
    payload = payload or _pm_payload(SENDER, subject=SUBJECT, attachments=[_pm_attachment("po.txt", GOOD_PO)])
    return client.post(f"/intake/email/{token}", json=payload, headers=intake_webhook_headers())


def _breaks(monkeypatch, name: str) -> None:
    """`email_intake.<name>` raises something of ours, its message a secret."""

    def broken(*args, **kwargs):
        raise RuntimeError(f"our bug: {SECRET}")

    monkeypatch.setattr(email_intake, name, broken)


def _alerts(tenant_id, alert_type: str = "intake_failed") -> list[dict]:
    with platform_session() as session:
        rows = (
            session.execute(
                text(
                    "SELECT a.id, a.payload, a.state_key, a.acknowledged_at, o.subject, o.body_text "
                    "FROM founder_alerts a LEFT JOIN email_outbox o ON o.id = a.email_outbox_id "
                    "WHERE a.tenant_id = :t AND a.type = :type ORDER BY a.created_at"
                ),
                {"t": str(tenant_id), "type": alert_type},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def _all_intake_failed() -> int:
    with platform_session() as session:
        return int(
            session.execute(
                text("SELECT count(*) FROM founder_alerts WHERE type = 'intake_failed'")
            ).scalar_one()
        )


# ── ours, for a known tenant: one alert, the same 500 ────────────────────────


def test_a_fault_of_ours_answers_500_and_raises_one_keyed_alert_with_nothing_of_the_email(
    lenient_client, monkeypatch, fake_celery, caplog
):
    _breaks(monkeypatch, "process_inbound_email")
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        with caplog.at_level("ERROR"):
            response = _post(lenient_client, tenant.token)

        assert response.status_code == 500, "a 403 would stop Postmark; anything but 5xx loses the retry"
        assert SECRET not in response.text
        (alert,) = _alerts(tenant.tenant_id)
        payload = alert["payload"]
        assert set(payload) == {"step", "exception_type", "location", "release", "error_code"}
        assert (payload["step"], payload["exception_type"], payload["error_code"]) == (
            "email_intake_webhook",
            "RuntimeError",
            "INT-011",
        )
        assert alert["state_key"].startswith(
            f"fault:intake_failed:{tenant.tenant_id}:email_intake_webhook:RuntimeError:"
        )
        assert alert["body_text"], "the alert's e-mail was not written"
        flat = f"{payload} {alert['subject']} {alert['body_text']}"
        for never in (SENDER, SUBJECT, SECRET, "po.txt", "TEST-B401"):
            assert never not in flat
        # Nothing of the e-mail was kept: its own transaction rolled back.
        assert tenant.documents() == [] and tenant.raw_emails() == [] and tenant.intake_rejections() == []
        assert fake_celery.sent == []

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "unhandled_error" in logged and "RuntimeError" in logged, "the 500 is no longer logged as before"
    assert SECRET not in logged


def test_postmarks_retries_of_one_email_raise_one_alert(lenient_client, monkeypatch, fake_celery):
    _breaks(monkeypatch, "process_inbound_email")
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        payload = _pm_payload(SENDER, subject=SUBJECT, message_id=str(uuid4()))
        statuses = [_post(lenient_client, tenant.token, payload).status_code for _ in range(3)]
        assert statuses == [500, 500, 500]
        assert len(_alerts(tenant.tenant_id)) == 1


def test_an_acknowledged_state_stays_quiet(lenient_client, monkeypatch, fake_celery):
    _breaks(monkeypatch, "process_inbound_email")
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        assert _post(lenient_client, tenant.token).status_code == 500
        (alert,) = _alerts(tenant.tenant_id)
        with platform_session() as session:
            session.execute(
                text("UPDATE founder_alerts SET acknowledged_at = now() WHERE id = :id"),
                {"id": str(alert["id"])},
            )
        assert _post(lenient_client, tenant.token).status_code == 500
        assert len(_alerts(tenant.tenant_id)) == 1, "an acknowledged state was raised again"


def test_a_fault_at_another_place_in_our_code_is_a_second_alert(lenient_client, monkeypatch, fake_celery):
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        with monkeypatch.context() as patch:
            _breaks(patch, "process_inbound_email")
            assert _post(lenient_client, tenant.token).status_code == 500
        with monkeypatch.context() as patch:
            _breaks(patch, "parse_postmark_payload")
            assert _post(lenient_client, tenant.token).status_code == 500
        alerts = _alerts(tenant.tenant_id)
        assert len(alerts) == 2
        assert alerts[0]["payload"]["location"] != alerts[1]["payload"]["location"]


def test_an_alert_that_cant_be_written_leaves_the_500_as_it_was(
    lenient_client, monkeypatch, fake_celery, caplog
):
    _breaks(monkeypatch, "process_inbound_email")

    def refused(*args, **kwargs):
        raise RuntimeError("the alert write failed")

    monkeypatch.setattr(founder_alerts, "raise_alert", refused)
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        with caplog.at_level("ERROR"):
            response = _post(lenient_client, tenant.token)
        assert response.status_code == 500
        assert _alerts(tenant.tenant_id) == []
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "founder_alert_not_raised" in logged and "alert=intake_failed" in logged


# ── unchanged: what the webhook already answers raises no intake_failed ──────


def test_an_accepted_email_answers_200_and_raises_nothing(client, fake_celery):
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        response = _post(client, tenant.token)
        assert response.status_code == 200 and response.json()["outcome"] == "processed"
        assert _alerts(tenant.tenant_id) == []


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (b"{not json", 400),
        (b'{"FromFull": "not an object"}', 400),
    ],
    ids=["body not JSON", "malformed payload"],
)
def test_the_senders_malformed_requests_are_still_400_and_raise_nothing(client, fake_celery, body, status):
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        response = client.post(
            f"/intake/email/{tenant.token}",
            content=body,
            headers={**intake_webhook_headers(), "Content-Type": "application/json"},
        )
        assert response.status_code == status
        assert _alerts(tenant.tenant_id) == []


def test_an_unknown_token_is_still_404_and_raises_nothing(client):
    before = _all_intake_failed()
    response = client.post(
        f"/intake/email/{uuid4().hex}", json=_pm_payload(SENDER), headers=intake_webhook_headers()
    )
    assert response.status_code == 404
    assert _all_intake_failed() == before


@pytest.mark.usefixtures("cleans_up_refusal_alerts")
def test_refused_credentials_are_still_401_and_raise_no_intake_failed(client):
    before = _all_intake_failed()
    response = client.post(
        f"/intake/email/{uuid4().hex}",
        json=_pm_payload(SENDER),
        headers=intake_webhook_headers(password="wrong-for-this-test"),
    )
    assert response.status_code == 401
    assert _all_intake_failed() == before


def test_storage_unavailable_is_still_503_and_raises_no_intake_failed(client, monkeypatch, fake_celery):
    def unavailable(*args, **kwargs):
        raise StorageUnavailableError("staging Storage is out (test)")

    monkeypatch.setattr(email_intake, "process_inbound_email", unavailable)
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        response = _post(client, tenant.token)
        assert response.status_code == 503
        # Its own alert is platform-wide once an hour (Stage 3b), so it is not counted here.
        assert _alerts(tenant.tenant_id) == []


def test_an_attachment_that_fails_on_our_side_is_still_its_doc_030_with_a_200(
    client, monkeypatch, fake_celery
):
    """PR 1b-i's rule stands (founder, 2026-10-19): one attachment's check
    failing on our side refuses that attachment, keeps its keyed
    `document_failed` alert, answers 200, and raises no `intake_failed`."""
    from tests.test_email_intake_narrowing import BROKEN, _validation_fails_for

    _validation_fails_for(monkeypatch, "broken.docx")
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        payload = _pm_payload(
            SENDER, attachments=[_pm_attachment("broken.docx", BROKEN, "application/octet-stream")]
        )
        response = _post(client, tenant.token, payload)
        assert response.status_code == 200
        assert [row["error_code"] for row in tenant.intake_rejections()] == ["DOC-030"]
        assert len(_alerts(tenant.tenant_id, "document_failed")) == 1
        assert _alerts(tenant.tenant_id) == []


# ── before the tenant is known: logged only (Option A; the gap in D-210) ─────


def test_a_fault_before_the_tenant_is_known_is_logged_and_raises_no_alert(
    lenient_client, monkeypatch, caplog
):
    _breaks(monkeypatch, "resolve_tenant_by_token")
    before = _all_intake_failed()
    with caplog.at_level("ERROR"):
        response = lenient_client.post(
            f"/intake/email/{uuid4().hex}", json=_pm_payload(SENDER), headers=intake_webhook_headers()
        )
    assert response.status_code == 500
    assert _all_intake_failed() == before
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "unhandled_error" in logged and "RuntimeError" in logged
    assert SECRET not in logged
