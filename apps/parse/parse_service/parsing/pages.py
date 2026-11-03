"""
Page images (viewer A2; D-228 items 1 and 2): every page of a PDF or a
TIFF, and a sender's single image, drawn inside a parse job as DocFlow's own
PNG or JPEG.

A page image is our output, not the original (CLAUDE.md 7.12, D-228): the
job decodes the hostile file in its sandbox and writes new pixels, with no
metadata carried over. The worker checks every image again before it stores
one (A3), because these are still bytes a parser made from a file we didn't
write.

`render(content, filename, first_page)` is the whole job. It re-validates
the bytes, applies the same page-count and password rules as
`documents.parse` (so the two routes can't disagree on what to refuse), and
answers pages from `first_page` until the next one would pass the answer's
budget or its time: `next_page` says where the next answer starts, or is
None at the end. Formats that have no pages to draw (office files, e-mail,
text) answer `page_count` 0.

Each page is drawn at PAGE_DPI and kept as the smaller of PNG and JPEG
(D-228 item 2). A PDF page too large for the pixel caps at PAGE_DPI is drawn
at the dpi that fits, and that dpi is in its answer. A page that can't be
drawn, or whose image passes PAGE_MAX_BYTES, is answered as not rendered,
with a reason; the other pages still come.
"""

from __future__ import annotations

import base64
import math
import time
from io import BytesIO

from docflow_core import file_types

from parse_service import config, fault_origin
from parse_service.parsing.conversion import ConversionError, pil_errors

PAGE_DPI = 200
PAGE_JPEG_QUALITY = 80
# A PDF's page size is in points, 72 to the inch.
_POINTS_PER_INCH = 72

_SINGLE_IMAGES = {
    file_types.FileTypeName.PNG,
    file_types.FileTypeName.JPEG,
    file_types.FileTypeName.GIF,
    file_types.FileTypeName.WEBP,
    file_types.FileTypeName.HEIC,
}


class _PageNotRendered(Exception):
    """One page that couldn't be drawn; the answer says why and goes on."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _pdfium_errors() -> tuple[type[BaseException], ...]:
    import pypdfium2

    return (pypdfium2.PdfiumError,)


def _pdf_errors() -> tuple[type[BaseException], ...]:
    from pdfminer.psexceptions import PSException
    from pdfplumber.utils.exceptions import PdfminerException

    return (PdfminerException, PSException, *_pdfium_errors())


def _encode(image) -> tuple[str, bytes]:
    """The smaller of PNG and JPEG for one page (D-228 item 2)."""
    from PIL import Image, ImageChops

    if image.mode not in ("1", "L", "RGB"):
        # Transparency goes onto white, as on paper, never onto black.
        rgba = image.convert("RGBA")
        image = Image.new("RGB", rgba.size, (255, 255, 255))
        image.paste(rgba, mask=rgba.getchannel("A"))
    if image.mode == "RGB":
        # A gray page (a gray scan, a fax) is stored as gray, as D-228's sizes
        # were measured; a page with any colour, a red stamp say, keeps it.
        red, green, blue = image.split()
        if ImageChops.difference(red, green).getbbox() is None and ImageChops.difference(green, blue).getbbox() is None:
            image = red
    png = BytesIO()
    image.save(png, format="PNG", optimize=True)
    jpeg = BytesIO()
    (image.convert("L") if image.mode == "1" else image).save(jpeg, format="JPEG", quality=PAGE_JPEG_QUALITY)
    if len(jpeg.getvalue()) < len(png.getvalue()):
        return "image/jpeg", jpeg.getvalue()
    return "image/png", png.getvalue()


def _page_answer(index: int, image, dpi: int | None) -> dict:
    media_type, data = _encode(image)
    if len(data) > config.PAGE_MAX_BYTES:
        raise _PageNotRendered("too_large")
    return {
        "index": index,
        "rendered": True,
        "media_type": media_type,
        "width": image.width,
        "height": image.height,
        "dpi": dpi,
        "data": base64.standard_b64encode(data).decode("ascii"),
    }


def _fitting_dpi(width_points: float, height_points: float) -> int:
    """PAGE_DPI, or the highest dpi at which the page fits both pixel caps."""
    scale = PAGE_DPI / _POINTS_PER_INCH
    width, height = width_points * scale, height_points * scale
    if width * height <= file_types.MAX_IMAGE_PIXELS and max(width, height) <= file_types.MAX_IMAGE_DIMENSION:
        return PAGE_DPI
    shrink = min(
        math.sqrt(file_types.MAX_IMAGE_PIXELS / (width * height)),
        file_types.MAX_IMAGE_DIMENSION / max(width, height),
    )
    return max(1, math.floor(PAGE_DPI * shrink))


def _checked_pdf_page_count(content: bytes) -> int:
    """The page count, after the rules `documents.parse` applies to a PDF:
    the page cap (DOC-016) and password protection (DOC-001)."""
    import pdfplumber
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            page_count = len(pdf.pages)
    except PDFPasswordIncorrect as exc:
        raise ConversionError("DOC-001", "PDF is password-protected.") from exc
    if page_count > file_types.MAX_DOCUMENT_PAGES:
        raise ConversionError("DOC-016", f"PDF has {page_count} pages (limit {file_types.MAX_DOCUMENT_PAGES}).")
    return page_count


def _pdf_pages(content: bytes):
    import pypdfium2

    page_count = _checked_pdf_page_count(content)
    try:
        pdf = pypdfium2.PdfDocument(content)
    except pypdfium2.PdfiumError as exc:
        if exc.err_code == pypdfium2.raw.FPDF_ERR_PASSWORD:
            raise ConversionError("DOC-001", "PDF is password-protected.") from exc
        raise
    try:
        if len(pdf) != page_count:
            # Two readers disagree about the file: draw no page of it.
            raise ConversionError("DOC-005", "The PDF's page count is not the same to both readers.")

        def page(index: int) -> dict:
            errors = _pdfium_errors()
            pdf_page = None
            try:
                pdf_page = pdf[index]
                dpi = _fitting_dpi(*pdf_page.get_size())
                bitmap = pdf_page.render(scale=dpi / _POINTS_PER_INCH)
                try:
                    image = bitmap.to_pil()
                    return _page_answer(index, image, dpi)
                finally:
                    bitmap.close()
            except fault_origin.catchable(errors) as exc:
                if not fault_origin.from_input(exc, errors):
                    raise
                raise _PageNotRendered("unreadable") from exc
            finally:
                if pdf_page is not None:
                    pdf_page.close()

        yield page_count
        yield page
    finally:
        pdf.close()


def _open_image(content: bytes, file_type: file_types.FileTypeName):
    from PIL import Image

    if file_type == file_types.FileTypeName.HEIC:
        import pillow_heif

        pillow_heif.register_heif_opener()
    # The same deliberate decompression-bomb guard as conversion (7.11).
    Image.MAX_IMAGE_PIXELS = file_types.MAX_IMAGE_PIXELS
    return Image.open(BytesIO(content))


def _upright(frame):
    """A phone photo stored sideways with an EXIF orientation, turned the way
    it was taken; nothing else of its metadata is kept."""
    from PIL import ImageOps

    return ImageOps.exif_transpose(frame)


def _image_pages(content: bytes, file_type: file_types.FileTypeName):
    errors = pil_errors()
    try:
        image = _open_image(content, file_type)
    except fault_origin.catchable(errors) as exc:
        if not fault_origin.from_input(exc, errors):
            raise
        raise ConversionError("DOC-017", f"Image could not be opened: {type(exc).__name__}") from exc
    try:
        width, height = image.size
        if (
            width > file_types.MAX_IMAGE_DIMENSION
            or height > file_types.MAX_IMAGE_DIMENSION
            or width * height > file_types.MAX_IMAGE_PIXELS
        ):
            raise ConversionError("DOC-018", f"Image is {width}x{height}px, beyond the pixel caps.")
        # A TIFF is a fax or a scan with pages; an animated GIF shows its first.
        page_count = getattr(image, "n_frames", 1) if file_type == file_types.FileTypeName.TIFF else 1
        if page_count > file_types.MAX_DOCUMENT_PAGES:
            raise ConversionError("DOC-016", f"TIFF has {page_count} pages (limit {file_types.MAX_DOCUMENT_PAGES}).")

        def page(index: int) -> dict:
            try:
                image.seek(index)
                frame = image.copy()
                frame.load()
                # An image keeps its own pixels: it isn't drawn at a dpi.
                return _page_answer(index, _upright(frame), None)
            except fault_origin.catchable(errors) as exc:
                if not fault_origin.from_input(exc, errors):
                    raise
                raise _PageNotRendered("unreadable") from exc

        yield page_count
        yield page
    finally:
        image.close()


def render(content: bytes, filename: str, first_page: int) -> dict:
    """
    The job's answer:
    - {"outcome": "rejected", "code": ...} -- the same catalog codes as
      `documents.parse` for the same file;
    - {"outcome": "ok", "file_type": ..., "page_count": N, "first_page": ...,
      "next_page": int | None, "pages": [...]} -- each page either
      {"index", "rendered": True, "media_type", "width", "height", "dpi",
      "data"} or {"index", "rendered": False, "reason"}. `dpi` is what a PDF
      page was drawn at; an image page's is None.
    """
    validation = file_types.validate_upload(content, filename)
    if not validation.ok or validation.file_type is None:
        return {"outcome": "rejected", "code": validation.error_code or "DOC-014"}
    file_type = validation.file_type.name
    if file_type == file_types.FileTypeName.PDF:
        source = _pdf_pages(content)
    elif file_type == file_types.FileTypeName.TIFF or file_type in _SINGLE_IMAGES:
        source = _image_pages(content, file_type)
    else:
        return {
            "outcome": "ok",
            "file_type": file_type.value,
            "page_count": 0,
            "first_page": first_page,
            "next_page": None,
            "pages": [],
        }
    try:
        try:
            page_count = next(source)
            page = next(source)
        except ConversionError as exc:
            return {"outcome": "rejected", "code": exc.error_code, "file_type": file_type.value}
        except fault_origin.catchable(_pdf_errors() + pil_errors()) as exc:
            if not fault_origin.from_input(exc, _pdf_errors() + pil_errors()):
                raise
            return {"outcome": "rejected", "code": "DOC-005", "file_type": file_type.value}

        started = time.monotonic()
        pages: list[dict] = []
        spent = 0
        next_page: int | None = None
        for index in range(first_page, page_count):
            if pages and time.monotonic() - started > config.PAGES_TIME_BUDGET_SECONDS:
                next_page = index
                break
            try:
                answer = page(index)
            except _PageNotRendered as not_rendered:
                answer = {"index": index, "rendered": False, "reason": not_rendered.reason}
            size = len(answer.get("data", ""))
            if pages and spent + size > config.PAGES_ANSWER_BUDGET_BYTES:
                next_page = index
                break
            pages.append(answer)
            spent += size
        return {
            "outcome": "ok",
            "file_type": file_type.value,
            "page_count": page_count,
            "first_page": first_page,
            "next_page": next_page,
            "pages": pages,
        }
    finally:
        source.close()
