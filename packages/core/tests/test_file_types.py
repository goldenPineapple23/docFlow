"""
CLAUDE.md Section 7.11 required tests at the allowlist boundary: a valid
fixture per Tier 1 and Tier 2 format is detected with the right type and
tier, and a zip bomb, an XXE payload, a misleading-extension file, an
oversized file, a password-protected PDF, a `.zip` containing a valid PO,
and every Tier 3 format are each rejected cleanly with a stable, per-case
catalog code.

Conversion itself (and therefore the page-count, pixel-dimension and
converter-failure tests) lives in the isolated worker, so those tests are in
apps/worker/tests -- this file covers everything decided before a parser or
converter is ever reached.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from docflow_core.errors import CATALOG
from docflow_core.file_types import (
    ALLOWED_EXTENSIONS,
    FORMATS,
    MAX_FILE_SIZE_BYTES,
    FileRejection,
    FileTypeName,
    ZipInspectionError,
    detect_file_type,
    validate_upload,
)


def _make_zip(entries: dict[str, bytes], compress: bool = True) -> bytes:
    buf = BytesIO()
    compression = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with zipfile.ZipFile(buf, "w", compression=compression) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _minimal_docx_bytes() -> bytes:
    return _make_zip(
        {
            "[Content_Types].xml": b"<?xml version='1.0'?><Types/>",
            "word/document.xml": b"<?xml version='1.0'?><w:document/>",
        }
    )


def _minimal_xlsx_bytes() -> bytes:
    return _make_zip(
        {
            "[Content_Types].xml": b"<?xml version='1.0'?><Types/>",
            "xl/workbook.xml": b"<?xml version='1.0'?><workbook/>",
        }
    )


# ── Valid Tier 1 fixtures ──────────────────────────────────────────────────


def test_detects_pdf():
    content = b"%PDF-1.4\n%rest of a pdf..."
    result = validate_upload(content, "invoice.pdf")
    assert result.ok
    assert result.file_type.name == FileTypeName.PDF


def test_detects_png():
    content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    result = validate_upload(content, "scan.png")
    assert result.ok
    assert result.file_type.name == FileTypeName.PNG


def test_detects_jpeg():
    content = b"\xff\xd8\xff\xe0" + b"\x00" * 32
    result = validate_upload(content, "scan.jpg")
    assert result.ok
    assert result.file_type.name == FileTypeName.JPEG


def test_detects_gif():
    content = b"GIF89a" + b"\x00" * 32
    result = validate_upload(content, "scan.gif")
    assert result.ok
    assert result.file_type.name == FileTypeName.GIF


def test_detects_webp():
    content = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32
    result = validate_upload(content, "scan.webp")
    assert result.ok
    assert result.file_type.name == FileTypeName.WEBP


def test_detects_plain_text():
    content = "PO Number: 12345\nBuyer: Acme Test Distributor\n".encode("utf-8")
    result = validate_upload(content, "po.txt")
    assert result.ok
    assert result.file_type.name == FileTypeName.TXT


def test_detects_csv_as_text_like():
    content = b"sku,qty,price\nCF-1001,12,47.50\n"
    result = validate_upload(content, "po.csv")
    assert result.ok


def test_detects_html_as_text_like():
    content = b"<html><body>Purchase Order</body></html>"
    result = validate_upload(content, "po.html")
    assert result.ok


def test_detects_docx():
    result = validate_upload(_minimal_docx_bytes(), "po.docx")
    assert result.ok
    assert result.file_type.name == FileTypeName.DOCX


def test_detects_xlsx():
    result = validate_upload(_minimal_xlsx_bytes(), "po.xlsx")
    assert result.ok
    assert result.file_type.name == FileTypeName.XLSX


# ── Rejections ──────────────────────────────────────────────────────────────


def test_rejects_oversized_file():
    content = b"%PDF-1.4\n" + b"0" * (MAX_FILE_SIZE_BYTES + 1)
    result = validate_upload(content, "big.pdf")
    assert not result.ok
    assert result.error_code == "DOC-002"


def test_rejects_misleading_extension_exe_renamed_pdf():
    # A Windows PE executable's real magic bytes ("MZ...") renamed to .pdf.
    content = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 32
    result = validate_upload(content, "invoice.pdf")
    assert not result.ok
    # Matches no allowlisted signature at all -- its own Tier 3 case.
    assert result.error_code == "DOC-014"


def test_rejects_extension_magic_byte_mismatch():
    # Real PNG bytes, claimed as a .pdf -- a detected, listed type that
    # doesn't match the claimed extension.
    content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    result = validate_upload(content, "invoice.pdf")
    assert not result.ok
    assert result.error_code == "DOC-006"


def test_rejects_unsupported_format_plain_zip():
    content = _make_zip({"readme.txt": b"just a plain zip, not docx/xlsx"})
    result = validate_upload(content, "archive.zip")
    assert not result.ok
    assert result.error_code == "DOC-010"


def test_zip_bomb_entry_count_rejected():
    entries = {f"file_{i}.txt": b"x" for i in range(2500)}
    content = _make_zip(entries)
    result = validate_upload(content, "bomb.docx")
    assert not result.ok
    assert result.error_code == "DOC-003"


def test_zip_bomb_compression_ratio_rejected():
    huge_compressible = b"\x00" * (5 * 1024 * 1024)
    content = _make_zip({"word/document.xml": huge_compressible})
    result = validate_upload(content, "bomb.docx")
    assert not result.ok
    assert result.error_code == "DOC-003"


def test_zip_path_traversal_rejected():
    content = _make_zip({"../../etc/passwd": b"nope"})
    result = validate_upload(content, "evil.docx")
    assert not result.ok
    assert result.error_code == "DOC-003"


def test_malformed_zip_rejected_cleanly():
    content = b"PK\x03\x04" + b"not a real zip directory"
    with pytest.raises(ZipInspectionError):
        detect_file_type(content)
    result = validate_upload(content, "broken.docx")
    assert not result.ok
    assert result.error_code == "DOC-005"


def test_empty_content_is_not_a_crash():
    result = validate_upload(b"", "empty.pdf")
    assert not result.ok


# ── Tier 2: recognized, accepted, and routed to a converter ────────────────


def _ole_with_name(stream_name: str) -> bytes:
    """
    An OLE compound file's directory sectors carry stream names as UTF-16LE,
    which is how .doc/.xls/.msg are told apart from one another (they share
    one magic-byte signature). A full container is built in
    apps/worker/tests/fixture_builders.py; this is the byte-level minimum
    the classifier reads.
    """
    return (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        + b"\x00" * 64
        + stream_name.encode("utf-16-le")
        + b"\x00" * 64
    )


def test_detects_legacy_doc_as_tier2():
    result = validate_upload(_ole_with_name("WordDocument"), "po.doc")
    assert result.ok
    assert result.file_type.name == FileTypeName.DOC
    assert result.file_type.tier == "tier2"
    assert result.file_type.handler == "convert_doc"


def test_detects_legacy_xls_as_tier2():
    result = validate_upload(_ole_with_name("Workbook"), "po.xls")
    assert result.ok
    assert result.file_type.name == FileTypeName.XLS
    assert result.file_type.tier == "tier2"


def test_detects_outlook_msg_as_tier2():
    result = validate_upload(_ole_with_name("__substg1.0_0037001F"), "forwarded.msg")
    assert result.ok
    assert result.file_type.name == FileTypeName.MSG


def test_msg_carrying_an_xls_attachment_is_still_a_msg():
    # The attachment's own directory bytes are inside the .msg, so a naive
    # scan order would call this an .xls.
    content = _ole_with_name("__substg1.0_0037001F") + "Workbook".encode("utf-16-le")
    result = validate_upload(content, "forwarded.msg")
    assert result.ok
    assert result.file_type.name == FileTypeName.MSG


def test_detects_tiff_both_byte_orders():
    for magic, name in ((b"II\x2a\x00", "little-endian"), (b"MM\x00\x2a", "big-endian")):
        result = validate_upload(magic + b"\x00" * 64, "fax.tif")
        assert result.ok, name
        assert result.file_type.name == FileTypeName.TIFF
        assert result.file_type.tier == "tier2"


def test_detects_heic():
    content = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64
    result = validate_upload(content, "photo.heic")
    assert result.ok
    assert result.file_type.name == FileTypeName.HEIC


def test_avif_is_not_accepted_as_heic():
    content = b"\x00\x00\x00\x18ftypavif" + b"\x00" * 64
    result = validate_upload(content, "photo.heic")
    assert not result.ok
    assert result.error_code == "DOC-014"


def _make_odf(mimetype: str) -> bytes:
    return _make_zip(
        {
            "mimetype": mimetype.encode("utf-8"),
            "content.xml": b"<?xml version='1.0'?><office:document-content/>",
        }
    )


def test_detects_odt_and_ods():
    odt = validate_upload(_make_odf("application/vnd.oasis.opendocument.text"), "po.odt")
    assert odt.ok
    assert odt.file_type.name == FileTypeName.ODT
    ods = validate_upload(_make_odf("application/vnd.oasis.opendocument.spreadsheet"), "po.ods")
    assert ods.ok
    assert ods.file_type.name == FileTypeName.ODS


def test_detects_rtf():
    content = rb"{\rtf1\ansi Purchase Order BCH-2291\par}"
    result = validate_upload(content, "po.rtf")
    assert result.ok
    assert result.file_type.name == FileTypeName.RTF


def test_rtf_saved_with_a_doc_extension_is_still_accepted():
    # Word writes RTF with a .doc name often enough that refusing it would
    # reject a readable purchase order over a benign extension lie.
    content = rb"{\rtf1\ansi Purchase Order BCH-2291\par}"
    result = validate_upload(content, "po.doc")
    assert result.ok
    assert result.file_type.name == FileTypeName.RTF


def test_detects_eml():
    content = (
        b"From: orders@bellascoffee.example\r\n"
        b"Subject: PO BCH-2291\r\n"
        b"\r\nPlease see attached.\r\n"
    )
    result = validate_upload(content, "forwarded.eml")
    assert result.ok
    assert result.file_type.name == FileTypeName.EML


def test_plain_text_po_is_not_mistaken_for_an_eml():
    content = b"PO Number: BCH-2291\nBuyer: Acme Test Distributor\n"
    result = validate_upload(content, "po.txt")
    assert result.ok
    assert result.file_type.name == FileTypeName.TXT


# ── Tier 3: each case has its own catalog code and names the format ────────


def test_rejects_rar_archive():
    result = validate_upload(b"Rar!\x1a\x07\x00" + b"\x00" * 32, "po.rar")
    assert result.error_code == "DOC-010"
    assert ".rar" in result.detail


def test_rejects_7z_archive():
    result = validate_upload(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 32, "po.7z")
    assert result.error_code == "DOC-010"


def test_zip_containing_a_valid_po_is_rejected_not_extracted():
    """
    CLAUDE.md Section 10: never auto-extract an archive. The PO inside is
    perfectly readable -- that is exactly the temptation the rule forbids.
    """
    inner = "PO Number: BCH-2291\nBuyer: Acme Test Distributor\n".encode("utf-8")
    result = validate_upload(_make_zip({"purchase_order.txt": inner}), "po.zip")
    assert not result.ok
    assert result.error_code == "DOC-010"


def test_rejects_iwork_document():
    content = _make_zip(
        {"Index/Document.iwa": b"binary iwa payload", "preview.jpg": b"\xff\xd8\xff"}
    )
    result = validate_upload(content, "po.pages")
    assert result.error_code == "DOC-011"


def test_rejects_cad_drawing():
    result = validate_upload(b"AC1032" + b"\x00" * 64, "part.dwg")
    assert result.error_code == "DOC-012"


def test_rejects_edi_payload():
    content = b"ISA*00*          *00*          *ZZ*ACMETEST      *ZZ*BUYERTEST\n"
    result = validate_upload(content, "order.edi")
    assert result.error_code == "DOC-012"


def test_rejects_encrypted_zip_entry():
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", b"<w:document/>")
    raw = bytearray(buf.getvalue())
    # Set the "encrypted" general-purpose flag bit on both the local file
    # header and the central-directory record (the latter is what a reader
    # actually consults).
    raw[6] |= 0x01
    central = raw.find(b"PK\x01\x02")
    raw[central + 8] |= 0x01
    result = validate_upload(bytes(raw), "protected.docx")
    assert result.error_code == "DOC-013"


def test_rejects_encrypted_ooxml_in_ole_container():
    result = validate_upload(_ole_with_name("EncryptedPackage"), "protected.xlsx")
    assert result.error_code == "DOC-013"


def test_rejects_password_protected_pdf():
    content = (
        b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\n"
        b"trailer\n<< /Size 2 /Encrypt 5 0 R /Root 1 0 R >>\nstartxref\n9\n%%EOF\n"
    )
    result = validate_upload(content, "protected.pdf")
    assert result.error_code == "DOC-001"


def test_rejects_unknown_binary_with_no_signature():
    result = validate_upload(b"\x01\x02\x03\x04" + b"\x99" * 64, "mystery.bin")
    assert result.error_code == "DOC-014"


def test_rejects_powerpoint_ole_with_readable_reason():
    result = validate_upload(_ole_with_name("PowerPoint Document"), "deck.ppt")
    assert result.error_code == "DOC-004"


# ── XXE and nested-archive defense ─────────────────────────────────────────


def test_xxe_payload_in_docx_is_rejected():
    xxe = (
        b"<?xml version='1.0'?>\n"
        b"<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>\n"
        b"<w:document>&xxe;</w:document>"
    )
    content = _make_zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": xxe})
    result = validate_upload(content, "po.docx")
    assert not result.ok
    assert result.error_code == "DOC-015"


def test_billion_laughs_entity_in_xlsx_is_rejected():
    payload = (
        b"<?xml version='1.0'?>\n"
        b"<!DOCTYPE lolz [<!ENTITY lol 'lol'><!ENTITY lol2 '&lol;&lol;&lol;'>]>\n"
        b"<workbook>&lol2;</workbook>"
    )
    content = _make_zip({"xl/workbook.xml": payload})
    result = validate_upload(content, "po.xlsx")
    assert result.error_code == "DOC-015"


def test_html_doctype_is_not_mistaken_for_an_xxe_payload():
    content = b"<!DOCTYPE html>\n<html><body>Purchase Order BCH-2291</body></html>"
    result = validate_upload(content, "po.html")
    assert result.ok


def test_nested_archive_inside_docx_is_rejected():
    content = _make_zip(
        {"word/document.xml": b"<w:document/>", "word/embeddings/payload.zip": b"PK\x03\x04"}
    )
    result = validate_upload(content, "po.docx")
    assert result.error_code == "DOC-003"


def test_detect_file_type_raises_a_coded_rejection():
    with pytest.raises(FileRejection) as excinfo:
        detect_file_type(b"Rar!\x1a\x07\x00")
    assert excinfo.value.error_code == "DOC-010"


# ── The allowlist is the spec (CLAUDE.md Section 7.11's table) ─────────────


def test_allowlist_matches_section_7_11_table():
    tier1 = {
        ".pdf", ".docx", ".xlsx", ".xlsm", ".rtf", ".txt", ".csv", ".md",
        ".html", ".htm", ".eml", ".png", ".jpg", ".jpeg", ".webp", ".gif",
    }
    tier2 = {".doc", ".xls", ".tif", ".tiff", ".heic", ".heif", ".msg", ".odt", ".ods"}
    assert set(ALLOWED_EXTENSIONS) == tier1 | tier2
    for extension in tier2:
        assert ALLOWED_EXTENSIONS[extension] is not None


def test_every_format_declares_a_handler_and_media_type():
    for spec in FORMATS:
        assert spec.handler, spec.name
        assert spec.media_type, spec.name
        assert spec.extensions, spec.name
        assert spec.tier in ("tier1", "tier2")


def test_every_rejection_code_exists_in_the_error_catalog():
    """
    CLAUDE.md Section 7.16.5: no user-facing failure exists outside the
    catalog. Every code this module can return must be in it.
    """
    from docflow_core import file_types as module

    source = __import__("inspect").getsource(module)
    import re as _re

    for code in sorted(set(_re.findall(r"\bDOC-\d{3}\b", source))):
        assert code in CATALOG, f"{code} is returned by file_types but missing from the catalog"
