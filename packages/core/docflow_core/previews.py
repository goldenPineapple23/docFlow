"""
Viewable renderings of documents a browser cannot display
(CLAUDE.md Section 7.11 / 7.12, DECISIONS.md D-092).

Section 7.11 accepts Word, Excel, raw email, RTF and TIFF on purpose. No
browser renders any of them, and the review screen exists to show the
original beside the extracted values -- so for those formats the screen was
half blank and the reviewer had to download the file and open it elsewhere.

**Nothing here opens a file** (Stage 3c). Converting a TIFF is image
decoding and reading a DOCX is parsing, so both happen in the parse service
(apps/parse), inside a sandboxed job: it returns the image preview, and the
text the worker sends the model becomes the text preview here. This module
keeps only the preview types and the text step. The API only ever serves
bytes something else produced, and a test still asserts this module is not
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

from dataclasses import dataclass

from docflow_core.file_types import FileTypeName

# The image preview's size limits moved to the parse service with the code
# that makes it (apps/parse/parse_service/parsing/documents.py).
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
    read -- tables included, and for every format the parse service opens
    (legacy Office, OpenDocument and Outlook messages among them).
    """
    if not text or not text.strip():
        return None
    if len(text) > MAX_PREVIEW_CHARS:
        text = text[:MAX_PREVIEW_CHARS] + "\n\n[…truncated for viewing]"
    return Preview(content=text.encode("utf-8"), media_type=PLAIN_TEXT, kind="extracted_text")
