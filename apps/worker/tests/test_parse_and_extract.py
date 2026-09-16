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


def test_overall_confidence_is_minimum_not_average():
    confidence = {"po_number": 0.98, "order_date": 0.4, "buyer_name": 0.9}
    assert _overall_confidence(confidence) == Decimal("0.4")


def test_overall_confidence_handles_empty():
    assert _overall_confidence({}) == Decimal("0")
