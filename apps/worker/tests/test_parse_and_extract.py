"""
Unit tests for the Tier 1 content-block builder and confidence rollup used
by the isolated parsing task (CLAUDE.md Section 7.11 / Section 7.1). The
full task (DB writes, real extraction call) is covered by
test_parse_and_extract_integration.py, gated on the documents schema
actually being applied (see conftest.py).
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

import docx
import openpyxl
import pytest
from docflow_core.file_types import FileType, FileTypeName

from app.tasks.parse_and_extract import (
    UnhandledFileTypeError,
    _overall_confidence,
    build_content_blocks,
)


def _ft(name: FileTypeName) -> FileType:
    return FileType(name=name, tier="tier1", media_type="application/octet-stream")


def test_plain_text_becomes_text_content():
    content = "PO Number: 12345".encode("utf-8")
    blocks = build_content_blocks(_ft(FileTypeName.TXT), content)
    assert blocks[0]["type"] == "text"
    assert "PO Number: 12345" in blocks[0]["text"]
    assert "<document>" in blocks[0]["text"]


def test_image_becomes_image_content_block():
    content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    blocks = build_content_blocks(_ft(FileTypeName.PNG), content)
    image_blocks = [b for b in blocks if b["type"] == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["media_type"] == "image/png"


def test_docx_extracts_paragraph_text():
    document = docx.Document()
    document.add_paragraph("PO Number: BCH-9999")
    document.add_paragraph("Buyer: Acme Test Distributor")
    buf = BytesIO()
    document.save(buf)

    blocks = build_content_blocks(_ft(FileTypeName.DOCX), buf.getvalue())
    assert blocks[0]["type"] == "text"
    assert "BCH-9999" in blocks[0]["text"]
    assert "Acme Test Distributor" in blocks[0]["text"]


def test_xlsx_extracts_cell_text():
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["SKU", "Description", "Qty"])
    sheet.append(["CF-1001", "Colombian Whole Bean 5lb", 12])
    buf = BytesIO()
    workbook.save(buf)

    blocks = build_content_blocks(_ft(FileTypeName.XLSX), buf.getvalue())
    assert blocks[0]["type"] == "text"
    assert "CF-1001" in blocks[0]["text"]
    assert "Colombian Whole Bean 5lb" in blocks[0]["text"]


def test_pdf_with_extractable_text_uses_text_path(monkeypatch):
    import app.tasks.parse_and_extract as mod

    monkeypatch.setattr(mod, "_extract_pdf_text", lambda content: "PO Number: BCH-1234\n" * 20)
    blocks = build_content_blocks(_ft(FileTypeName.PDF), b"%PDF-1.4 fake")
    assert blocks[0]["type"] == "text"
    assert "BCH-1234" in blocks[0]["text"]


def test_pdf_without_extractable_text_uses_visual_path(monkeypatch):
    import app.tasks.parse_and_extract as mod

    monkeypatch.setattr(mod, "_extract_pdf_text", lambda content: "")
    blocks = build_content_blocks(_ft(FileTypeName.PDF), b"%PDF-1.4 fake pdf bytes")
    doc_blocks = [b for b in blocks if b["type"] == "document"]
    assert len(doc_blocks) == 1
    assert doc_blocks[0]["source"]["media_type"] == "application/pdf"


def test_unhandled_file_type_raises():
    with pytest.raises(UnhandledFileTypeError):
        build_content_blocks(_ft(FileTypeName.DOC), b"whatever")


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeSession:
    """Just enough SQLAlchemy surface to drive the task without a database."""

    def __init__(self, row):
        self._row = row
        self.statements: list[tuple[str, dict]] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, params or {}))
        if sql.strip().upper().startswith("SELECT"):
            return _FakeResult(self._row)
        return _FakeResult(None)


def test_conversion_failure_marks_the_document_failed_and_keeps_the_worker_alive(monkeypatch):
    """
    CLAUDE.md Section 7.11: "a conversion failure is a clean `failed` with a
    catalog-coded message, never a crash", and "a worker that dies takes one
    document to `failed`, never the app". A truncated TIFF is the malformed
    Tier 2 file; the task must return normally with a DOC-coded failure
    recorded.
    """
    import contextlib
    from uuid import uuid4

    import app.tasks.parse_and_extract as mod

    session = _FakeSession({"storage_path": "tenants/x/uploads/y.tif", "original_filename": "fax.tif"})

    @contextlib.contextmanager
    def fake_tenant_session(tenant_id):
        yield session

    monkeypatch.setattr(mod, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(mod, "read_file", lambda path: b"II\x2a\x00" + b"\xff" * 512)

    mod.parse_and_extract(str(uuid4()), str(uuid4()))

    failures = [
        params
        for sql, params in session.statements
        if "status='failed'" in sql.replace(" ", "") or "status = 'failed'" in sql
    ]
    assert failures, "the document was not marked failed"
    assert failures[-1]["raw_json"]["error_code"].startswith("DOC-")


def test_provenance_records_extracted_for_every_present_field():
    from app.tasks.parse_and_extract import _PROVENANCE_HEADER_FIELDS, _extracted_provenance

    header = {
        "po_number": "BCH-9999",
        "buyer_name": "Bella's Coffee House",
        "order_date": None,
        "currency": "USD",
    }
    provenance = _extracted_provenance(header, _PROVENANCE_HEADER_FIELDS)

    assert provenance == {"po_number": "extracted", "buyer_name": "extracted", "currency": "extracted"}
    # A field the model returned as null has no value, so it has no provenance.
    assert "order_date" not in provenance


def test_provenance_of_a_document_with_nothing_extracted_is_empty_not_wrong():
    from app.tasks.parse_and_extract import _PROVENANCE_LINE_FIELDS, _extracted_provenance

    assert _extracted_provenance({"sku": None, "description": None}, _PROVENANCE_LINE_FIELDS) == {}


def test_overall_confidence_is_minimum_not_average():
    confidence = {"po_number": 0.98, "order_date": 0.4, "buyer_name": 0.9}
    assert _overall_confidence(confidence) == Decimal("0.4")


def test_overall_confidence_handles_empty():
    assert _overall_confidence({}) == Decimal("0")


# ── matching is wired in, and cannot cost the document its extraction ───────


def _drive_successful_task(monkeypatch, *, buyer_id, matcher):
    """
    Runs `parse_and_extract` end to end over a plain-text PO with the model
    call, storage and both post-extraction steps replaced. Returns the fake
    session so a test can inspect what was written.
    """
    import contextlib
    from uuid import uuid4

    from docflow_core.buyers import BuyerIdentification
    from docflow_core.extraction import ExtractionResult

    import app.tasks.parse_and_extract as mod

    session = _FakeSession({"storage_path": "tenants/x/uploads/po.txt", "original_filename": "po.txt"})

    @contextlib.contextmanager
    def fake_tenant_session(tenant_id):
        yield session

    header = {key: None for key in mod._PROVENANCE_HEADER_FIELDS}
    header["po_number"] = "BCH-2291"
    header["buyer_name"] = "Bella's Coffee House"
    result = ExtractionResult(
        ok=True,
        model_id="test-model",
        prompt_hash="testhash",
        schema_version="test",
        raw_response={},
        header=header,
        header_confidence={"po_number": 0.97},
        lines=[
            {
                "line_number": 1,
                "sku": "CF-1001",
                "description": "Colombian Whole Bean 5lb",
                "quantity": Decimal("12"),
                "unit": "CS",
                "unit_price": Decimal("47.50"),
                "line_total": Decimal("570.00"),
                "confidence": Decimal("0.95"),
            }
        ],
        injection_suspected=False,
        currency_inferred=False,
    )

    monkeypatch.setattr(mod, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(mod, "read_file", lambda path: b"PURCHASE ORDER\nPO Number: BCH-2291\n")
    monkeypatch.setattr(mod.anthropic, "Anthropic", lambda api_key=None: object())
    monkeypatch.setattr(mod, "extract_document", lambda client, content: result)
    monkeypatch.setattr(
        mod,
        "identify_and_link_buyer",
        lambda *args, **kwargs: BuyerIdentification(buyer_id=buyer_id, created=True),
    )
    monkeypatch.setattr(mod, "match_document_lines", matcher)

    mod.parse_and_extract(str(uuid4()), str(uuid4()))
    return session


def test_matching_runs_after_buyer_identification_with_the_resolved_buyer(monkeypatch):
    """
    Matching is buyer-scoped (CLAUDE.md Section 7.6), so it has to see the
    buyer the previous step resolved -- not re-derive one, and not run first.
    """
    from uuid import uuid4

    from docflow_core.matching import MatchingSummary

    buyer_id = uuid4()
    calls: list[dict] = []

    def matcher(session, tenant_id, document_id, *, buyer_id=None):
        calls.append({"tenant_id": tenant_id, "document_id": document_id, "buyer_id": buyer_id})
        return MatchingSummary(lines_considered=1, lines_matched=1)

    _drive_successful_task(monkeypatch, buyer_id=buyer_id, matcher=matcher)

    assert len(calls) == 1
    assert calls[0]["buyer_id"] == buyer_id


def test_a_matching_failure_leaves_the_extraction_intact(monkeypatch):
    """
    DECISIONS.md D-058, extended to matching: extraction is one model call the
    tenant has already paid for. A failure in the deterministic step that runs
    afterwards must never roll it back or mark the document failed -- the
    document stays `needs_review` with unmatched lines, which is the same
    state a document with no catalog hits legitimately has.
    """
    from uuid import uuid4

    def matcher(session, tenant_id, document_id, *, buyer_id=None):
        raise RuntimeError("catalog unavailable")

    session = _drive_successful_task(monkeypatch, buyer_id=uuid4(), matcher=matcher)

    statuses = [
        params
        for sql, params in session.statements
        if "status = 'needs_review'" in sql or "status='failed'" in sql.replace(" ", "")
    ]
    assert statuses, "the document was never given a final status"
    assert all("raw_json" not in params or params.get("model_id") for params in statuses)
    assert not [sql for sql, _ in session.statements if "status='failed'" in sql.replace(" ", "")]
