"""Make a large image-only ("scanned") PDF for the worker memory reading.

Fictional throughout (CLAUDE.md Section 0 rule 4): page 1 is a purchase order
from an "Acme Test" buyer, the rest are filler terms pages. Scanner-like noise
is added so each page compresses like a real scan.

    python make_big_scanned_pdf.py <out.pdf> <pages> <target_mb>
"""

from __future__ import annotations

import io
import random
import sys

from PIL import Image, ImageDraw, ImageFont

OUT, PAGES, TARGET_MB = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
W, H = 1654, 2339  # A4 at 200 dpi
random.seed(2026100800 + 2)
big, mid = ImageFont.load_default(size=44), ImageFont.load_default(size=30)

PO_LINES = [
    "PURCHASE ORDER",
    "",
    "Acme Test Wholesale Grocers",
    "400 Test Parkway, Corvallis, OR 97330",
    "orders@acmetestgrocers.example",
    "",
    "PO Number: MEM-1008-02",
    "Date: October 8, 2026",
    "Requested Delivery: 10/16/2026",
    "Terms: Net 30",
    "",
    "Ship To: Acme Test Wholesale Grocers, 400 Test Parkway, Corvallis, OR 97330",
    "",
    "ITEM       DESCRIPTION                    QTY  UOM  UNIT PRICE     TOTAL",
    "CF-1001    Colombian Whole Bean 5lb         8  CS       $47.50   $380.00",
    "CF-2210    Ethiopian Yirgacheffe 5lb        5  CS       $62.00   $310.00",
    "SY-0045    Vanilla Syrup 750ml             12  EA        $8.25    $99.00",
    "CUP-12     12oz Paper Cups (1000ct)         3  BOX      $54.00   $162.00",
    "",
    "                                         ORDER TOTAL:          $951.00",
    "",
    "Standard purchasing terms follow on pages 2 onward.",
]
TERMS = [
    "This page is part of the buyer's standard purchasing terms. It adds no items,",
    "quantities, prices or dates to the purchase order on page 1.",
    "Goods are received Monday to Friday. Deliveries are checked against the order.",
    "Shortages and damage are reported to the supplier within five working days.",
    "Invoices quote the purchase order number shown on page 1 of this document.",
    "Pallets are standard size and are exchanged or collected by arrangement.",
]


def page(n: int) -> Image.Image:
    im = Image.new("L", (W, H), 246)
    d = ImageDraw.Draw(im)
    if n == 1:
        y = 150
        for i, line in enumerate(PO_LINES):
            d.text((120, y), line, fill=20, font=big if i == 0 else mid)
            y += 70 if i == 0 else 48
    else:
        d.text((120, 150), f"STANDARD PURCHASING TERMS  -  page {n} of {PAGES}", fill=20, font=mid)
        y = 260
        for k in range(30):
            d.text((120, y), f"{n}.{k + 1}  {TERMS[(n + k) % len(TERMS)]}", fill=30, font=mid)
            y += 62
    noise = Image.effect_noise((W, H), NOISE)
    return Image.blend(im, noise, 0.22).convert("L")


def build(quality: int) -> bytes:
    pages = [page(n) for n in range(1, PAGES + 1)]
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:], resolution=200, quality=quality)
    return buf.getvalue()


NOISE = 70
best = None
for q in (64, 66, 68, 69, 70):
    data = build(q)
    mb = len(data) / 1048576
    print(f"quality {q}: {mb:.2f} MB")
    if mb <= TARGET_MB:
        best = (q, data)
    else:
        break
if best is None:
    sys.exit("even the lowest quality is over the target")
with open(OUT, "wb") as fh:
    fh.write(best[1])
print(f"wrote {OUT}: quality {best[0]}, {len(best[1])} bytes = {len(best[1]) / 1048576:.2f} MB, {PAGES} pages")
