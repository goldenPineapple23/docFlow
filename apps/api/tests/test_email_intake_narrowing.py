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
