"""
The isolated parsing/extraction task (CLAUDE.md Section 7.11: "parsing never
runs in the web process... a worker that dies takes one document to `failed`,
never the app"). This module IS that isolated worker -- the API layer
(apps/api/app/routers/documents.py) only validates bytes and enqueues this
task, it never parses. Tier 2 conversion happens here too, one step earlier
in the same process (see app/conversion.py), because "Tier 2 is where the
risk is -- treat conversion as parsing."

`tenant_id` is a required argument, not derived from anything inside this
task: the enqueuing caller (the upload endpoint) already resolved it from
the authenticated session per CLAUDE.md Section 7.5, and passes it through
so this task can open a correctly-scoped `tenant_session()` from the start --
there is no query in this module that runs before a tenant context is set.

Every DB write goes through `docflow_core.db.tenant_session()` -- never a
raw connection, never `admin_data_access` (Section 7.15.1's import boundary
doesn't apply here; this module is not `/admin/*` and must never import it).
"""

from __future__ import annotations

import base64
import logging
import re
from decimal import Decimal
from io import BytesIO
from uuid import UUID, uuid4

import anthropic
from docflow_core import file_types
from docflow_core.config import get_settings
from docflow_core.db import tenant_session
from docflow_core.extraction import (
    build_text_content,
    extract_document,
    wrap_document_content,
)
from docflow_core.storage import read_file
from sqlalchemy import text

from app.celery_app import celery_app
from app.conversion import ConversionError, PreparedArtifact, format_cell_value, prepare_artifacts

logger = logging.getLogger(__name__)

# Below this many extracted characters, a PDF is treated as scanned/
# image-only and sent to the model visually instead (mirrors docs/parse_pos.py).
PDF_MIN_TEXT_CHARS = 100

_IMAGE_MEDIA_TYPES = {
    file_types.FileTypeName.PNG: "image/png",
    file_types.FileTypeName.JPEG: "image/jpeg",
    file_types.FileTypeName.GIF: "image/gif",
    file_types.FileTypeName.WEBP: "image/webp",
}

_PLAIN_TEXT_LIKE = {
    file_types.FileTypeName.TXT,
    file_types.FileTypeName.CSV,
    file_types.FileTypeName.MD,
    file_types.FileTypeName.HTML,
}


def _extract_pdf_text(content: bytes) -> str:
    """
    Also the enforcement point for two Section 7.11 limits a PDF can only be
    checked for once it is open: the page-count cap
    (`file_types.MAX_DOCUMENT_PAGES`) and password protection. Both raise a
    catalog-coded ConversionError, so the task records the same clean
    `failed` it records for a Tier 2 conversion failure -- never a crash,
    and the file is never handed to the model.
    """
    import pdfplumber
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            page_count = len(pdf.pages)
            if page_count > file_types.MAX_DOCUMENT_PAGES:
                raise ConversionError(
                    "DOC-016",
                    f"PDF has {page_count} pages (limit {file_types.MAX_DOCUMENT_PAGES}).",
                )
            chunks = [page.extract_text() or "" for page in pdf.pages]
    except PDFPasswordIncorrect as exc:
        # Never brute-forced, never passed to the model (Section 7.11).
        raise ConversionError("DOC-001", "PDF is password-protected.") from exc
    return "\n".join(chunks).strip()


def _extract_docx_text(content: bytes) -> str:
    """
    Paragraphs *and* tables: a purchase order's line items almost always
    live in a table, and python-docx's `paragraphs` skips table content
    entirely. Nothing is executed -- python-docx reads the XML part only, it
    has no macro or embedded-object path (Section 7.11: "No active content,
    ever").
    """
    import docx

    document = docx.Document(BytesIO(content))
    lines = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.replace("\n", " ").strip() for cell in row.cells]
            if any(cells):
                lines.append(", ".join(cells))
    return "\n".join(lines)


def _extract_xlsx_text(content: bytes) -> str:
    import openpyxl

    workbook = openpyxl.load_workbook(BytesIO(content), data_only=True, keep_vba=False)
    lines: list[str] = []
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(values_only=True):
            if any(cell is not None for cell in row):
                lines.append(", ".join(format_cell_value(cell) for cell in row))
    return "\n".join(lines)


_RTF_HEX_RE = re.compile(r"\\'([0-9a-fA-F]{2})")
_RTF_CONTROL_RE = re.compile(r"\\([a-zA-Z]+)(-?\d+)?[ ]?")
# Destination groups whose contents are metadata, not document text.
_RTF_SKIPPED_DESTINATIONS = {
    "fonttbl",
    "colortbl",
    "stylesheet",
    "info",
    "pict",
    "object",
    "themedata",
    "colorschememapping",
    "latentstyles",
    "datastore",
    "generator",
}


def _extract_rtf_text(content: bytes) -> str:
    """
    A deliberately small, dependency-free RTF reader: it walks the control
    words it needs for text flow and drops every destination group that
    carries metadata, embedded objects or pictures. It is a *reader*, not an
    interpreter -- there is no path here that executes an embedded object,
    which is exactly why RTF is not handed to a heavier library
    (Section 7.11: "No active content, ever").
    """
    text_value = content.decode("cp1252", errors="replace")
    out: list[str] = []
    skip_depth = 0
    depth = 0
    index = 0
    length = len(text_value)
    while index < length:
        char = text_value[index]
        if char == "{":
            depth += 1
            index += 1
            continue
        if char == "}":
            if skip_depth and depth <= skip_depth:
                skip_depth = 0
            depth -= 1
            index += 1
            continue
        if char == "\\":
            hex_match = _RTF_HEX_RE.match(text_value, index)
            if hex_match:
                if not skip_depth:
                    out.append(bytes([int(hex_match.group(1), 16)]).decode("cp1252", errors="replace"))
                index = hex_match.end()
                continue
            if index + 1 < length and text_value[index + 1] in "\\{}":
                if not skip_depth:
                    out.append(text_value[index + 1])
                index += 2
                continue
            control_match = _RTF_CONTROL_RE.match(text_value, index)
            if control_match:
                word = control_match.group(1)
                if word == "*" or word in _RTF_SKIPPED_DESTINATIONS:
                    skip_depth = skip_depth or depth
                elif not skip_depth:
                    if word in ("par", "line", "row", "sect", "page"):
                        out.append("\n")
                    elif word in ("tab", "cell"):
                        out.append("\t")
                index = control_match.end()
                continue
            index += 1
            continue
        if not skip_depth:
            out.append(char)
        index += 1
    lines = [" ".join(line.split()) for line in "".join(out).splitlines()]
    return "\n".join(line for line in lines if line)


class UnhandledFileTypeError(Exception):
    pass


def _inner_blocks(file_type: file_types.FileType, content: bytes) -> list[dict]:
    """
    The content blocks for one Tier 1 artifact, *without* the <document>
    delimiters -- so several artifacts (a multi-page TIFF's pages, an
    Outlook message's body plus its attachment) can be wrapped in one
    envelope by `build_content_blocks_for_artifacts`.
    """
    name = file_type.name

    if name == file_types.FileTypeName.PDF:
        text_content = _extract_pdf_text(content)
        if len(text_content) >= PDF_MIN_TEXT_CHARS:
            return [{"type": "text", "text": text_content}]
        encoded = base64.standard_b64encode(content).decode("utf-8")
        return [
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": encoded},
            }
        ]

    if name in _IMAGE_MEDIA_TYPES:
        encoded = base64.standard_b64encode(content).decode("utf-8")
        return [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": _IMAGE_MEDIA_TYPES[name],
                    "data": encoded,
                },
            }
        ]

    if name in _PLAIN_TEXT_LIKE:
        return [{"type": "text", "text": content.decode("utf-8", errors="replace")}]

    if name == file_types.FileTypeName.RTF:
        return [{"type": "text", "text": _extract_rtf_text(content)}]

    if name == file_types.FileTypeName.DOCX:
        return [{"type": "text", "text": _extract_docx_text(content)}]

    if name == file_types.FileTypeName.XLSX:
        return [{"type": "text", "text": _extract_xlsx_text(content)}]

    raise UnhandledFileTypeError(f"No Tier 1 content-block builder for {name}")


def build_content_blocks(file_type: file_types.FileType, content: bytes) -> list[dict]:
    """
    Native-text PDFs and text-like formats are sent as text; images and
    text-less (scanned) PDFs are sent to the model visually, matching
    docs/parse_pos.py's proven approach, extended to Tier 1's full format
    list (CLAUDE.md Section 8.2 / DECISIONS.md D-010). Tier 2 formats never
    reach this function as themselves -- they arrive as the Tier 1 artifacts
    app/conversion.py produced from them.
    """
    parts = _inner_blocks(file_type, content)
    if len(parts) == 1 and parts[0]["type"] == "text":
        return build_text_content(parts[0]["text"])
    return wrap_document_content(parts)


def build_content_blocks_for_artifacts(artifacts: list[PreparedArtifact]) -> list[dict]:
    """
    One <document> envelope for the whole document, however many Tier 1
    artifacts it turned into (CLAUDE.md Section 7.2: document content is
    always delimited, and it is data, never instructions).
    """
    if len(artifacts) == 1:
        return build_content_blocks(artifacts[0].file_type, artifacts[0].content)
    parts: list[dict] = []
    for artifact in artifacts:
        parts.extend(_inner_blocks(artifact.file_type, artifact.content))
    return wrap_document_content(parts)


def _mark_failed(tenant_id: UUID, document_id: UUID, *, raw_response: dict | None = None) -> None:
    with tenant_session(tenant_id) as session:
        session.execute(
            text(
                "UPDATE documents SET status='failed', raw_json=:raw_json, processed_at=now() WHERE id=:id"
            ),
            {"id": str(document_id), "raw_json": raw_response},
        )


def _overall_confidence(header_confidence: dict) -> Decimal:
    """
    CLAUDE.md Section 7.1: "Overall document confidence is the minimum of
    required-field confidences, not an average." All header fields are
    treated as the required set for this slice (no per-tenant schema yet --
    Section 7.13's per-tenant field config is a later phase).
    """
    numeric_values = [v for v in header_confidence.values() if isinstance(v, (int, float))]
    return Decimal(str(min(numeric_values))) if numeric_values else Decimal("0")


@celery_app.task(name="docflow.parse_and_extract")
def parse_and_extract(tenant_id: str, document_id: str) -> None:
    tid = UUID(tenant_id)
    did = UUID(document_id)

    with tenant_session(tid) as session:
        row = session.execute(
            text("SELECT storage_path, original_filename FROM documents WHERE id = :id"),
            {"id": str(did)},
        ).mappings().first()
        if row is None:
            logger.error("parse_and_extract_missing_document document_id=%s", did)
            return
        session.execute(
            text("UPDATE documents SET status = 'processing' WHERE id = :id"), {"id": str(did)}
        )
        storage_path = row["storage_path"]
        original_filename = row["original_filename"]

    content = read_file(storage_path)

    # Defense in depth (CLAUDE.md Section 7.11): re-validate here too. Never
    # trust that upload-time validation still holds by the time this task
    # runs -- the file on disk is the same hostile input either way.
    validation = file_types.validate_upload(content, original_filename)
    if not validation.ok:
        logger.error(
            "parse_and_extract_revalidation_failed document_id=%s error_code=%s",
            did,
            validation.error_code,
        )
        _mark_failed(
            tid, did, raw_response={"error_code": validation.error_code, "detail": validation.detail}
        )
        return

    # Tier 2 conversion and .msg/.eml unwrapping happen here, in the isolated
    # worker, before any parsing (CLAUDE.md Section 7.11). Every converted
    # artifact is re-validated inside prepare_artifacts; a conversion failure
    # is a clean, catalog-coded `failed`, never a crash.
    try:
        artifacts = prepare_artifacts(validation.file_type, content)
        content_blocks = build_content_blocks_for_artifacts(artifacts)
    except ConversionError as exc:
        logger.error(
            "parse_and_extract_conversion_failed document_id=%s error_code=%s",
            did,
            exc.error_code,
        )
        _mark_failed(tid, did, raw_response={"error_code": exc.error_code, "detail": exc.detail})
        return
    except Exception:
        logger.exception("parse_and_extract_parse_error document_id=%s", did)
        _mark_failed(tid, did, raw_response={"error_code": "DOC-005", "detail": "Parsing failed."})
        return

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    result = extract_document(client, content_blocks)

    if not result.ok:
        with tenant_session(tid) as session:
            session.execute(
                text(
                    """
                    UPDATE documents
                    SET status = 'failed', model_id = :model_id, prompt_hash = :prompt_hash,
                        schema_version = :schema_version, raw_json = :raw_json, processed_at = now()
                    WHERE id = :id
                    """
                ),
                {
                    "id": str(did),
                    "model_id": result.model_id,
                    "prompt_hash": result.prompt_hash,
                    "schema_version": result.schema_version,
                    "raw_json": result.raw_response,
                },
            )
        return

    header = result.header
    overall_confidence = _overall_confidence(result.header_confidence)

    with tenant_session(tid) as session:
        session.execute(
            text(
                """
                UPDATE documents
                SET status = 'needs_review', model_id = :model_id, prompt_hash = :prompt_hash,
                    schema_version = :schema_version, input_tokens = :input_tokens,
                    output_tokens = :output_tokens, est_cost_usd = :est_cost_usd,
                    injection_suspected = :injection_suspected,
                    overall_confidence = :overall_confidence, raw_json = :raw_json,
                    processed_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": str(did),
                "model_id": result.model_id,
                "prompt_hash": result.prompt_hash,
                "schema_version": result.schema_version,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "est_cost_usd": str(result.est_cost_usd) if result.est_cost_usd is not None else None,
                "injection_suspected": result.injection_suspected,
                "overall_confidence": str(overall_confidence),
                "raw_json": result.raw_response,
            },
        )

        session.execute(
            text(
                """
                INSERT INTO document_headers
                    (document_id, tenant_id, po_number, order_date, requested_delivery_date,
                     buyer_name, buyer_contact_email, ship_to_address, payment_terms,
                     order_total, currency, notes, header_confidence, currency_inferred)
                VALUES
                    (:document_id, :tenant_id, :po_number, :order_date, :requested_delivery_date,
                     :buyer_name, :buyer_contact_email, :ship_to_address, :payment_terms,
                     :order_total, :currency, :notes, :header_confidence, :currency_inferred)
                """
            ),
            {
                "document_id": str(did),
                "tenant_id": str(tid),
                "po_number": header["po_number"],
                "order_date": header["order_date"],
                "requested_delivery_date": header["requested_delivery_date"],
                "buyer_name": header["buyer_name"],
                "buyer_contact_email": header["buyer_contact_email"],
                "ship_to_address": header["ship_to_address"],
                "payment_terms": header["payment_terms"],
                "order_total": str(header["order_total"]) if header["order_total"] is not None else None,
                "currency": header["currency"],
                "notes": header["notes"],
                "header_confidence": result.header_confidence,
                "currency_inferred": result.currency_inferred,
            },
        )

        for line in result.lines:
            session.execute(
                text(
                    """
                    INSERT INTO document_lines
                        (id, document_id, tenant_id, line_number, sku, description,
                         quantity, unit, unit_price, line_total, confidence)
                    VALUES
                        (:id, :document_id, :tenant_id, :line_number, :sku, :description,
                         :quantity, :unit, :unit_price, :line_total, :confidence)
                    """
                ),
                {
                    "id": str(uuid4()),
                    "document_id": str(did),
                    "tenant_id": str(tid),
                    "line_number": line["line_number"],
                    "sku": line["sku"],
                    "description": line["description"],
                    "quantity": str(line["quantity"]) if line["quantity"] is not None else None,
                    "unit": line["unit"],
                    "unit_price": str(line["unit_price"]) if line["unit_price"] is not None else None,
                    "line_total": str(line["line_total"]) if line["line_total"] is not None else None,
                    "confidence": str(line["confidence"]) if line["confidence"] is not None else None,
                },
            )
