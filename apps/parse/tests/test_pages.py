"""
Page images (viewer A2; D-228 items 1 and 2): every page of a PDF or TIFF,
and a sender's single image, drawn inside a parse job as our own PNG or JPEG.

These run in-process on this machine and in CI's unit job, and over HTTP to
the real sandboxed service in CI's image job (`PARSE_SERVICE_URL`; see
conftest). The tests that change a budget or a cap to make it bite are in
test_pages_budgets.py, in-process only: the running service's settings
can't be changed from here. Every fixture is the committed fake set or built
at test time with obviously fake content (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import base64
from io import BytesIO

import pytest
from docflow_core import file_types

from parse_service import config
from parse_service.parsing import pages
from tests import conftest
from tests import fixture_builders as fb
from tests.conftest import FIXTURES

POSITIVE = FIXTURES / "positive"
HOSTILE = FIXTURES / "hostile"

# The committed positive fixtures that have pages, and how many.
PAGED = {
    "po.pdf": 1,
    "po-scanned.pdf": 1,
    "po.tif": 2,
    "po.png": 1,
    "po.jpg": 1,
    "po.gif": 1,
    "po.webp": 1,
    "po.heic": 1,
}
# The rest of the positive set: shown as text, so no pages (D-228 items 4, 11).
UNPAGED = sorted(
    path.name for path in POSITIVE.iterdir() if path.is_file() and path.name not in PAGED
)


def _image(page: dict):
    from PIL import Image

    return Image.open(BytesIO(base64.standard_b64decode(page["data"])))


def _all_pages(content: bytes, filename: str) -> list[dict]:
    """Every page, asking again from `next_page` as the worker will (A3)."""
    collected: list[dict] = []
    first_page = 0
    while True:
        answer = conftest.answer_for("pages", content, filename, first_page=first_page)
        assert answer["outcome"] == "ok", answer
        collected.extend(answer["pages"])
        if answer["next_page"] is None:
            return collected
        assert answer["next_page"] > first_page
        first_page = answer["next_page"]


# ── What is drawn ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("name,count", sorted(PAGED.items()))
def test_every_page_of_each_paged_format_is_drawn_as_a_png_or_jpeg(name, count):
    answer = conftest.answer("pages", POSITIVE / name)
    assert answer["outcome"] == "ok", answer
    assert answer["page_count"] == count
    assert answer["next_page"] is None
    assert [page["index"] for page in answer["pages"]] == list(range(count))
    for page in answer["pages"]:
        assert page["rendered"] is True
        image = _image(page)
        assert {"PNG": "image/png", "JPEG": "image/jpeg"}[image.format] == page["media_type"]
        assert image.size == (page["width"], page["height"])
        # Our own pixels: nothing of the sender's metadata comes across.
        assert "exif" not in image.info
        assert "icc_profile" not in image.info


@pytest.mark.parametrize("name", UNPAGED)
def test_formats_shown_as_text_answer_no_pages(name):
    answer = conftest.answer("pages", POSITIVE / name)
    assert answer["outcome"] == "ok", answer
    assert answer["page_count"] == 0
    assert answer["pages"] == []
    assert answer["next_page"] is None


def test_a_pdf_page_is_drawn_at_200_dpi():
    page = conftest.answer("pages", POSITIVE / "po.pdf")["pages"][0]
    # US Letter, 8.5 x 11 in.
    assert (page["width"], page["height"], page["dpi"]) == (1700, 2200, 200)


def test_an_image_keeps_its_own_pixels_and_has_no_dpi():
    page = conftest.answer("pages", POSITIVE / "po.png")["pages"][0]
    assert page["dpi"] is None
    from PIL import Image

    with Image.open(POSITIVE / "po.png") as original:
        assert (page["width"], page["height"]) == original.size


def test_every_page_of_a_multi_page_pdf_and_tiff_is_drawn_in_order():
    pdf = _all_pages(fb.build_pdf([f"Acme Test Distributor page {i}" for i in range(1, 8)]), "po.pdf")
    tiff = _all_pages(fb.build_tiff(pages=5), "po.tif")
    assert [page["index"] for page in pdf] == list(range(7))
    assert [page["index"] for page in tiff] == list(range(5))
    assert all(page["rendered"] for page in pdf + tiff)


def test_the_same_file_gives_the_same_bytes_twice():
    content = fb.build_pdf(["Acme Test Distributor", "page two"])
    first = conftest.answer_for("pages", content, "po.pdf")
    second = conftest.answer_for("pages", content, "po.pdf")
    assert [page["data"] for page in first["pages"]] == [page["data"] for page in second["pages"]]


def test_first_page_starts_the_answer_there():
    content = fb.build_pdf(["one", "two", "three"])
    answer = conftest.answer_for("pages", content, "po.pdf", first_page=2)
    assert [page["index"] for page in answer["pages"]] == [2]
    assert answer["first_page"] == 2
    past_the_end = conftest.answer_for("pages", content, "po.pdf", first_page=5)
    assert past_the_end["pages"] == [] and past_the_end["next_page"] is None


def test_a_photo_stored_sideways_is_turned_upright():
    from PIL import Image

    photo = Image.new("RGB", (400, 200), (255, 255, 255))
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90 CW to view"
    buffer = BytesIO()
    photo.save(buffer, format="JPEG", exif=exif.tobytes())
    page = conftest.answer_for("pages", buffer.getvalue(), "photo.jpg")["pages"][0]
    assert (page["width"], page["height"]) == (200, 400)
    assert "exif" not in _image(page).info


def test_a_gray_page_is_stored_as_gray_and_a_page_with_colour_keeps_it():
    from PIL import Image, ImageDraw

    def page_with(stamp_colour):
        image = Image.new("RGB", (400, 300), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.text((20, 20), "Acme Test Distributor PO TEST-0042", fill=(0, 0, 0))
        draw.rectangle((200, 150, 380, 280), outline=stamp_colour, width=6)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return conftest.answer_for("pages", buffer.getvalue(), "po.png")["pages"][0]

    assert _image(page_with((90, 90, 90))).mode in ("L", "1")
    red, green, blue = _image(page_with((200, 0, 0))).convert("RGB").getpixel((203, 200))
    assert red - max(green, blue) > 100


def test_transparency_is_drawn_on_white_not_black():
    from PIL import Image

    logo = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
    buffer = BytesIO()
    logo.save(buffer, format="PNG")
    page = conftest.answer_for("pages", buffer.getvalue(), "logo.png")["pages"][0]
    assert _image(page).convert("RGB").getpixel((10, 10)) == (255, 255, 255)


def test_each_page_is_kept_as_the_smaller_of_png_and_jpeg():
    from PIL import Image, ImageDraw

    # Colour text on white is smaller as PNG; colour photographic noise is
    # smaller as JPEG. (Both in colour: a gray page is stored as gray first.)
    text = Image.new("RGB", (800, 600), (255, 255, 255))
    ImageDraw.Draw(text).text((20, 20), "Acme Test Distributor PO TEST-0042", fill=(0, 0, 160))
    noise = Image.merge("RGB", [Image.effect_noise((800, 600), sigma) for sigma in (40, 64, 90)])
    for image, expected in ((text, "image/png"), (noise, "image/jpeg")):
        png, jpeg = BytesIO(), BytesIO()
        image.save(png, format="PNG", optimize=True)
        image.save(jpeg, format="JPEG", quality=pages.PAGE_JPEG_QUALITY)
        media_type, data = pages._encode(image)
        assert media_type == expected
        assert len(data) == min(len(png.getvalue()), len(jpeg.getvalue()))


# ── Limits ─────────────────────────────────────────────────────────────────


def test_a_page_too_large_for_the_pixel_caps_at_200_dpi_is_drawn_at_the_dpi_that_fits():
    # 100 x 100 inches: 20,000 x 20,000 px at 200 dpi, eight times the pixel cap.
    page = conftest.answer_for("pages", fb.build_pdf(["Acme Test drawing"], media_box=(7200, 7200)), "po.pdf")[
        "pages"
    ][0]
    assert page["rendered"] is True
    assert page["dpi"] < pages.PAGE_DPI
    assert page["width"] * page["height"] <= file_types.MAX_IMAGE_PIXELS
    assert max(page["width"], page["height"]) <= file_types.MAX_IMAGE_DIMENSION


def test_the_page_cap_and_password_rules_are_the_same_as_for_extraction():
    over_cap = fb.build_pdf([f"page {i}" for i in range(file_types.MAX_DOCUMENT_PAGES + 1)])
    assert conftest.answer_for("pages", over_cap, "po.pdf")["code"] == "DOC-016"
    assert conftest.answer_for("pages", fb.build_pdf_with_encrypt_trailer(), "po.pdf")["code"] == "DOC-001"
    tiff_over_cap = fb.build_tiff(pages=file_types.MAX_DOCUMENT_PAGES + 1, size=(20, 20))
    assert conftest.answer_for("pages", tiff_over_cap, "po.tif")["code"] == "DOC-016"


@pytest.mark.parametrize("path", sorted(HOSTILE.iterdir()), ids=lambda path: path.name)
def test_a_hostile_file_is_refused_or_answered_never_a_fault_of_ours(path):
    pages_answer = conftest.answer("pages", path)
    assert pages_answer["outcome"] in ("ok", "rejected"), pages_answer
    document_answer = conftest.answer("document", path)
    if document_answer["outcome"] == "rejected" and pages_answer["outcome"] == "ok":
        # Only a format with nothing to draw may be refused by one route and
        # not the other: no hostile file's pages are ever drawn.
        assert pages_answer["page_count"] == 0, (path.name, document_answer)


def test_the_answer_cap_holds_with_room_to_spare():
    assert config.PAGES_ANSWER_BUDGET_BYTES + config.PAGE_MAX_BYTES * 4 // 3 + 64 * 1024 < config.ANSWER_CAP_BYTES
