"""
Viewable renderings of documents a browser cannot display
(CLAUDE.md Section 7.11 / 7.12, DECISIONS.md D-092).

Section 7.11 accepts Word, Excel, raw email, RTF and TIFF on purpose. No
browser renders any of them, and the review screen exists to show the
original beside the extracted values -- so for those formats the screen was
half blank and the reviewer had to download the file and open it elsewhere.

**Nothing in this module may be called from the web process.** Section 7.11:
"Parsing never runs in the web process. All document parsing (PDF text
extraction, DOCX/XLSX/RTF reading, image decoding) runs in an isolated
worker." Converting a TIFF is image decoding; reading a DOCX is parsing.
Callers are the worker and the seeding scripts. The API only ever serves
bytes that something else decoded, and a test asserts this module is not
imported under `apps/api/app/`.

Two kinds of preview, and the difference is told to the reviewer rather than
hidden:

  * `converted_image` -- the page as it was sent, re-encoded into a format a
    browser shows. A TIFF fax becomes a PNG. Nothing is lost that a reader
    would notice.
  * `extracted_text` -- the text out of a Word file, a spreadsheet or an
    email. This is **not** the original layout, and a reviewer checking a
    number against it is reading DocFlow's rendering rather than the
    document. The screen says so.

Every limit in Section 7.11 still applies: this runs on content that has
already passed magic-byte validation and the size caps, and a preview that
cannot be produced is simply absent rather than an error.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from docflow_core.file_types import FileTypeName

# Big enough to read a purchase order, small enough not to bloat storage.
MAX_PREVIEW_PIXELS = 2200
# A preview is a convenience, never a reason to hold a document up.
MAX_PREVIEW_BYTES = 8 * 1024 * 1024
# Text previews are for reading a page, not for dumping a spreadsheet.
MAX_PREVIEW_CHARS = 200_000

PLAIN_TEXT = "text/plain; charset=utf-8"


@dataclass(frozen=True)
class Preview:
    content: bytes
    media_type: str
    kind: str  # 'converted_image' | 'extracted_text'


# Formats whose pages are images and can simply be re-encoded.
_IMAGE_LIKE = {FileTypeName.TIFF, FileTypeName.HEIC}

# Formats whose content is text, however it is packaged.
_TEXT_LIKE = {
    FileTypeName.DOCX,
    FileTypeName.DOC,
    FileTypeName.XLSX,
    FileTypeName.XLS,
    FileTypeName.ODT,
    FileTypeName.ODS,
    FileTypeName.RTF,
    FileTypeName.EML,
    FileTypeName.MSG,
    FileTypeName.HTML,
}


def needs_preview(file_type: FileTypeName) -> bool:
    return file_type in _IMAGE_LIKE or file_type in _TEXT_LIKE


def is_image_like(file_type: FileTypeName) -> bool:
    return file_type in _IMAGE_LIKE


def text_preview(text: str) -> Preview | None:
    """
    A text preview from text a caller already extracted. The worker uses this
    with the text it sent the model, so a reviewer sees exactly what DocFlow
    read -- tables included, and for every format the worker can open
    (legacy Office, OpenDocument and Outlook messages among them), not only
    the ones `_extract_text` below knows.
    """
    if not text or not text.strip():
        return None
    if len(text) > MAX_PREVIEW_CHARS:
        text = text[:MAX_PREVIEW_CHARS] + "\n\n[…truncated for viewing]"
    return Preview(content=text.encode("utf-8"), media_type=PLAIN_TEXT, kind="extracted_text")


def build_preview(content: bytes, file_type: FileTypeName) -> Preview | None:
    """
    A viewable rendering, or None if one cannot be made.

    Never raises for a malformed file: a preview is a convenience, and a
    document that cannot be previewed still reviews fine against its
    extracted values. The caller stores what it gets and moves on.
    """
    try:
        if file_type in _IMAGE_LIKE:
            return _image_preview(content)
        if file_type in _TEXT_LIKE:
            return _text_preview(content, file_type)
    except Exception:
        # Deliberately broad. Every parser reachable from here is a library
        # handling a file a stranger sent (Section 7.11), and the correct
        # outcome for any failure is "no preview", never a failed document.
        return None
    return None


def _image_preview(content: bytes) -> Preview | None:
    from PIL import Image

    with Image.open(io.BytesIO(content)) as image:
        image.load()
        # Multi-page TIFF is a fax with several pages; the first is the one
        # the order starts on. Paging through belongs with the multi-page
        # viewer work, not here.
        if getattr(image, "n_frames", 1) > 1:
            image.seek(0)
        converted = image.convert("RGB")
        converted.thumbnail((MAX_PREVIEW_PIXELS, MAX_PREVIEW_PIXELS))
        buf = io.BytesIO()
        converted.save(buf, format="PNG", optimize=True)

    data = buf.getvalue()
    if len(data) > MAX_PREVIEW_BYTES:
        buf = io.BytesIO()
        converted.save(buf, format="JPEG", quality=80)
        data = buf.getvalue()
        if len(data) > MAX_PREVIEW_BYTES:
            return None
        return Preview(content=data, media_type="image/jpeg", kind="converted_image")

    return Preview(content=data, media_type="image/png", kind="converted_image")


def _text_preview(content: bytes, file_type: FileTypeName) -> Preview | None:
    return text_preview(_extract_text(content, file_type))


def _extract_text(content: bytes, file_type: FileTypeName) -> str:
    if file_type in (FileTypeName.DOCX, FileTypeName.ODT):
        from docx import Document

        document = Document(io.BytesIO(content))
        lines: list[str] = [p.text for p in document.paragraphs]
        # A Word PO's line items almost always live in a table, which
        # `paragraphs` skips entirely.
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.replace("\n", " ").strip() for cell in row.cells]
                if any(cells):
                    lines.append("\t".join(cells))
        return "\n".join(lines)

    if file_type in (FileTypeName.XLSX, FileTypeName.ODS):
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        lines = []
        for sheet in workbook.worksheets:
            lines.append(f"--- {sheet.title} ---")
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if cell is None else str(cell) for cell in row]
                if any(cell.strip() for cell in cells):
                    lines.append("\t".join(cells).rstrip())
        workbook.close()
        return "\n".join(lines)

    if file_type in (FileTypeName.EML, FileTypeName.MSG):
        from email import policy
        from email.parser import BytesParser

        message = BytesParser(policy=policy.default).parsebytes(content)
        header_lines = [
            f"{name}: {message[name]}"
            for name in ("From", "To", "Subject", "Date")
            if message[name]
        ]
        body = message.get_body(preferencelist=("plain",))
        body_text = body.get_content() if body else ""
        return "\n".join(header_lines) + "\n\n" + body_text

    if file_type == FileTypeName.RTF:
        # RTF is text with control words; strip the obvious ones rather than
        # pull in a dependency for a preview.
        import re

        raw = content.decode("latin-1", errors="replace")
        raw = re.sub(r"\\'[0-9a-fA-F]{2}", "", raw)
        raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", raw)
        return raw.replace("{", "").replace("}", "")

    if file_type == FileTypeName.HTML:
        # Tags stripped to text. The result is served as text/plain and is
        # never rendered as markup (Section 7.12).
        import re

        raw = content.decode("utf-8", errors="replace")
        raw = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)
        raw = re.sub(r"(?s)<[^>]+>", " ", raw)
        return re.sub(r"[ \t]{2,}", " ", raw)

    return ""
