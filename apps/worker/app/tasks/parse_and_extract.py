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
from docflow_core import (
    example_prompting,
    field_schema,
    file_types,
    model_runs,
    previews,
    review_digest,
)
from docflow_core.buyers import identify_and_link_buyer
from docflow_core.config import get_settings
from docflow_core.db import tenant_session
from docflow_core.duplicates import detect_document_relationships
from docflow_core.example_prompting import ExamplePlan
from docflow_core.extraction import (
    ExtractionResult,
    build_text_content,
    extract_document,
    wrap_document_content,
)
from docflow_core.field_schema import FieldSchema
from docflow_core.matching import match_document_lines
from docflow_core.storage import read_file, save_file
from docflow_core.validation import validate_document
from sqlalchemy import text
from sqlalchemy.orm import Session

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


def _envelope(parts: list[dict]) -> list[dict]:
    if len(parts) == 1 and parts[0]["type"] == "text":
        return build_text_content(parts[0]["text"])
    return wrap_document_content(parts)


def build_content_blocks(file_type: file_types.FileType, content: bytes) -> list[dict]:
    """
    Native-text PDFs and text-like formats are sent as text; images and
    text-less (scanned) PDFs are sent to the model visually, matching
    docs/parse_pos.py's proven approach, extended to Tier 1's full format
    list (CLAUDE.md Section 8.2 / DECISIONS.md D-010). Tier 2 formats never
    reach this function as themselves -- they arrive as the Tier 1 artifacts
    app/conversion.py produced from them.
    """
    return _envelope(_inner_blocks(file_type, content))


def _artifact_parts(artifacts: list[PreparedArtifact]) -> list[dict]:
    parts: list[dict] = []
    for artifact in artifacts:
        parts.extend(_inner_blocks(artifact.file_type, artifact.content))
    return parts


def build_content_blocks_for_artifacts(artifacts: list[PreparedArtifact]) -> list[dict]:
    """
    One <document> envelope for the whole document, however many Tier 1
    artifacts it turned into (CLAUDE.md Section 7.2: document content is
    always delimited, and it is data, never instructions).
    """
    return _envelope(_artifact_parts(artifacts))


# Stands in, in a text preview, for a page the model read visually (a scanned
# PDF or an image attached to an email). Deliberately generic: an artifact's
# label can carry an attachment filename, which is untrusted (Section 7.11).
_VISUAL_PART_PLACEHOLDER = (
    "[An attached page or image was read visually and cannot be shown as text here. "
    "Download the original to see it.]"
)


def build_preview(
    file_type: file_types.FileType, content: bytes, parts: list[dict]
) -> previews.Preview | None:
    """
    A viewable rendering for a format no browser displays (DECISIONS.md D-092),
    or None when the browser can show the original as it is.

    Image-like formats (TIFF, HEIC) are re-encoded from the original. For
    every other format the preview is the text this task extracted and sent
    to the model -- one extraction, two uses, so the reviewer sees exactly
    what DocFlow read, tables included, for every format the worker opens.
    """
    name = file_type.name
    if not previews.needs_preview(name):
        return None
    if previews.is_image_like(name):
        return previews.build_preview(content, name)
    chunks = [part["text"] if part["type"] == "text" else _VISUAL_PART_PLACEHOLDER for part in parts]
    return previews.text_preview("\n\n".join(chunks))


_PREVIEW_SUFFIXES = {"image/png": ".png", "image/jpeg": ".jpg"}


def _store_preview(
    tenant_id: UUID, document_id: UUID, file_type: file_types.FileType, content: bytes, parts: list[dict]
) -> None:
    """
    Best effort, by design: a preview is a convenience, and a document that
    cannot be previewed still reviews fine against its extracted values --
    the viewer says so. A failure here never touches the document's status.
    """
    try:
        preview = build_preview(file_type, content, parts)
        if preview is None:
            return
        suffix = _PREVIEW_SUFFIXES.get(preview.media_type, ".txt")
        preview_path = save_file(tenant_id, f"preview{suffix}", preview.content)
        with tenant_session(tenant_id) as session:
            session.execute(
                text(
                    """
                    UPDATE documents
                    SET preview_storage_path = :path, preview_media_type = :media_type,
                        preview_kind = :kind
                    WHERE id = :id
                    """
                ),
                {
                    "id": str(document_id),
                    "path": preview_path,
                    "media_type": preview.media_type,
                    "kind": preview.kind,
                },
            )
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.error("preview_failed document_id=%s error_type=%s", document_id, type(exc).__name__)


def _store_extracted_text(tenant_id: UUID, document_id: UUID, parts: list[dict]) -> None:
    """
    Keep the text this task sent the model, so the order can later be shown
    to the model as a past example (Section 7.13: "the same extracted text
    the parser produced"). Only when every part is text: a document read
    visually has no text of its own and can never be an example -- an example
    is never a file or an image. Best effort, like the preview: a document
    whose text couldn't be kept reviews and exports exactly the same.
    """
    if not parts or any(part["type"] != "text" for part in parts):
        return
    body = "\n\n".join(part["text"] for part in parts).strip()
    if not body:
        return
    try:
        path = save_file(tenant_id, "extracted.txt", body.encode("utf-8"))
        with tenant_session(tenant_id) as session:
            session.execute(
                text("UPDATE documents SET extracted_text_path = :path WHERE id = :id"),
                {"id": str(document_id), "path": path},
            )
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.error(
            "extracted_text_store_failed document_id=%s error_type=%s", document_id, type(exc).__name__
        )


def _plan_examples(
    client: anthropic.Anthropic,
    tenant_id: UUID,
    document_id: UUID,
    sender_email: str | None,
    parts: list[dict],
) -> ExamplePlan:
    """
    Approved-example prompting (Section 7.13). Any failure here means "no
    examples", which is exactly how a tenant with the feature off is read --
    the feature must never cost a document its extraction.
    """
    try:
        plan = example_prompting.plan(
            client,
            tenant_id,
            document_id,
            sender_email=sender_email,
            parts=parts,
            session_factory=tenant_session,
        )
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.error("example_planning_failed document_id=%s error_type=%s", document_id, type(exc).__name__)
        return ExamplePlan(outcome="planning_failed")
    # IDs and outcomes only (Section 7.10).
    logger.info(
        "example_plan document_id=%s outcome=%s identified_by=%s examples=%d routing=%s",
        document_id,
        plan.outcome,
        plan.identified_by,
        len(plan.examples),
        plan.routing is not None,
    )
    return plan


def _record_runs(
    session: Session, tenant_id: UUID, document_id: UUID, plan: ExamplePlan, result: ExtractionResult
) -> UUID:
    """Every model call this document cost, one extraction_runs row each
    (D-142) -- the routing read first, because it happened first."""
    if plan.routing is not None:
        model_runs.record_routing(session, tenant_id, document_id, plan.routing)
    return model_runs.record_extraction(session, tenant_id, document_id, result)


def _total_cost(plan: ExamplePlan, result: ExtractionResult) -> Decimal | None:
    """The document's whole model bill: extraction plus the routing read, so
    cost per document and margin on the dashboard stay honest."""
    routing_cost = plan.routing.est_cost_usd if plan.routing is not None else None
    costs = [c for c in (result.est_cost_usd, routing_cost) if c is not None]
    return sum(costs, Decimal("0")) if costs else None


def _money(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _mark_failed(tenant_id: UUID, document_id: UUID, *, raw_response: dict | None = None) -> None:
    with tenant_session(tenant_id) as session:
        session.execute(
            text(
                "UPDATE documents SET status='failed', raw_json=:raw_json, processed_at=now() WHERE id=:id"
            ),
            {"id": str(document_id), "raw_json": raw_response},
        )


_PROVENANCE_HEADER_FIELDS = (
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

_PROVENANCE_LINE_FIELDS = ("sku", "description", "quantity", "unit", "unit_price", "line_total")


def _extracted_provenance(values: dict, fields: tuple[str, ...]) -> dict:
    """
    Per-field provenance (Section 9 / Section 7.1: "Provenance for every
    value: extracted, edited-by-human, or mapped"). Every value this task
    writes came straight from the model, so every present field is
    `extracted`; a field the model returned as null has no value and
    therefore no provenance. `learned_rule:<id>` and
    `human_edit:<review_action_id>` are written by the matching and review
    slices that produce them.
    """
    return {name: "extracted" for name in fields if values.get(name) is not None}


def _overall_confidence(header_confidence: dict, schema: FieldSchema) -> Decimal:
    """
    CLAUDE.md Section 7.1: "Overall document confidence is the minimum of
    required-field confidences, not an average." Required means required for
    THIS tenant (D-120): the fields its current field schema marks required.
    """
    return schema.overall_confidence(header_confidence)


@celery_app.task(name="docflow.parse_and_extract")
def parse_and_extract(tenant_id: str, document_id: str) -> None:
    tid = UUID(tenant_id)
    did = UUID(document_id)

    with tenant_session(tid) as session:
        row = session.execute(
            text(
                "SELECT storage_path, original_filename, status, sender_email "
                "FROM documents WHERE id = :id"
            ),
            {"id": str(did)},
        ).mappings().first()
        if row is None:
            logger.error("parse_and_extract_missing_document document_id=%s", did)
            return
        if row.get("status") == "staged":
            # A test-batch file waits for the founder's "Run extraction"
            # (Section 7.15.2 Step 7, D-112), which moves it to 'pending'
            # first. Anything that enqueues it earlier is a bug; it must not
            # reach the model.
            logger.error("parse_and_extract_staged_document document_id=%s", did)
            return
        session.execute(
            text("UPDATE documents SET status = 'processing' WHERE id = :id"), {"id": str(did)}
        )
        storage_path = row["storage_path"]
        original_filename = row["original_filename"]
        sender_email = row.get("sender_email")

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
        parts = _artifact_parts(artifacts)
        content_blocks = _envelope(parts)
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

    # Before the model call, so the reviewer can see the original even if
    # extraction fails. Stays here in the worker: building a preview is
    # parsing (Section 7.11), and the API only serves what this wrote.
    _store_preview(tid, did, validation.file_type, content, parts)
    _store_extracted_text(tid, did, parts)

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    plan = _plan_examples(client, tid, did, sender_email, parts)
    result = extract_document(client, content_blocks, examples=plan.examples)

    if not result.ok:
        with tenant_session(tid) as session:
            _record_runs(session, tid, did, plan, result)
            session.execute(
                text(
                    """
                    UPDATE documents
                    SET status = 'failed', model_id = :model_id, prompt_hash = :prompt_hash,
                        schema_version = :schema_version, raw_json = :raw_json,
                        est_cost_usd = :est_cost_usd, processed_at = now()
                    WHERE id = :id
                    """
                ),
                {
                    "id": str(did),
                    "model_id": result.model_id,
                    "prompt_hash": result.prompt_hash,
                    "schema_version": result.schema_version,
                    "raw_json": result.raw_response,
                    "est_cost_usd": _money(_total_cost(plan, result)),
                },
            )
        return

    header = result.header
    # The tenant's field schema decides which fields count towards the
    # document's confidence and which are checked (D-120). Read once here and
    # recorded on the document, so a later change never makes this
    # document's numbers unexplainable (Section 7.13: versioned).
    with tenant_session(tid) as session:
        schema = field_schema.current(session, tid)
    overall_confidence = _overall_confidence(result.header_confidence, schema)

    with tenant_session(tid) as session:
        run_id = _record_runs(session, tid, did, plan, result)
        session.execute(
            text(
                """
                UPDATE documents
                SET status = 'needs_review', model_id = :model_id, prompt_hash = :prompt_hash,
                    schema_version = :schema_version, input_tokens = :input_tokens,
                    output_tokens = :output_tokens, est_cost_usd = :est_cost_usd,
                    injection_suspected = :injection_suspected,
                    overall_confidence = :overall_confidence, raw_json = :raw_json,
                    field_schema_version = :field_schema_version,
                    current_extraction_run_id = :run_id, processed_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": str(did),
                "model_id": result.model_id,
                "prompt_hash": result.prompt_hash,
                "schema_version": result.schema_version,
                "field_schema_version": schema.version or None,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "est_cost_usd": _money(_total_cost(plan, result)),
                "run_id": str(run_id),
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
                     order_total, currency, notes, header_confidence, currency_inferred,
                     field_provenance)
                VALUES
                    (:document_id, :tenant_id, :po_number, :order_date, :requested_delivery_date,
                     :buyer_name, :buyer_contact_email, :ship_to_address, :payment_terms,
                     :order_total, :currency, :notes, :header_confidence, :currency_inferred,
                     :field_provenance)
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
                "field_provenance": _extracted_provenance(header, _PROVENANCE_HEADER_FIELDS),
            },
        )

        for line in result.lines:
            session.execute(
                text(
                    """
                    INSERT INTO document_lines
                        (id, document_id, tenant_id, line_number, sku, description,
                         quantity, unit, unit_price, line_total, confidence, field_provenance)
                    VALUES
                        (:id, :document_id, :tenant_id, :line_number, :sku, :description,
                         :quantity, :unit, :unit_price, :line_total, :confidence, :field_provenance)
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
                    "field_provenance": _extracted_provenance(line, _PROVENANCE_LINE_FIELDS),
                },
            )

    # Buyer identification runs in its own transaction, after the extraction
    # above has committed (CLAUDE.md Section 7.6). Deliberately not part of
    # the same transaction: a failure here must never roll back a successful
    # extraction and cost the document its header and lines. The document is
    # already `needs_review` and simply has no buyer link, which is the same
    # state a document with no extracted buyer name legitimately has; a
    # re-run links it.
    buyer_id: UUID | None = None
    try:
        with tenant_session(tid) as session:
            buyer_id = identify_and_link_buyer(
                session,
                tid,
                did,
                buyer_name=header["buyer_name"],
                buyer_contact_email=header["buyer_contact_email"],
            ).buyer_id
    except Exception as exc:  # noqa: BLE001 -- see the comment above
        # No exception message is logged: a database error's text can contain
        # the bound parameters, which here are customer data (Section 7.10).
        logger.error(
            "buyer_identification_failed document_id=%s error_type=%s", did, type(exc).__name__
        )

    # Catalog matching runs after buyer identification, because Section 7.6's
    # learned mappings are scoped to a buyer -- and in its own transaction for
    # the same reason buyer identification is (D-058): a matching failure must
    # never roll back the paid-for model call. A document whose matching
    # failed is `needs_review` with unmatched lines, which is exactly the
    # state a document with no catalog hits legitimately has; a re-run matches
    # it, and re-running is idempotent (it never overwrites a human's answer).
    #
    # `buyer_id` being None is not a failure path: matching simply runs with
    # the tenant-wide rules only, and never with another buyer's.
    try:
        with tenant_session(tid) as session:
            summary = match_document_lines(session, tid, did, buyer_id=buyer_id)
        # Counts and IDs only -- never a SKU, a description or a score, which
        # are customer/document data (Section 7.10).
        logger.info(
            "matching_complete document_id=%s considered=%d matched=%d",
            did,
            summary.lines_considered,
            summary.lines_matched,
        )
    except Exception as exc:  # noqa: BLE001 -- see the comment above
        logger.error("matching_failed document_id=%s error_type=%s", did, type(exc).__name__)

    # Duplicate / change-order detection (CLAUDE.md Section 7.8). It runs here
    # rather than only at ingest because the PO number the change-order case
    # needs does not exist until extraction has run. The exact-content half
    # already ran at ingest, where the hash was final; re-running it is
    # idempotent and reaches the same answer, and running both halves in one
    # place is what keeps a document uploaded by any path consistently
    # flagged. Own transaction, same reason as above (D-058): nothing here is
    # worth losing a paid-for model call over, and a document with no
    # relationship flags is the state the overwhelming majority of documents
    # are legitimately in.
    try:
        with tenant_session(tid) as session:
            relationships = detect_document_relationships(session, tid, did)
        # Booleans and IDs only -- never a PO number or a hash (Section 7.10).
        logger.info(
            "duplicate_detection_complete document_id=%s duplicate=%s change_order=%s",
            did,
            relationships.is_possible_duplicate,
            relationships.is_possible_change_order,
        )
    except Exception as exc:  # noqa: BLE001 -- see the comment above
        logger.error("duplicate_detection_failed document_id=%s error_type=%s", did, type(exc).__name__)

    # Validation runs last (CLAUDE.md Section 7.7), because it reports on
    # everything the steps before it concluded: the extracted values, the
    # matching slice's `uom_mismatch`, and the duplicate/change-order flags
    # just written. It changes no value -- it only writes `document_warnings`.
    try:
        with tenant_session(tid) as session:
            # The same schema the confidence above was computed with, so one
            # document is never half-checked under two versions.
            validation = validate_document(session, tid, did, schema.rules())
        # Counts only -- never a field name's value, a total or a date
        # (Section 7.10).
        logger.info(
            "validation_complete document_id=%s warnings=%d created=%d resolved=%d",
            did,
            validation.evaluated,
            validation.created,
            validation.resolved,
        )
    except Exception as exc:  # noqa: BLE001 -- see the comment above
        logger.error("validation_failed document_id=%s error_type=%s", did, type(exc).__name__)

    # The "needs review" digest (slice 5.8c, D-131): add this order to the
    # tenant's pending digest email. Last, and in its own transaction, for the
    # same reason as every step above: a document that is ready for review
    # must never lose its extraction over a notification.
    try:
        with tenant_session(tid) as session:
            review_digest.note_needs_review(session, tid, did)
    except Exception as exc:  # noqa: BLE001 -- see the comment above
        logger.error("review_digest_failed document_id=%s error_type=%s", did, type(exc).__name__)
