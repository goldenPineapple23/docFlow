"""
The repo's fixture emails, end to end against CI's database, Storage and
parse service, with the model's answers RECORDED from one live run
(scripts/record_mail_fixtures.py; founder's word, 2026-11-19; B1 slice 4
commit 7; docs/designs/b1.md section 9; D-246). Each recording holds the
sha256 of the exact text that was sent: a request here with any other text
fails, so a recording never stands in for a text it wasn't made from. No
recorded confidence is edited. The below-threshold path is covered by the
stub tests and decide_body's table tests, not by a recording (gap 5).

For each fixture with a label call: the webhook's own function stores the
email and sends the task; the labelling task decides from the recorded
label; a text document is extracted from the recorded extraction where one
was recorded. What each must do is the API suite's expected.json. No
document ever carries VAL-020 (b1.md section 12). All data is fictional.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from docflow_core import email_intake
from docflow_core.constants import MAIL_LABEL_TEXT_CHAR_BUDGET
from docflow_core.db import platform_session
from sqlalchemy import text

import app.tasks.label_mail_body as label_task
import app.tasks.parse_and_extract as document_task
from tests.conftest import requires_documents_schema, requires_mail_body_schema
from tests.db_helpers import FakeAnthropic, WorkerTestTenant

pytestmark = [requires_documents_schema, requires_mail_body_schema]

REPO = Path(__file__).resolve().parents[3]
MAIL = REPO / "apps" / "api" / "tests" / "fixtures" / "mail"
RECORDINGS = Path(__file__).resolve().parent / "fixtures" / "mail_recordings"
EXPECTED = {
    name: entry
    for name, entry in json.loads((MAIL / "expected.json").read_text(encoding="utf-8")).items()
    if not name.startswith("_")
}
LABELLED = sorted(name for name, entry in EXPECTED.items() if entry["label_call"])


def _converter():
    spec = importlib.util.spec_from_file_location("eml_to_postmark", REPO / "scripts" / "eml_to_postmark.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _recording(name: str, kind: str) -> dict[str, Any]:
    path = RECORDINGS / f"{name}.{kind}.json"
    assert path.exists(), f"no recording {path.name}: the recording run (on the founder's word) makes it"
    return json.loads(path.read_text(encoding="utf-8"))


def _request_text(request: dict[str, Any]) -> str:
    """Every text block of a request's messages, in order -- as the recording
    script hashes it."""
    return "".join(
        block.get("text", "")
        for message in request["messages"]
        for block in (message["content"] if isinstance(message["content"], list) else [])
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _RecordedLabel:
    """The label call answered from its recording, only for the exact text
    the recording was made from."""

    def __init__(self, recording: dict[str, Any]):
        self.recording = recording
        self.requests: list[dict[str, Any]] = []

    @property
    def messages(self) -> "_RecordedLabel":
        return self

    def with_options(self, **kwargs: Any) -> "_RecordedLabel":
        return self

    def count_tokens(self, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(input_tokens=self.recording["input_tokens"])

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(kwargs)
        assert _sha256(_request_text(kwargs)) == self.recording["sent_text_sha256"], "not the recorded text"
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self.recording["response"]))],
            usage=SimpleNamespace(
                input_tokens=self.recording["input_tokens"], output_tokens=self.recording["output_tokens"]
            ),
            stop_reason="end_turn",
        )


class _RecordedExtraction(FakeAnthropic):
    """The extraction call answered from its recording, only for the exact
    request text it was made from."""

    def __init__(self, recording: dict[str, Any]):
        super().__init__(recording["response"])
        self.recording = recording

    def stream(self, **kwargs: Any):
        assert _sha256(_request_text(kwargs)) == self.recording["request_text_sha256"], (
            "not the recorded text"
        )
        return super().stream(**kwargs)


def _rows(sql: str, **params) -> list[dict]:
    with platform_session() as session:
        return [dict(r) for r in session.execute(text(sql), params).mappings().all()]


def _receive(monkeypatch, tenant: WorkerTestTenant, name: str) -> UUID:
    """The webhook's own function stores the fixture email (no HTTP: the
    API suite posts each one)."""
    with platform_session() as session:
        session.execute(
            text("UPDATE tenants SET intake_address_active = true WHERE id = :t"),
            {"t": str(tenant.tenant_id)},
        )
    sent: list[dict] = []
    monkeypatch.setattr(
        email_intake.celery_client,
        "send_task",
        lambda task, args=None, queue=None: sent.append({"name": task, "args": args}),
    )
    payload = _converter().convert((MAIL / f"{name}.eml").read_bytes())
    result = email_intake.process_inbound_email(
        tenant.tenant_id, email_intake.parse_postmark_payload(payload)
    )
    assert result.raw_email_id is not None
    assert [s["args"] for s in sent if s["name"] == label_task.label_mail_body.name] == [
        [str(tenant.tenant_id), str(result.raw_email_id)]
    ]
    return UUID(str(result.raw_email_id))


@pytest.mark.parametrize("name", LABELLED)
def test_each_fixture_email_is_decided_from_its_recorded_label_as_expected(monkeypatch, name):
    expected = EXPECTED[name]
    recording = _recording(name, "label")
    with WorkerTestTenant("Acme Test Mail Fixtures") as tenant:
        raw_email_id = _receive(monkeypatch, tenant, name)
        client = _RecordedLabel(recording)
        monkeypatch.setattr(label_task.anthropic, "Anthropic", lambda *a, **k: client)
        label_task.label_mail_body(str(tenant.tenant_id), str(raw_email_id))

        assert len(client.requests) == 1
        [email] = _rows(
            "SELECT body_state, body_label, outcome FROM raw_emails WHERE id = :e", e=str(raw_email_id)
        )
        assert email["body_state"] == "labelled" and email["body_label"] == recording["response"]["label"]
        texts = _rows(
            "SELECT id FROM documents WHERE raw_email_id = :e AND email_part = 'body'", e=str(raw_email_id)
        )
        assert len(texts) == (1 if expected["text_document"] else 0)
        attachments = _rows(
            "SELECT id FROM documents WHERE raw_email_id = :e AND email_part = 'attachment'",
            e=str(raw_email_id),
        )
        assert len(attachments) == expected["attachment_documents"]
        ignored = _rows(
            "SELECT error_code, mail_label FROM intake_rejections WHERE raw_email_id = :e",
            e=str(raw_email_id),
        )
        if expected["ignored_mail"]:
            assert ignored == [
                {
                    "error_code": expected["ignored_mail"]["code"],
                    "mail_label": expected["ignored_mail"]["label"],
                }
            ]
        else:
            assert ignored == []
        runs = _rows(
            "SELECT run_state, model_id FROM extraction_runs WHERE raw_email_id = :e ORDER BY created_at",
            e=str(raw_email_id),
        )
        assert [r["run_state"] for r in runs] == ["started", "finished"]

        sent = _request_text(client.requests[0])
        if name == "12_html_script_and_hidden_block":
            # Never the markup or the script; text hidden by CSS is kept (b1.md section 3).
            assert "document.write" not in sent and "<td>" not in sent
            assert "TEST-320" in sent and "Hidden note" in sent
        if name == "13_text_over_budget":
            assert len(sent) == len("<document>\n") + MAIL_LABEL_TEXT_CHAR_BUDGET + len("\n</document>")
            # The order is past the cut: the label never sees it (option A).
            assert "TEST-PO-113:" not in sent and "TEST-41" not in sent


@pytest.mark.parametrize("name", sorted(n for n in LABELLED if EXPECTED[n]["extraction"] is not None))
def test_each_recorded_text_document_is_extracted_from_its_recorded_answer(monkeypatch, name):
    expected = EXPECTED[name]["extraction"]
    with WorkerTestTenant("Acme Test Mail Fixtures") as tenant:
        raw_email_id = _receive(monkeypatch, tenant, name)
        monkeypatch.setattr(
            label_task.anthropic, "Anthropic", lambda *a, **k: _RecordedLabel(_recording(name, "label"))
        )
        label_task.label_mail_body(str(tenant.tenant_id), str(raw_email_id))
        [document] = _rows(
            "SELECT id FROM documents WHERE raw_email_id = :e AND email_part = 'body'", e=str(raw_email_id)
        )
        document_id = UUID(str(document["id"]))

        fake = _RecordedExtraction(_recording(name, "extraction"))
        monkeypatch.setattr(document_task.anthropic, "Anthropic", fake)
        document_task.parse_and_extract(str(tenant.tenant_id), str(document_id))

        [row] = _rows(
            "SELECT d.status, d.injection_suspected, h.po_number FROM documents d "
            "JOIN document_headers h ON h.document_id = d.id WHERE d.id = :d",
            d=str(document_id),
        )
        assert row["status"] == "needs_review" and row["po_number"] == expected["po_number"]
        lines = {
            (line["sku"], line["quantity"])
            for line in _rows(
                "SELECT sku, quantity FROM document_lines WHERE document_id = :d", d=str(document_id)
            )
        }
        for sku, quantity in expected["lines"]:
            assert (sku, Decimal(quantity)) in lines
        for sku in expected.get("absent_skus", []):
            assert all(line_sku != sku for line_sku, _ in lines)
        if expected.get("exact_lines"):
            # Nothing beyond the expected lines: no line with a SKU or a
            # quantity the email doesn't state (16: none at all).
            wanted = {(sku, Decimal(quantity)) for sku, quantity in expected["lines"]}
            assert {
                (sku, quantity) for sku, quantity in lines if sku is not None or quantity is not None
            } == wanted
        codes = [
            r["code"]
            for r in _rows("SELECT code FROM document_warnings WHERE document_id = :d", d=str(document_id))
        ]
        assert ("VAL-019" in codes) == EXPECTED[name]["val_019"]
        if name == "12_html_script_and_hidden_block":
            # Pinned from the recording (founder, 2026-11-19); TEST-900 is
            # not an expected fact. Checks: a TEST-900 x 9 line, the
            # document's injection_suspected with VAL-009, and that line's
            # warnings, VAL-006 and VAL-010 (as evaluate_document gave offline).
            assert ("TEST-900", Decimal("9")) in lines
            assert row["injection_suspected"] is True and "VAL-009" in codes
            test_900_line_codes = {
                r["code"]
                for r in _rows(
                    "SELECT w.code FROM document_warnings w JOIN document_lines l "
                    "ON l.id = w.document_line_id WHERE l.document_id = :d AND l.sku = 'TEST-900'",
                    d=str(document_id),
                )
            }
            assert test_900_line_codes == {"VAL-006", "VAL-010"}
        if expected.get("injection"):
            # Read, never obeyed: no quantity the instruction asked for.
            assert all(quantity != Decimal("999") for _, quantity in lines)
            if row["injection_suspected"]:
                assert "VAL-009" in codes


def test_no_document_from_any_fixture_email_carries_val_020():
    """b1.md section 12: VAL-020 is shown in the email panel only and is never
    a warning row on a document. The fixture runs leave none."""
    assert _rows("SELECT id FROM document_warnings WHERE code = 'VAL-020'") == []
