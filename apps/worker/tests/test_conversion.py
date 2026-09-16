"""
CLAUDE.md Section 7.11's required tests, worker side -- everything that can
only be decided once a parser or converter actually opens the file:

  "a zip bomb, an XXE payload, an oversized image, a 500-page PDF, a file
   with a misleading extension, a password-protected PDF, a `.zip`
   containing a valid PO, and a malformed file of each Tier 2 format that
   kills the converter. Each must be rejected or failed cleanly with the
   worker still healthy afterward. Plus a positive fixture for every Tier 1
   and Tier 2 format -- a real PO in each -- asserting it reaches
   extraction."

(The zip bomb, XXE payload, misleading extension and `.zip`-with-a-PO are
decided before any parser runs, so they live in
packages/core/tests/test_file_types.py; the rest are here.)

Every fixture is generated at test time by tests/fixture_builders.py with
obviously fake content (CLAUDE.md Section 0 rule 4). No test in this file
makes a network call: "reaches extraction" is asserted against a mocked
Anthropic client, exactly as apps/api/tests/test_golden_fixture.py does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from docflow_core import file_types
from docflow_core.extraction import extract_document
from docflow_core.file_types import FileTypeName, validate_upload

from app import conversion
from app.conversion import ConversionError, prepare_artifacts
from app.tasks.parse_and_extract import (
    _extract_rtf_text,
    build_content_blocks_for_artifacts,
)
from tests import fixture_builders as fb

LIBREOFFICE = conversion.find_libreoffice()
requires_libreoffice = pytest.mark.skipif(
    LIBREOFFICE is None,
    reason=(
        "LibreOffice is not installed on this machine, so legacy .doc conversion cannot be "
        "exercised end-to-end. The code path and its clean DOC-017 degradation are still "
        "tested below -- see SETUP.md and DECISIONS.md D-041."
    ),
)


# ── A mocked extraction call (no network), mirroring the golden fixture ────


@dataclass
class _FakeTextBlock:
    type: str
    text: str


@dataclass
class _FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _FakeMessage:
    content: list[_FakeTextBlock]
    usage: _FakeUsage


class _FakeMessagesResource:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload
        self.last_content: list[dict] | None = None

    def create(self, **kwargs) -> _FakeMessage:
        self.last_content = kwargs["messages"][0]["content"]
        return _FakeMessage(
            content=[_FakeTextBlock(type="text", text=json.dumps(self._payload))],
            usage=_FakeUsage(input_tokens=100, output_tokens=50),
        )


class _FakeAnthropicClient:
    def __init__(self):
        self.messages = _FakeMessagesResource(_MINIMAL_RESPONSE)


_MINIMAL_RESPONSE: dict[str, Any] = {
    "header": {
        "po_number": "BCH-2291",
        "order_date": "2026-03-14",
        "requested_delivery_date": None,
        "buyer_name": "Bella's Coffee House",
        "buyer_contact_email": "orders@bellascoffee.com",
        "ship_to_address": None,
        "payment_terms": "Net 30",
        "order_total": "1356.00",
        "currency": "USD",
        "notes": None,
    },
    "header_confidence": {
        name: 0.95
        for name in (
            "po_number",
            "order_date",
            "requested_delivery_date",
            "buyer_name",
            "buyer_contact_email",
            "ship_to_address",
            "payment_terms",
            "order_total",
            "currency",
            "notes",
        )
    },
    "line_items": [
        {
            "line_number": 1,
            "sku": "CF-1001",
            "description": "Colombian Whole Bean 5lb",
            "quantity": "12",
            "unit": "CS",
            "unit_price": "47.50",
            "line_total": "570.00",
            "confidence": 0.95,
        }
    ],
    "document_notes": "",
    "injection_suspected": False,
    "currency_inferred": False,
}


def _run_to_extraction(content: bytes, filename: str):
    """
    The real path a document takes in the worker: validate -> convert /
    unwrap -> build content blocks -> extract. Returns
    (ExtractionResult, content_blocks).
    """
    validation = validate_upload(content, filename)
    assert validation.ok, f"{filename} was rejected: {validation.error_code} {validation.detail}"
    artifacts = prepare_artifacts(validation.file_type, content)
    blocks = build_content_blocks_for_artifacts(artifacts)
    client = _FakeAnthropicClient()
    result = extract_document(client, blocks)
    return result, blocks


def _text_of(blocks: list[dict]) -> str:
    return "\n".join(block["text"] for block in blocks if block["type"] == "text")


# ── Positive fixture per Tier 1 format ─────────────────────────────────────


@pytest.mark.parametrize(
    "filename,builder",
    [
        ("po.txt", lambda: fb.PO_TEXT.encode("utf-8")),
        ("po.csv", lambda: fb.PO_TEXT.encode("utf-8")),
        ("po.md", lambda: fb.PO_TEXT.encode("utf-8")),
        ("po.html", lambda: f"<html><body><pre>{fb.PO_TEXT}</pre></body></html>".encode("utf-8")),
        ("po.pdf", lambda: fb.build_pdf([fb.PO_TEXT.replace("\n", " ")])),
        ("po.docx", fb.build_docx),
        ("po.xlsx", fb.build_xlsx),
        ("po.rtf", fb.build_rtf),
        ("po.eml", fb.build_eml),
    ],
)
def test_tier1_fixture_reaches_extraction(filename, builder):
    result, blocks = _run_to_extraction(builder(), filename)
    assert result.ok
    assert "BCH-2291" in _text_of(blocks)
    assert result.header["order_total"] == Decimal("1356.00")


def test_tier1_image_fixture_reaches_extraction_visually():
    import io

    buffer = io.BytesIO()
    fb._po_image().save(buffer, format="PNG")
    result, blocks = _run_to_extraction(buffer.getvalue(), "po.png")
    assert result.ok
    image_blocks = [block for block in blocks if block["type"] == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["media_type"] == "image/png"


def test_scanned_pdf_without_text_uses_the_visual_path():
    # A PDF with no text objects at all -- the scanned-fax case.
    blank = fb.build_pdf([""])
    validation = validate_upload(blank, "scan.pdf")
    blocks = build_content_blocks_for_artifacts(prepare_artifacts(validation.file_type, blank))
    assert any(block["type"] == "document" for block in blocks)


# ── Positive fixture per Tier 2 format ─────────────────────────────────────


def test_tiff_fixture_reaches_extraction_as_one_image_per_page():
    content = fb.build_tiff(pages=3)
    validation = validate_upload(content, "fax.tif")
    assert validation.ok
    assert validation.file_type.name == FileTypeName.TIFF
    artifacts = prepare_artifacts(validation.file_type, content)
    assert len(artifacts) == 3
    assert all(artifact.file_type.name == FileTypeName.JPEG for artifact in artifacts)
    blocks = build_content_blocks_for_artifacts(artifacts)
    assert [block["type"] for block in blocks] == ["text", "image", "image", "image", "text"]
    # One <document> envelope for the whole document, not one per page.
    assert blocks[0]["text"] == "<document>"
    assert blocks[-1]["text"] == "</document>"
    result = extract_document(_FakeAnthropicClient(), blocks)
    assert result.ok


def test_heic_fixture_reaches_extraction():
    content = fb.build_heic()
    validation = validate_upload(content, "photo.heic")
    assert validation.ok
    assert validation.file_type.name == FileTypeName.HEIC
    artifacts = prepare_artifacts(validation.file_type, content)
    assert len(artifacts) == 1
    assert artifacts[0].file_type.name == FileTypeName.JPEG
    result = extract_document(_FakeAnthropicClient(), build_content_blocks_for_artifacts(artifacts))
    assert result.ok


def test_xls_fixture_reaches_extraction():
    result, blocks = _run_to_extraction(fb.build_xls(), "po.xls")
    assert result.ok
    text = _text_of(blocks)
    assert "BCH-2291" in text
    assert "Colombian Whole Bean 5lb" in text


def test_odt_and_ods_fixtures_reach_extraction():
    for content, filename in ((fb.build_odt(), "po.odt"), (fb.build_ods(), "po.ods")):
        result, blocks = _run_to_extraction(content, filename)
        assert result.ok
        text = _text_of(blocks)
        assert "BCH-2291" in text
        assert "CF-1001" in text


def test_msg_fixture_reaches_extraction_with_body_and_attachment():
    content = fb.build_msg(attachments=[("purchase_order.txt", fb.PO_TEXT.encode("utf-8"))])
    validation = validate_upload(content, "forwarded.msg")
    assert validation.ok
    assert validation.file_type.name == FileTypeName.MSG
    artifacts = prepare_artifacts(validation.file_type, content)
    assert [artifact.label for artifact in artifacts] == [
        "message-body.txt",
        "purchase_order.txt",
    ]
    blocks = build_content_blocks_for_artifacts(artifacts)
    text = _text_of(blocks)
    assert "Subject: PO BCH-2291" in text
    assert "CF-1001" in text
    result = extract_document(_FakeAnthropicClient(), blocks)
    assert result.ok


def test_eml_attachment_is_revalidated_and_extracted():
    content = fb.build_eml(
        attachments=[("purchase_order.txt", "octet-stream", fb.PO_TEXT.encode("utf-8"))]
    )
    validation = validate_upload(content, "forwarded.eml")
    assert validation.file_type.name == FileTypeName.EML
    artifacts = prepare_artifacts(validation.file_type, content)
    assert len(artifacts) == 2
    assert "CF-1001" in _text_of(build_content_blocks_for_artifacts(artifacts))


def test_eml_attachment_that_is_not_allowlisted_is_dropped_not_fatal():
    content = fb.build_eml(attachments=[("logo.exe", "octet-stream", b"MZ\x90\x00" + b"\x00" * 64)])
    validation = validate_upload(content, "forwarded.eml")
    artifacts = prepare_artifacts(validation.file_type, content)
    # The body still carries the purchase order; the bad attachment is gone.
    assert len(artifacts) == 1
    assert artifacts[0].label == "message-body.txt"


@requires_libreoffice
def test_doc_fixture_reaches_extraction():  # pragma: no cover - environment dependent
    docx_bytes = fb.build_docx()
    converted = conversion.convert_with_libreoffice(docx_bytes, ".docx", "doc")
    result, blocks = _run_to_extraction(converted, "po.doc")
    assert result.ok
    assert "BCH-2291" in _text_of(blocks)


# ── One level of nesting, and no more ──────────────────────────────────────


def test_attachment_nested_two_levels_deep_is_rejected():
    inner = fb.build_msg(attachments=[("po.txt", fb.PO_TEXT.encode("utf-8"))])
    validation = validate_upload(inner, "inner.msg")
    with pytest.raises(ConversionError) as excinfo:
        prepare_artifacts(validation.file_type, inner, depth=file_types.MAX_EMBEDDING_DEPTH)
    assert excinfo.value.error_code == "DOC-019"


def test_msg_inside_msg_does_not_recurse_into_the_inner_message():
    inner = fb.build_msg(
        subject="Inner message", body="SECRET-INNER-BODY", attachments=[]
    )
    outer = fb.build_msg(attachments=[("forwarded.msg", inner)])
    validation = validate_upload(outer, "outer.msg")
    artifacts = prepare_artifacts(validation.file_type, outer)
    text = _text_of(build_content_blocks_for_artifacts(artifacts))
    assert "SECRET-INNER-BODY" not in text
    assert "BCH-2291" in text


def test_eml_inside_eml_does_not_recurse():
    inner = fb.build_eml()
    outer = fb.build_eml(attachments=[("forwarded.eml", "octet-stream", inner)])
    validation = validate_upload(outer, "outer.eml")
    artifacts = prepare_artifacts(validation.file_type, outer)
    assert [artifact.label for artifact in artifacts] == ["message-body.txt"]


# ── Page-count, pixel and password limits ──────────────────────────────────


def test_500_page_pdf_is_rejected_cleanly():
    content = fb.build_pdf([f"Page {index}" for index in range(500)])
    validation = validate_upload(content, "huge.pdf")
    assert validation.ok  # nothing about the bytes is wrong; the page count is
    artifacts = prepare_artifacts(validation.file_type, content)
    # The cap is enforced where the pages are actually counted -- when the
    # parser opens the document, inside the same guarded step of the task.
    with pytest.raises(ConversionError) as excinfo:
        build_content_blocks_for_artifacts(artifacts)
    assert excinfo.value.error_code == "DOC-016"


def test_multipage_tiff_over_the_page_cap_is_rejected():
    content = fb.build_tiff(pages=file_types.MAX_DOCUMENT_PAGES + 1, size=(40, 30))
    validation = validate_upload(content, "fax.tif")
    with pytest.raises(ConversionError) as excinfo:
        prepare_artifacts(validation.file_type, content)
    assert excinfo.value.error_code == "DOC-016"


def test_oversized_image_is_rejected_before_decode():
    content = fb.build_tiff(pages=1, size=(file_types.MAX_IMAGE_DIMENSION + 1, 8))
    validation = validate_upload(content, "huge.tif")
    with pytest.raises(ConversionError) as excinfo:
        prepare_artifacts(validation.file_type, content)
    assert excinfo.value.error_code == "DOC-018"


def test_pillow_pixel_guard_is_set_explicitly():
    from PIL import Image

    conversion._open_image(fb.build_tiff(pages=1, size=(20, 20)))
    assert Image.MAX_IMAGE_PIXELS == file_types.MAX_IMAGE_PIXELS


def test_password_protected_pdf_fails_with_doc_001(monkeypatch):
    """
    The allowlist catches the common case from the trailer; this is the
    other one -- an encrypted PDF whose /Encrypt reference the tail scan
    cannot see, caught when the parser opens it. Never brute-forced, never
    passed to the model (CLAUDE.md Section 7.11).
    """
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    import app.tasks.parse_and_extract as mod

    def _raise(*args, **kwargs):
        raise PDFPasswordIncorrect("password required")

    monkeypatch.setattr("pdfplumber.open", _raise)
    with pytest.raises(ConversionError) as excinfo:
        mod._extract_pdf_text(fb.build_pdf(["anything"]))
    assert excinfo.value.error_code == "DOC-001"


# ── A malformed file of each Tier 2 format fails cleanly ───────────────────


@pytest.mark.parametrize(
    "file_type_name,content",
    [
        (FileTypeName.TIFF, b"II\x2a\x00" + b"\xff" * 512),
        (FileTypeName.HEIC, b"\x00\x00\x00\x18ftypheic" + b"\xff" * 512),
        (FileTypeName.XLS, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\xff" * 512),
        (FileTypeName.MSG, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\xff" * 512),
        (FileTypeName.ODT, b"PK\x03\x04 truncated opendocument"),
        (FileTypeName.ODS, b"PK\x03\x04 truncated opendocument"),
    ],
)
def test_malformed_tier2_file_fails_cleanly(file_type_name, content):
    file_type = file_types.ALL_TYPES[file_type_name]
    with pytest.raises(ConversionError) as excinfo:
        prepare_artifacts(file_type, content)
    assert excinfo.value.error_code in ("DOC-016", "DOC-017", "DOC-018")


def test_worker_is_healthy_after_a_converter_failure():
    """A dead converter takes one document to failed, never the process."""
    with pytest.raises(ConversionError):
        prepare_artifacts(file_types.ALL_TYPES[FileTypeName.TIFF], b"II\x2a\x00" + b"\xff" * 512)
    content = fb.build_tiff(pages=1)
    artifacts = prepare_artifacts(file_types.ALL_TYPES[FileTypeName.TIFF], content)
    assert len(artifacts) == 1


def test_malformed_doc_fails_cleanly_whether_or_not_libreoffice_exists():
    with pytest.raises(ConversionError) as excinfo:
        prepare_artifacts(
            file_types.ALL_TYPES[FileTypeName.DOC],
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\xff" * 512,
        )
    assert excinfo.value.error_code == "DOC-017"


def test_doc_without_libreoffice_degrades_to_a_catalog_code(monkeypatch):
    monkeypatch.setattr(conversion, "find_libreoffice", lambda: None)
    with pytest.raises(ConversionError) as excinfo:
        conversion.convert_doc(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    assert excinfo.value.error_code == "DOC-017"
    assert "LibreOffice" in excinfo.value.detail


# ── Converted output is re-validated, never trusted ────────────────────────


def test_converter_output_is_revalidated(monkeypatch):
    monkeypatch.setitem(
        conversion._CONVERTERS, "convert_tiff", lambda content: [(b"MZ\x90\x00 not an image", "x.png")]
    )
    with pytest.raises(ConversionError) as excinfo:
        prepare_artifacts(file_types.ALL_TYPES[FileTypeName.TIFF], fb.build_tiff())
    assert excinfo.value.error_code in ("DOC-006", "DOC-014")


# ── Numbers never become floats on the way to the model ────────────────────


def test_spreadsheet_cells_never_render_as_float_repr():
    assert conversion.format_cell_value(12.0) == "12"
    assert conversion.format_cell_value(47.5) == "47.5"
    assert conversion.format_cell_value(1356.0) == "1356"
    assert conversion.format_cell_value(0.1 + 0.2) == "0.30000000000000004"
    assert conversion.format_cell_value(1e16) == "10000000000000000"
    assert conversion.format_cell_value(None) == ""


def test_xls_text_has_no_scientific_notation():
    text = conversion.convert_xls(fb.build_xls())[0][0].decode("utf-8")
    assert "E+" not in text
    assert "e+" not in text


# ── RTF reader ─────────────────────────────────────────────────────────────


def test_rtf_reader_drops_metadata_groups_and_keeps_text():
    rtf = (
        rb"{\rtf1\ansi{\fonttbl{\f0 Helvetica;}}{\*\generator Riched;}"
        rb"PO Number: BCH-2291\par Buyer: Bella\'92s Coffee House\par}"
    )
    text = _extract_rtf_text(rtf)
    assert "BCH-2291" in text
    assert "Helvetica" not in text
    assert "Riched" not in text
    assert "generator" not in text


# ── The allowlist and the worker agree on what is implemented ──────────────


def test_every_allowlisted_format_has_a_worker_handler():
    """
    CLAUDE.md Section 7.11: "Adding a format is one edit plus a test
    fixture." This is the test that makes that true -- a new FormatSpec with
    no handler implementation fails here.
    """
    from app.tasks.parse_and_extract import _inner_blocks

    tier1_handlers = {"pdf", "image", "text", "docx", "xlsx", "rtf"}
    implemented = tier1_handlers | conversion.tier2_handlers()
    for spec in file_types.FORMATS:
        assert spec.handler in implemented, f"{spec.name.value} has no worker handler"
    assert callable(_inner_blocks)
