"""The text preview step (DECISIONS.md D-092). Since Stage 3c the image
preview and every file reader live in the parse service, where test D2
proves a Word file's text, tables included, is read exactly as before."""

from __future__ import annotations

from docflow_core import file_types, previews
from docflow_core.file_types import FileTypeName


def test_text_preview_truncates_and_refuses_blank_text():
    assert previews.text_preview("   \n ") is None
    long = previews.text_preview("x" * (previews.MAX_PREVIEW_CHARS + 10))
    assert long is not None and long.content.endswith("[…truncated for viewing]".encode())


def test_which_formats_get_which_preview():
    assert previews.is_image_like(FileTypeName.TIFF) and previews.is_image_like(FileTypeName.HEIC)
    assert previews.needs_preview(FileTypeName.DOCX) and not previews.is_image_like(FileTypeName.DOCX)
    assert not previews.needs_preview(FileTypeName.PDF)


def test_no_file_the_detector_reads_as_text_ever_gets_a_preview():
    """
    The viewer's text route takes a stored text preview first and, only
    without one, shows a TXT, CSV or MD file's `extracted_text_path`
    (founder, 2026-11-04; D-233 item 5). That is the text the worker sent the
    model only while none of those files ever gets a preview. If this fails,
    revisit the route's rule before changing the preview's.
    """
    for name in (FileTypeName.TXT, FileTypeName.CSV, FileTypeName.MD):
        assert not previews.needs_preview(name), name
    text_specs = [spec for spec in file_types.FORMATS if spec.handler == "text"]
    assert {spec.name for spec in text_specs} >= {FileTypeName.TXT, FileTypeName.CSV, FileTypeName.MD}
    for spec in text_specs:
        for extension in spec.extensions:
            detected = file_types.detect_file_type(b"PO Number: BCH-2291\nQty 4\n", extension)
            assert detected is not None, extension
            assert not previews.needs_preview(detected.name), (extension, detected.name)
