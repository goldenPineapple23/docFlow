"""
Render one purchase order into the formats buyers actually send.

CLAUDE.md Section 7.11: "Buyers send POs in whatever their system produces; a
format DocFlow rejects is a PO the customer has to key by hand, which is the
exact failure the product exists to prevent." A demo queue where every
document is a .txt file tests none of that.

Each function takes the plain-text rendering of an order and returns
`(filename, bytes)`. Only the standard library, Pillow, python-docx and
openpyxl are used -- all already dependencies of the parsing worker, so this
adds nothing to the lockfile.

No PDF writer is installed, and a scanned PDF is the more realistic artefact
anyway: `Image.save(..., "PDF")` produces exactly that, a page of pixels with
no text layer, which is what a desktop scanner emits.

All content is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from email.message import EmailMessage

from docx import Document as DocxDocument
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont

# The formats this module can produce, in the rough proportions a
# distributor's inbox actually sees: mostly PDF and email, a steady trickle of
# Excel and Word, and a stubborn minority of scans and phone photos.
FORMATS = ["pdf", "eml", "xlsx", "docx", "txt", "png", "tiff", "csv", "jpg"]


def _font() -> ImageFont.ImageFont:
    """A monospace face if one can be found, so columns line up in a scan."""
    for candidate in ("consola.ttf", "cour.ttf", "DejaVuSansMono.ttf"):
        try:
            return ImageFont.truetype(candidate, 15)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_image(text: str, *, jitter: bool = False) -> Image.Image:
    """
    The order as a page of pixels -- a scan or a phone photo.

    `jitter` tilts and greys it very slightly, the way a photo taken over a
    counter looks. It is not noise for its own sake: a reviewer should meet a
    document that is a little harder to read, because that is the document
    this product exists for.
    """
    font = _font()
    lines = text.split("\n")
    width, height = 1240, max(1754, 40 + len(lines) * 22)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    y = 40
    for line in lines:
        draw.text((60, y), line, fill=(20, 20, 20), font=font)
        y += 22

    if jitter:
        image = image.rotate(-0.4, expand=False, fillcolor="white")
        image = image.convert("L").point(lambda p: min(255, int(p * 1.04) + 4)).convert("RGB")
    return image


def as_txt(text: str, po_number: str) -> tuple[str, bytes]:
    return f"{po_number}.txt", text.encode("utf-8")


def as_csv(text: str, po_number: str, rows: list[dict]) -> tuple[str, bytes]:
    """A PO exported straight out of the buyer's system."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["PO Number", po_number])
    writer.writerow([])
    writer.writerow(["Item", "Description", "Qty", "UOM", "Unit Price", "Line Total"])
    for row in rows:
        writer.writerow(
            [row["sku"], row["description"], row["qty"], row["uom"], row["price"], row["total"]]
        )
    return f"{po_number}.csv", buf.getvalue().encode("utf-8")


def as_eml(text: str, po_number: str, sender: str, buyer: str) -> tuple[str, bytes]:
    """The PO typed into the body of an email, which plenty of buyers do."""
    message = EmailMessage()
    message["Subject"] = f"Purchase Order {po_number}"
    message["From"] = f"{buyer} <{sender}>"
    message["To"] = "orders@acme-test-distributor.test"
    message["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    message.set_content(
        f"Hi,\n\nPlease see our order below. Confirm when you can.\n\nThanks\n\n{text}"
    )
    return f"{po_number}.eml", message.as_bytes()


def as_docx(text: str, po_number: str) -> tuple[str, bytes]:
    document = DocxDocument()
    for line in text.split("\n"):
        if line.strip() == "PURCHASE ORDER":
            document.add_heading(line.strip(), level=1)
        else:
            paragraph = document.add_paragraph(line)
            paragraph.paragraph_format.space_after = None
    buf = io.BytesIO()
    document.save(buf)
    return f"{po_number}.docx", buf.getvalue()


def as_xlsx(text: str, po_number: str, rows: list[dict], header: dict) -> tuple[str, bytes]:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Purchase Order"
    sheet["A1"] = "PURCHASE ORDER"
    sheet["A3"], sheet["B3"] = "PO Number", po_number
    sheet["A4"], sheet["B4"] = "Buyer", header["buyer"]
    sheet["A5"], sheet["B5"] = "Date", header["order_date"]
    sheet["A6"], sheet["B6"] = "Requested Delivery", header["delivery_date"]

    sheet.append([])
    sheet.append(["Item", "Description", "Qty", "UOM", "Unit Price", "Line Total"])
    for row in rows:
        sheet.append(
            [row["sku"], row["description"], int(row["qty"]), row["uom"], row["price"], row["total"]]
        )
    sheet.append([])
    sheet.append(["", "", "", "", "ORDER TOTAL", header["order_total"]])

    buf = io.BytesIO()
    workbook.save(buf)
    return f"{po_number}.xlsx", buf.getvalue()


def as_pdf(text: str, po_number: str) -> tuple[str, bytes]:
    """A scanned PDF: a page of pixels, no text layer. What a scanner emits."""
    image = _render_image(text)
    buf = io.BytesIO()
    image.save(buf, format="PDF", resolution=150.0)
    return f"{po_number}.pdf", buf.getvalue()


def as_png(text: str, po_number: str) -> tuple[str, bytes]:
    buf = io.BytesIO()
    _render_image(text).save(buf, format="PNG")
    return f"{po_number}.png", buf.getvalue()


def as_jpg(text: str, po_number: str) -> tuple[str, bytes]:
    """A phone photo of a paper order, slightly off-square."""
    buf = io.BytesIO()
    _render_image(text, jitter=True).save(buf, format="JPEG", quality=72)
    return f"{po_number}.jpg", buf.getvalue()


def as_tiff(text: str, po_number: str) -> tuple[str, bytes]:
    """Fax output. Bitonal, which is what a fax actually is."""
    buf = io.BytesIO()
    _render_image(text).convert("1").save(buf, format="TIFF", compression="group4")
    return f"{po_number}.tif", buf.getvalue()


def render(
    fmt: str, *, text: str, po_number: str, rows: list[dict], header: dict, sender: str, buyer: str
) -> tuple[str, bytes]:
    if fmt == "txt":
        return as_txt(text, po_number)
    if fmt == "csv":
        return as_csv(text, po_number, rows)
    if fmt == "eml":
        return as_eml(text, po_number, sender, buyer)
    if fmt == "docx":
        return as_docx(text, po_number)
    if fmt == "xlsx":
        return as_xlsx(text, po_number, rows, header)
    if fmt == "pdf":
        return as_pdf(text, po_number)
    if fmt == "png":
        return as_png(text, po_number)
    if fmt == "jpg":
        return as_jpg(text, po_number)
    if fmt == "tiff":
        return as_tiff(text, po_number)
    raise ValueError(f"unknown format {fmt!r}")
