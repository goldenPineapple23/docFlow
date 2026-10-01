"""The text preview step (DECISIONS.md D-092). Since Stage 3c the image
preview and every file reader live in the parse service, where test D2
proves a Word file's text, tables included, is read exactly as before."""

from __future__ import annotations

from docflow_core import previews
from docflow_core.file_types import FileTypeName


def test_text_preview_truncates_and_refuses_blank_text():
    assert previews.text_preview("   \n ") is None
    long = previews.text_preview("x" * (previews.MAX_PREVIEW_CHARS + 10))
    assert long is not None and long.content.endswith("[…truncated for viewing]".encode())


def test_which_formats_get_which_preview():
    assert previews.is_image_like(FileTypeName.TIFF) and previews.is_image_like(FileTypeName.HEIC)
    assert previews.needs_preview(FileTypeName.DOCX) and not previews.is_image_like(FileTypeName.DOCX)
    assert not previews.needs_preview(FileTypeName.PDF)
