"""
The inbound webhook's two decoding blocks, narrowed (Phase 5.5 Stage 4,
PR 1a; DECISIONS.md D-203).

Both caught every exception and answered 400 "malformed": the body that
isn't JSON (`routers/email_intake.py`) and the attachment that isn't base64
(`docflow_core/email_intake.py`). They now catch what reading the SENDER's
content raises. A failure of ours is no longer called a malformed request:
it goes to the API's last handler, which logs it with its frames and answers
500 in catalog words.

Postmark retries a 400 and a 500 alike (ten times over about ten hours; only
a 403 stops it), so this changes what the record says, not whether the mail
is tried again.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from docflow_core import email_intake
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import intake_webhook_headers, requires_database
from tests.test_email_intake import _pm_payload

SECRET = "Acme Test secret value 9931"


@pytest.fixture
def lenient_client() -> TestClient:
    # Returns a server error as a response, as Postmark would receive it,
    # instead of re-raising it into the test.
    return TestClient(app, raise_server_exceptions=False)


def _url() -> str:
    return f"/intake/email/{uuid4().hex}"


# ── the sender's content: still 400 ──────────────────────────────────────────


@requires_database
@pytest.mark.parametrize(
    "body",
    [b"{not json", b"", b"\xff\xfe\x00not utf-8"],
    ids=["not JSON", "empty", "not UTF-8"],
)
def test_a_body_that_is_not_json_is_still_a_malformed_request(client, body):
    response = client.post(
        _url(), content=body, headers={**intake_webhook_headers(), "Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert "not valid JSON" in response.json()["detail"]


@pytest.mark.parametrize(
    "content",
    ["this is not base64!!", "QUJD=", 12345, ["QUJD"]],
    ids=["not base64", "bad padding", "a number", "a list"],
)
def test_an_attachment_that_is_not_base64_text_is_still_a_malformed_payload(content):
    payload = _pm_payload("buyer@example.test", attachments=[{"Name": "po.txt", "Content": content}])
    with pytest.raises(email_intake.MalformedPayloadError, match="invalid base64"):
        email_intake.parse_postmark_payload(payload)


# ── ours: never called malformed ─────────────────────────────────────────────


@requires_database
def test_a_failure_of_ours_while_reading_the_body_answers_500_not_400(lenient_client, monkeypatch, caplog):
    from starlette.requests import Request

    async def broken(self):
        raise RuntimeError(f"our bug: {SECRET}")

    monkeypatch.setattr(Request, "json", broken)
    with caplog.at_level("ERROR"):
        response = lenient_client.post(
            _url(), json=_pm_payload("buyer@example.test"), headers=intake_webhook_headers()
        )

    assert response.status_code == 500, "a failure of ours was answered as the sender's malformed request"
    assert "Malformed" not in response.text and SECRET not in response.text
    assert "unhandled_error" in caplog.text and "RuntimeError" in caplog.text
    assert SECRET not in caplog.text


def test_a_failure_of_ours_while_decoding_an_attachment_is_not_a_malformed_payload(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("our bug in the decoder")

    monkeypatch.setattr(email_intake.base64, "b64decode", broken)
    payload = _pm_payload("buyer@example.test", attachments=[{"Name": "po.txt", "Content": "QUJD"}])
    with pytest.raises(RuntimeError, match="our bug"):
        email_intake.parse_postmark_payload(payload)


# ── one attachment's check failing on our side (PR 1b-i; founder, 2026-10-16) ──


def _validation_fails_for(monkeypatch, filename: str) -> None:
    """`validate_upload` raises something of ours for one attachment only.
    Since PR 1b-i the zip scan no longer turns every exception into DOC-005,
    so an unexpected one passes through validation to intake."""
    from docflow_core import file_types

    real = file_types.validate_upload

    def validate(content, claimed_filename):
        if claimed_filename == filename:
            raise RuntimeError(SECRET)
        return real(content, claimed_filename)

    monkeypatch.setattr(file_types, "validate_upload", validate)


BROKEN = b"PK\x03\x04 not really a document"
GOOD_PO = b"PURCHASE ORDER\nPO Number: TEST-1B01\n"


@requires_database
def test_a_good_attachment_beside_one_that_fails_on_our_side_is_still_read(client, monkeypatch, caplog):
    """The faulting attachment is refused as DOC-030 (an intake rejection),
    the other is read, and the webhook answers 200: never the whole e-mail's
    loss to a retry (founder, 2026-10-16). The type and our location are
    logged, never the message."""
    from tests.test_email_intake import _FakeCeleryClient, _pm_attachment, _TestIntakeTenant

    fake_celery = _FakeCeleryClient()
    monkeypatch.setattr("docflow_core.email_intake.celery_client", fake_celery)
    _validation_fails_for(monkeypatch, "broken.docx")
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        payload = _pm_payload(
            "fresh-buyer-1b@example.test",
            attachments=[
                _pm_attachment("broken.docx", BROKEN, "application/octet-stream"),
                _pm_attachment("po.txt", GOOD_PO),
            ],
        )
        with caplog.at_level("ERROR"):
            response = client.post(
                f"/intake/email/{tenant.token}", json=payload, headers=intake_webhook_headers()
            )
        assert response.status_code == 200
        assert response.json()["outcome"] == "processed"
        assert len(tenant.documents()) == 1 and len(fake_celery.sent) == 1
        assert [row["error_code"] for row in tenant.intake_rejections()] == ["DOC-030"]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "email_attachment_validation_fault" in logged and "error_type=RuntimeError" in logged
    assert SECRET not in logged


def _intake_alerts(tenant_id) -> list[dict]:
    from docflow_core.db import platform_session
    from sqlalchemy import text

    with platform_session() as session:
        rows = session.execute(
            text(
                "SELECT payload, state_key FROM founder_alerts "
                "WHERE tenant_id = :t AND type = 'document_failed' ORDER BY created_at"
            ),
            {"t": str(tenant_id)},
        ).mappings().all()
    return [dict(r) for r in rows]


@requires_database
def test_an_email_whose_only_attachment_fails_on_our_side_still_answers_200(client, monkeypatch):
    from tests.test_email_intake import _FakeCeleryClient, _pm_attachment, _TestIntakeTenant

    fake_celery = _FakeCeleryClient()
    monkeypatch.setattr("docflow_core.email_intake.celery_client", fake_celery)
    _validation_fails_for(monkeypatch, "broken.docx")
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        payload = _pm_payload(
            "fresh-buyer-1b2@example.test",
            attachments=[_pm_attachment("broken.docx", BROKEN, "application/octet-stream")],
        )
        response = client.post(
            f"/intake/email/{tenant.token}", json=payload, headers=intake_webhook_headers()
        )
        assert response.status_code == 200
        assert tenant.documents() == [] and fake_celery.sent == []
        assert [row["error_code"] for row in tenant.intake_rejections()] == ["DOC-030"]


@requires_database
def test_the_intake_doc_030_alert_names_the_rejection_and_nothing_of_the_email(client, monkeypatch):
    """Founder, 2026-10-16, (a1): DOC-030 promises "we're already on it", so
    the keyed alert is raised at intake too, once per state, with step
    `email_intake`. With no document, the payload names the intake rejection;
    it carries no body, no sender address, no message, no library stderr. A
    second e-mail hit by the same fault raises nothing more."""
    from tests.test_email_intake import _FakeCeleryClient, _pm_attachment, _TestIntakeTenant

    monkeypatch.setattr("docflow_core.email_intake.celery_client", _FakeCeleryClient())
    _validation_fails_for(monkeypatch, "broken.docx")
    sender = "secret-buyer-1b3@example.test"
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        for subject in ("PO TEST-1B03 first", "PO TEST-1B04 second"):
            payload = _pm_payload(
                sender,
                subject=subject,
                attachments=[_pm_attachment("broken.docx", BROKEN, "application/octet-stream")],
            )
            response = client.post(
                f"/intake/email/{tenant.token}", json=payload, headers=intake_webhook_headers()
            )
            assert response.status_code == 200

        rejections = tenant.intake_rejections()
        assert [row["error_code"] for row in rejections] == ["DOC-030", "DOC-030"]
        (alert,) = _intake_alerts(tenant.tenant_id)
        payload = alert["payload"]
        assert set(payload) == {
            "intake_rejection_id", "step", "exception_type", "location", "release", "error_code"
        }
        assert (payload["step"], payload["exception_type"], payload["error_code"]) == (
            "email_intake", "RuntimeError", "DOC-030"
        )
        assert alert["state_key"].startswith(
            f"fault:document_failed:{tenant.tenant_id}:email_intake:RuntimeError:"
        )
        flat = str(payload)
        for never in (sender, SECRET, "PO TEST-1B03", "broken.docx"):
            assert never not in flat
