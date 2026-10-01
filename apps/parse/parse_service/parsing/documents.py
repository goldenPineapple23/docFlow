"""
Reading one document: what the worker's `parse_and_extract` did itself
before Stage 3c, moved here so it runs only inside a parse service job.

`parse(content, filename)` is the whole job: re-validate the bytes (never
trust that the upload-time check still holds), convert or unwrap Tier 2
formats, read every Tier 1 artifact into the content parts the model call
takes, and, for TIFF and HEIC, make the image preview. The answer is plain
JSON-ready data; the worker checks it again before using it (Section 7.11:
a converter's output is still bytes DocFlow didn't write).

Native-text PDFs and text-like formats become text; images and text-less
(scanned) PDFs go to the model visually (docs/parse_pos.py's approach,
DECISIONS.md D-010).
"""

from __future__ import annotations

import base64
import io
import re
import sys
from io import BytesIO

from docflow_core import file_types

from parse_service.parsing.conversion import (
    ConversionError,
    PreparedArtifact,
    format_cell_value,
    prepare_artifacts,
)

# Below this many extracted characters, a PDF is treated as scanned/
# image-only and sent to the model visually instead (mirrors docs/parse_pos.py).
PDF_MIN_TEXT_CHARS = 100

# The image preview for formats no browser shows (TIFF, HEIC; D-092).
MAX_PREVIEW_PIXELS = 2200
MAX_PREVIEW_BYTES = 8 * 1024 * 1024

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

_IMAGE_LIKE = {file_types.FileTypeName.TIFF, file_types.FileTypeName.HEIC}


def _extract_pdf_text(content: bytes) -> str:
    """
    Also the enforcement point for two Section 7.11 limits a PDF can only be
    checked for once it is open: the page-count cap
    (`file_types.MAX_DOCUMENT_PAGES`) and password protection. Both raise a
    catalog-coded ConversionError, so the answer is the same clean
    rejection a Tier 2 conversion failure gets -- and the file is never
    handed to the model.
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


def inner_blocks(file_type: file_types.FileType, content: bytes) -> list[dict]:
    """
    The content parts for one Tier 1 artifact, *without* the <document>
    delimiters: the worker wraps all of a document's parts in one envelope
    (CLAUDE.md Section 7.2), so a multi-page TIFF's pages or an Outlook
    message's body plus its attachment arrive as one document.
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


def artifact_parts(artifacts: list[PreparedArtifact]) -> list[dict]:
    parts: list[dict] = []
    for artifact in artifacts:
        parts.extend(inner_blocks(artifact.file_type, artifact.content))
    return parts


def image_preview(content: bytes) -> dict | None:
    """
    A PNG (or, when too large, JPEG) of a TIFF's or HEIC's first page, or
    None. Best effort: a document that can't be previewed still reviews
    fine against its extracted values.
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(content)) as image:
            image.load()
            # Multi-page TIFF is a fax with several pages; the first is the
            # one the order starts on.
            if getattr(image, "n_frames", 1) > 1:
                image.seek(0)
            converted = image.convert("RGB")
            converted.thumbnail((MAX_PREVIEW_PIXELS, MAX_PREVIEW_PIXELS))
            buf = io.BytesIO()
            converted.save(buf, format="PNG", optimize=True)

        data = buf.getvalue()
        media_type = "image/png"
        if len(data) > MAX_PREVIEW_BYTES:
            buf = io.BytesIO()
            converted.save(buf, format="JPEG", quality=80)
            data = buf.getvalue()
            media_type = "image/jpeg"
            if len(data) > MAX_PREVIEW_BYTES:
                return None
    except Exception:  # noqa: BLE001 -- "no preview" is the right outcome for any failure
        return None
    return {
        "media_type": media_type,
        "kind": "converted_image",
        "data": base64.standard_b64encode(data).decode("ascii"),
    }


def parse(content: bytes, filename: str) -> dict:
    """
    The job's answer for one document:
    - {"outcome": "rejected", "code": ...} -- a catalog code, as before 3c;
    - {"outcome": "ok", "file_type": ..., "parts": [...], "image_preview": ...}.
    """
    validation = file_types.validate_upload(content, filename)
    if not validation.ok or validation.file_type is None:
        return {"outcome": "rejected", "code": validation.error_code or "DOC-014"}
    file_type = validation.file_type
    try:
        parts = artifact_parts(prepare_artifacts(file_type, content))
    except ConversionError as exc:
        # The reason (for LibreOffice, its own stderr) reaches the service log
        # through the job's stderr; the answer carries the catalog code only.
        print(f"rejected code={exc.error_code} reason={exc.detail}", file=sys.stderr)
        return {"outcome": "rejected", "code": exc.error_code, "file_type": file_type.name.value}
    except MemoryError:
        raise  # a limit, not the file's content: the supervisor answers `stopped`
    except Exception as exc:  # noqa: BLE001 -- any library failure on a hostile file, as before 3c
        print(f"rejected code=DOC-005 reason={type(exc).__name__}", file=sys.stderr)
        return {"outcome": "rejected", "code": "DOC-005", "file_type": file_type.name.value}
    preview = image_preview(content) if file_type.name in _IMAGE_LIKE else None
    return {
        "outcome": "ok",
        "file_type": file_type.name.value,
        "parts": parts,
        "image_preview": preview,
    }
