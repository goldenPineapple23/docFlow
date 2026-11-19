"""
The repo's fixture emails through the webhook (B1 slice 4 commit 7;
docs/designs/b1.md section 9; D-246). Each `.eml` in
tests/fixtures/mail/ is converted by scripts/eml_to_postmark.py and posted
to POST /intake/email/{token} with the webhook's Basic credentials, against
CI's database and Storage. What each must do is in expected.json, written
before any recording. All data is fictional.

The converter itself is checked here with no database: its output carries
Postmark's documented inbound fields, and the webhook's own parser reads it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest
from docflow_core import email_intake
from docflow_core.constants import MAIL_LABEL_TEXT_CHAR_BUDGET
from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import intake_webhook_headers, requires_mail_body_schema
from tests.test_email_intake import _FakeCeleryClient, _TestIntakeTenant

REPO = Path(__file__).resolve().parents[3]
MAIL = Path(__file__).resolve().parent / "fixtures" / "mail"
EXPECTED = {
    name: entry
    for name, entry in json.loads((MAIL / "expected.json").read_text(encoding="utf-8")).items()
    if not name.startswith("_")
}
LABEL_TASK = "docflow.label_mail_body"

# Postmark's documented inbound payload (its "Inbound webhook" reference).
POSTMARK_INBOUND_FIELDS = {
    "From", "FromFull", "FromName", "To", "ToFull", "Cc", "CcFull", "ReplyTo", "Subject", "MessageID",
    "Date", "MailboxHash", "TextBody", "HtmlBody", "StrippedTextReply", "Tag", "Headers", "Attachments",
}


def _converter():
    spec = importlib.util.spec_from_file_location("eml_to_postmark", REPO / "scripts" / "eml_to_postmark.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(name: str) -> dict:
    return _converter().convert((MAIL / f"{name}.eml").read_bytes())


def test_every_fixture_has_an_expected_outcome_and_every_outcome_a_fixture():
    assert sorted(EXPECTED) == sorted(p.stem for p in MAIL.glob("*.eml"))
    assert len(EXPECTED) == 16
    # Founder, 2026-11-19: 14 label calls, and an extraction recorded for
    # every text document (11, with 15 from the second run's ruling).
    assert sum(e["label_call"] for e in EXPECTED.values()) == 14
    text_documents = sorted(n for n, e in EXPECTED.items() if e["text_document"])
    assert sorted(n for n, e in EXPECTED.items() if e["extraction"] is not None) == text_documents
    assert len(text_documents) == 11


def test_fixture_13s_order_lines_come_only_after_the_label_cut():
    """Founder, 2026-11-19 (option A): more than the label budget of text
    with no SKU-like string and no digit, then the order. The label is sent
    the cut text only; extraction reads the order past it."""
    body = _payload("13_text_over_budget")["TextBody"]
    order_starts = body.index("Please ship PO TEST-PO-113:")
    before = body[:order_starts]
    assert len(before) > MAIL_LABEL_TEXT_CHAR_BUDGET
    assert not re.search(r"\d", before)
    assert not re.search(r"[A-Za-z]+-[A-Za-z]*\d", before)
    for sku, quantity in EXPECTED["13_text_over_budget"]["extraction"]["lines"]:
        assert f"{quantity} x {sku} " in body[order_starts:]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_converter_gives_postmarks_shape_and_the_webhook_reads_it(name):
    payload = _payload(name)
    assert set(payload) == POSTMARK_INBOUND_FIELDS
    for attachment in payload["Attachments"]:
        assert set(attachment) == {"Name", "Content", "ContentType", "ContentLength", "ContentID"}
    parsed = email_intake.parse_postmark_payload(payload)
    assert parsed.sender_email.endswith(".example")
    assert parsed.message_id == f"{name}@mail.acmetest.example"
    assert len(parsed.attachments) == EXPECTED[name]["attachment_documents"]


@pytest.fixture()
def celery(monkeypatch):
    fake = _FakeCeleryClient()
    monkeypatch.setattr("docflow_core.email_intake.celery_client", fake)
    return fake


def _one(sql: str, **params) -> dict:
    with platform_session() as session:
        rows = session.execute(text(sql), params).mappings().all()
    assert len(rows) == 1, rows
    return dict(rows[0])


@requires_mail_body_schema
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_fixture_email_is_stored_and_sent_for_labelling_as_expected(client, celery, name):
    expected = EXPECTED[name]
    payload = _payload(name)
    body = email_intake.choose_body(payload["TextBody"], payload["HtmlBody"])
    with _TestIntakeTenant("Acme Test Distributor -- mail fixtures") as tenant:
        if expected["strict_mode"]:
            with platform_session() as session:
                session.execute(
                    text(
                        "UPDATE tenants SET strict_sender_mode = true, sender_allowlist = '{}' "
                        "WHERE id = :t"
                    ),
                    {"t": str(tenant.tenant_id)},
                )
        url = f"/intake/email/{tenant.token}"
        response = client.post(url, json=payload, headers=intake_webhook_headers())
        assert response.status_code == 200

        raw = _one(
            "SELECT id, body_state, body_kind, body_sha256, body_html_path "
            "FROM raw_emails WHERE tenant_id = :t",
            t=str(tenant.tenant_id),
        )
        if body is None:
            assert raw["body_state"] == "none" and raw["body_sha256"] is None
        else:
            assert raw["body_kind"] == body.kind
            assert raw["body_sha256"] == hashlib.sha256(body.content).hexdigest()
            assert (raw["body_html_path"] is not None) == (body.html_also is not None)
            assert raw["body_state"] == ("held" if expected["held"] else "pending")

        tasks = [sent for sent in celery.sent if sent["name"] == LABEL_TASK]
        if expected["label_call"]:
            assert tasks == [
                {"name": LABEL_TASK, "args": [str(tenant.tenant_id), str(raw["id"])], "queue": "interactive"}
            ]
        else:
            assert tasks == []

        documents = tenant.documents()
        attachments = [d for d in documents if d["source"] == "email" and d["status"] != "quarantined"]
        if expected["held"]:
            [held] = documents
            assert held["status"] == "quarantined" and held["quarantine_reason"] == "sender_not_allowed"
        else:
            assert len(attachments) == expected["attachment_documents"]
        codes = [row["error_code"] for row in tenant.intake_rejections()]
        if expected["ignored_mail"] and expected["ignored_mail"]["code"] == "INT-001":
            assert codes == ["INT-001"]
        else:
            # INT-012 is the labelling task's, after the label: not the webhook's.
            assert "INT-001" not in codes and "INT-012" not in codes
