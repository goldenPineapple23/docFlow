"""
One-time script: a realistic queue to try the review screen against.

Creates a 20-item catalog, a handful of buyers, and 20 purchase orders with
the kind of variety a real week produces -- most of them fine, several with a
genuine problem to find, one resent, one revised.

**Every name here is unmistakably fake** (CLAUDE.md Section 0 rule 4 and
Section 7.10: "Seed and test data must be unmistakably fake"). Every buyer
and every product is invented for this file, and each buyer's name contains
"Test" so that no screenshot of this data can ever be mistaken for a real
customer's.

**It seeds rows rather than running the pipeline.** There is no Redis on this
machine yet, so the queue cannot run, and extraction against 20 documents
would cost real money for no benefit here -- the point is to exercise the
REVIEW SCREEN. So it writes the rows the pipeline would have written, writes
a matching document for the viewer to show, and then runs the REAL matching
and validation code over them. Every warning a reviewer sees is genuine
output.

Each document's text and its extracted values agree, EXCEPT where a defect
is introduced on purpose. Those are listed in `DEFECTS` below, so a reviewer
can be checked against a known answer.

Usage:

    python scripts/seed_demo_data.py            # add 20 orders
    python scripts/seed_demo_data.py --reset    # remove previous demo orders first

Re-running adds a fresh batch; `--reset` clears the demo tenant's documents
so the queue does not grow forever.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))

import po_formats
from docflow_core import file_types, previews
from docflow_core.db import platform_session, tenant_session
from docflow_core.matching import match_document_lines
from docflow_core.storage import save_file
from docflow_core.validation import validate_document
from sqlalchemy import text

TENANT_NAME = "Acme Test Distributor"

# A wholesale food-service catalog. Twenty items, invented.
CATALOG: list[tuple[str, str, str, str]] = [
    ("CF-1001", "Colombian Whole Bean 5lb", "CS", "47.50"),
    ("CF-2210", "Ethiopian Yirgacheffe 5lb", "CS", "62.00"),
    ("CF-3040", "Breakfast Blend Ground 2lb", "CS", "28.75"),
    ("CF-4115", "Decaf Sumatra Whole Bean 5lb", "CS", "54.00"),
    ("TEA-0220", "English Breakfast Tea 100ct", "BOX", "18.40"),
    ("TEA-0310", "Peppermint Herbal Tea 100ct", "BOX", "16.90"),
    ("SY-0045", "Vanilla Syrup 750ml", "EA", "8.25"),
    ("SY-0046", "Caramel Syrup 750ml", "EA", "8.25"),
    ("SY-0051", "Hazelnut Syrup 750ml", "EA", "8.75"),
    ("MLK-1100", "Oat Milk 32oz", "CS", "34.20"),
    ("MLK-1200", "Almond Milk 32oz", "CS", "31.80"),
    ("CUP-12X", "12oz Paper Cups 1000ct", "BOX", "54.00"),
    ("CUP-16X", "16oz Paper Cups 1000ct", "BOX", "61.50"),
    ("LID-12", "12oz Cup Lids 1000ct", "BOX", "22.30"),
    ("NAP-0500", "Beverage Napkins 5000ct", "BOX", "37.00"),
    ("STR-0100", "Paper Straws 2000ct", "BOX", "26.40"),
    ("SUG-0400", "Raw Sugar Packets 2000ct", "BOX", "42.10"),
    ("CHO-0075", "Drinking Chocolate 2lb", "EA", "19.60"),
    ("FLT-0900", "Coffee Filters 1000ct", "BOX", "15.75"),
    ("CLN-0300", "Espresso Machine Cleaner 1kg", "EA", "29.90"),
]

# Buyers. Fictional, and each says "Test" so no screenshot can be mistaken
# for a real customer.
BUYERS: list[tuple[str, str]] = [
    ("Bella's Test Coffee House", "orders@bellas.test"),
    ("Northwind Test Bakery", "purchasing@northwind.test"),
    ("Southgate Test Grocers", "ap@southgate.test"),
    ("Harborview Test Cafe", "orders@harborview.test"),
    ("Cedar Lane Test Diner", "buying@cedarlane.test"),
    ("Mill Street Test Roasters", "orders@millstreet.test"),
]

# What is deliberately wrong, and on which order. Everything else is clean.
DEFECTS = {
    3: "quantity_misread",  # a digit dropped -- line total stops multiplying out
    6: "unknown_sku",  # a SKU that is not in the catalog
    9: "header_total_wrong",  # the printed order total does not sum
    12: "quantity_misread",
    14: "unknown_sku",
    17: "low_confidence",  # a poor scan -- several fields below threshold
    19: "uom_swapped",  # ordered in EA where the catalog says CS
}

PO_TEMPLATE = """PURCHASE ORDER

{buyer}
{address}
{email}

PO Number: {po_number}
Date: {order_date}
Requested Delivery: {delivery_date}
Terms: {terms}

Ship To: {buyer}, {address}

ITEM        DESCRIPTION                          QTY   UOM   UNIT PRICE      TOTAL
{lines}

{total_label:>68} {order_total:>10}

{note}
"""

ADDRESSES = [
    "1442 Oak Street, Portland, OR 97204",
    "88 Harbor Road, Astoria, OR 97103",
    "512 Cedar Lane, Eugene, OR 97401",
    "7 Mill Street, Bend, OR 97701",
    "301 Southgate Avenue, Salem, OR 97301",
    "24 Northwind Way, Hillsboro, OR 97124",
]

TERMS = ["Net 30", "Net 15", "Net 45", "Due on receipt"]


def _tenant_id() -> UUID:
    with platform_session() as session:
        row = session.execute(
            text("SELECT id FROM tenants WHERE name = :name AND deleted_at IS NULL"),
            {"name": TENANT_NAME},
        ).mappings().first()
    if row is None:
        sys.exit(
            f"No tenant named {TENANT_NAME!r}. Run scripts/seed_review_walkthrough.py first -- "
            "it creates the tenant and the reviewer login."
        )
    return UUID(str(row["id"]))


def _ensure_catalog(tenant_id: UUID) -> None:
    with platform_session() as session:
        for sku, description, uom, _price in CATALOG:
            session.execute(
                text(
                    "INSERT INTO items (id, tenant_id, sku, description, unit_of_measure, "
                    "created_at, updated_at) "
                    "VALUES (:id, :tenant_id, :sku, :description, :uom, now(), now()) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "sku": sku,
                    "description": description,
                    "uom": uom,
                },
            )
    print(f"Catalog: {len(CATALOG)} items.")


def _reset(tenant_id: UUID) -> None:
    """Remove this tenant's documents. Demo data only -- never a customer's."""
    tid = str(tenant_id)
    with platform_session() as session:
        for table in (
            "document_snapshots",
            "document_warnings",
            "review_actions",
            "document_lines",
            "document_headers",
        ):
            session.execute(text(f"DELETE FROM {table} WHERE tenant_id = :tid"), {"tid": tid})
        session.execute(
            text(
                "UPDATE documents SET duplicate_of_document_id = NULL, "
                "change_order_of_document_id = NULL WHERE tenant_id = :tid"
            ),
            {"tid": tid},
        )
        session.execute(text("DELETE FROM documents WHERE tenant_id = :tid"), {"tid": tid})
    print("Cleared the demo tenant's existing orders.")


def _ext(filename: str) -> str:
    return filename[filename.rindex(".") :].lower() if "." in filename else ""


def _render_line(sku: str, description: str, qty: str, uom: str, price: str, total: str) -> str:
    return f"{sku:<11} {description:<36} {qty:>4}   {uom:<5} {'$' + price:>10} {'$' + total:>10}"


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _build_order(index: int, rng: random.Random) -> dict:
    """One order: the text a buyer sent, and the values extraction produced."""
    buyer, email = BUYERS[index % len(BUYERS)]
    address = ADDRESSES[index % len(ADDRESSES)]
    defect = DEFECTS.get(index)

    received = datetime.now(timezone.utc).date() - timedelta(days=rng.randint(0, 9))
    order_date = received - timedelta(days=rng.randint(0, 2))
    delivery = received + timedelta(days=rng.randint(3, 14))

    chosen = rng.sample(CATALOG, rng.randint(2, 5))
    printed_rows: list[str] = []
    structured_rows: list[dict] = []
    extracted_lines: list[dict] = []
    printed_total = Decimal("0.00")

    for line_number, (sku, description, uom, price) in enumerate(chosen, start=1):
        qty = rng.choice([2, 4, 6, 10, 12, 20, 24])
        unit_price = Decimal(price)
        line_total = (unit_price * qty).quantize(Decimal("0.01"))
        printed_total += line_total

        printed_sku, printed_uom = sku, uom
        # What the document says is always the truth; the defect changes what
        # EXTRACTION recorded, which is what a reviewer has to catch.
        extracted_qty, extracted_sku, extracted_uom = str(qty), sku, uom
        confidence = Decimal(str(rng.choice([0.93, 0.95, 0.96, 0.97, 0.98])))

        if defect == "quantity_misread" and line_number == 2:
            extracted_qty = str(qty // 10 if qty >= 10 else 1)
        if defect == "unknown_sku" and line_number == 1:
            # The buyer used their own part number, which is not in the catalog.
            printed_sku = extracted_sku = f"{sku.split('-')[0]}-{rng.randint(700, 799)}"
        if defect == "uom_swapped" and line_number == 1:
            extracted_uom = "EA" if uom != "EA" else "CS"
            printed_uom = extracted_uom
        if defect == "low_confidence":
            confidence = Decimal(str(rng.choice([0.55, 0.62, 0.68, 0.71])))

        printed_rows.append(
            _render_line(printed_sku, description, str(qty), printed_uom, price, _money(line_total))
        )
        structured_rows.append(
            {
                "sku": printed_sku,
                "description": description,
                "qty": str(qty),
                "uom": printed_uom,
                "price": price,
                "total": _money(line_total),
            }
        )
        extracted_lines.append(
            {
                "line_number": line_number,
                "sku": extracted_sku,
                "description": description,
                "unit": extracted_uom,
                "quantity": extracted_qty,
                "unit_price": price,
                "line_total": _money(line_total),
                "confidence": str(confidence),
            }
        )

    extracted_total = printed_total
    if defect == "header_total_wrong":
        # The document's own total is wrong -- a real thing buyers do.
        extracted_total = printed_total + Decimal("120.00")
        printed_total = extracted_total

    terms = rng.choice(TERMS)
    po_number = f"{buyer.split()[0][:3].upper()}-{4000 + index}"
    # Most buyers' systems print the currency; a minority send a bare "$".
    # Seeding every order as symbol-only made every single one carry a
    # low-confidence currency and an "inferred from a symbol" check, which
    # made the whole queue look uncertain and taught a reviewer to ignore
    # the flag -- the precise failure D-073 warns about.
    currency_stated = index % 10 not in (3, 7)
    header_confidence = {
        "po_number": 0.98,
        "order_date": 0.95,
        "requested_delivery_date": 0.94,
        "buyer_name": 0.96,
        "buyer_contact_email": 0.93,
        "ship_to_address": 0.95,
        "payment_terms": 0.94,
        "order_total": 0.96,
        # Capped at 0.6 only when the document never says which currency it
        # is in (Section 7.1).
        "currency": 0.97 if currency_stated else 0.6,
        "notes": 0.9,
    }
    if defect == "low_confidence":
        # A poor scan garbles SOME fields, not every one. Capping all ten
        # produced sixteen checks on one order and taught a reviewer to tick
        # through them -- the failure D-073 warns about. These are the ones a
        # soft, skewed scan actually costs you: the reference number, the
        # total, and the handwritten-looking date.
        header_confidence = {
            **header_confidence,
            "po_number": 0.64,
            "order_total": 0.58,
            "order_date": 0.71,
        }

    document_text = PO_TEMPLATE.format(
        buyer=buyer,
        address=address,
        email=email,
        po_number=po_number,
        order_date=order_date.strftime("%B %d, %Y"),
        delivery_date=delivery.strftime("%m/%d/%Y"),
        terms=terms,
        lines="\n".join(printed_rows),
        total_label="ORDER TOTAL (USD):" if currency_stated else "ORDER TOTAL:",
        order_total="$" + _money(printed_total),
        note=rng.choice(
            [
                "Note: Please deliver before 10am. Back door access only.",
                "Note: Call ahead on arrival.",
                "Note: Substitutions not accepted.",
                "",
            ]
        ),
    )

    fmt = po_formats.FORMATS[index % len(po_formats.FORMATS)]
    filename, content = po_formats.render(
        fmt,
        text=document_text,
        po_number=po_number,
        rows=structured_rows,
        header={
            "buyer": buyer,
            "order_date": order_date.isoformat(),
            "delivery_date": delivery.isoformat(),
            "order_total": _money(extracted_total),
        },
        sender=email,
        buyer=buyer,
        degraded=defect == "low_confidence",
    )

    return {
        # The document and the extracted values must agree on everything
        # except a declared defect. An earlier version printed a random term
        # on the document and hardcoded "Net 30" in the data, so three
        # orders in four carried an undeclared mismatch -- which teaches a
        # reviewer to distrust the thing they are checking against.
        "payment_terms": terms,
        "currency_inferred": not currency_stated,
        "format": fmt,
        "filename": filename,
        "content": content,
        "po_number": po_number,
        "buyer": buyer,
        "email": email,
        "address": address,
        "order_date": order_date.isoformat(),
        "delivery_date": delivery.isoformat(),
        "received": received,
        "order_total": _money(extracted_total),
        "header_confidence": header_confidence,
        "lines": extracted_lines,
        "text": document_text,
        "defect": defect,
    }


def _insert(tenant_id: UUID, order: dict, *, content_sha: str | None = None) -> UUID:
    content = order["content"]
    storage_path = save_file(tenant_id, order["filename"], content)
    document_id = uuid4()

    # A viewable rendering for the formats no browser shows (D-092). Built
    # HERE, in a script, never in the web process -- Section 7.11 keeps
    # parsing out of the API, and decoding a TIFF or reading a DOCX is
    # parsing. The worker does the same thing for real intake.
    preview_path = preview_media_type = preview_kind = None
    detected = file_types.detect_file_type(content, _ext(order["filename"]))
    if detected is not None and previews.needs_preview(detected.name):
        preview = previews.build_preview(content, detected.name)
        if preview is not None:
            suffix = ".png" if preview.media_type == "image/png" else (
                ".jpg" if preview.media_type == "image/jpeg" else ".txt"
            )
            preview_path = save_file(tenant_id, f"{order['po_number']}-preview{suffix}", preview.content)
            preview_media_type = preview.media_type
            preview_kind = preview.kind
    sha = content_sha or hashlib.sha256(content).hexdigest()
    overall = min(Decimal(str(v)) for v in order["header_confidence"].values())

    with platform_session() as session:
        session.execute(
            text(
                """
                INSERT INTO documents
                    (id, tenant_id, original_filename, storage_path, source, status,
                     content_sha256, injection_suspected, overall_confidence, created_at,
                     preview_storage_path, preview_media_type, preview_kind)
                VALUES
                    (:id, :tenant_id, :filename, :storage_path, :source, 'needs_review',
                     :sha, false, :confidence, :created_at,
                     :preview_path, :preview_media_type, :preview_kind)
                """
            ),
            {
                "id": str(document_id),
                "tenant_id": str(tenant_id),
                "filename": order["filename"],
                "storage_path": storage_path,
                "source": "email" if order["format"] == "eml" else "upload",
                "sha": sha,
                "confidence": str(overall),
                "created_at": order["received"],
                "preview_path": preview_path,
                "preview_media_type": preview_media_type,
                "preview_kind": preview_kind,
            },
        )
        session.execute(
            text(
                """
                INSERT INTO document_headers
                    (document_id, tenant_id, po_number, order_date, requested_delivery_date,
                     buyer_name, buyer_contact_email, ship_to_address, payment_terms,
                     order_total, currency, notes, header_confidence, currency_inferred,
                     created_at, updated_at)
                VALUES
                    (:document_id, :tenant_id, :po_number, :order_date, :delivery_date,
                     :buyer, :email, :address, :payment_terms,
                     :order_total, 'USD', NULL, :header_confidence, :currency_inferred,
                     now(), now())
                """
            ),
            {
                "document_id": str(document_id),
                "tenant_id": str(tenant_id),
                "po_number": order["po_number"],
                "order_date": order["order_date"],
                "delivery_date": order["delivery_date"],
                "buyer": order["buyer"],
                "email": order["email"],
                "address": order["address"],
                "order_total": order["order_total"],
                "header_confidence": json.dumps(order["header_confidence"]),
                "currency_inferred": order["currency_inferred"],
                "payment_terms": order["payment_terms"],
            },
        )
        for item in order["lines"]:
            session.execute(
                text(
                    """
                    INSERT INTO document_lines
                        (id, document_id, tenant_id, line_number, sku, description, unit,
                         quantity, unit_price, line_total, confidence, uom_mismatch, created_at)
                    VALUES
                        (:id, :document_id, :tenant_id, :line_number, :sku, :description, :unit,
                         :quantity, :unit_price, :line_total, :confidence, false, now())
                    """
                ),
                {
                    "id": str(uuid4()),
                    "document_id": str(document_id),
                    "tenant_id": str(tenant_id),
                    **item,
                },
            )

    with tenant_session(tenant_id) as session:
        match_document_lines(session, tenant_id, document_id)
    with tenant_session(tenant_id) as session:
        validate_document(session, tenant_id, document_id)

    return document_id


def main() -> None:
    reset = "--reset" in sys.argv
    tenant_id = _tenant_id()
    if reset:
        _reset(tenant_id)
    _ensure_catalog(tenant_id)

    # Seeded, so a re-run without --reset produces a different-looking batch
    # but the same defect pattern, and a run with --reset is reproducible.
    rng = random.Random(20260917)

    created = []
    for index in range(1, 21):
        order = _build_order(index, rng)
        document_id = _insert(tenant_id, order)
        created.append((order, document_id))

    # One resend: identical bytes arriving twice (Section 7.8).
    resend_source = created[1][0]
    resend_sha = hashlib.sha256(resend_source["text"].encode("utf-8")).hexdigest()
    _insert(tenant_id, resend_source, content_sha=resend_sha)

    # One revision: the same PO number, different contents.
    revision = dict(created[4][0])
    revision["lines"] = [dict(line) for line in revision["lines"]]
    revision["lines"][0]["quantity"] = str(int(revision["lines"][0]["quantity"]) + 6)
    revision["text"] = revision["text"].replace("PURCHASE ORDER", "PURCHASE ORDER (REVISED)", 1)
    _insert(tenant_id, revision)

    # Re-run detection now that the related documents exist.
    from docflow_core.duplicates import detect_document_relationships

    with platform_session() as session:
        ids = [
            UUID(str(r["id"]))
            for r in session.execute(
                text(
                    "SELECT id FROM documents WHERE tenant_id = :tid AND deleted_at IS NULL "
                    "ORDER BY created_at, id"
                ),
                {"tid": str(tenant_id)},
            ).mappings()
        ]
    for document_id in ids:
        with tenant_session(tenant_id) as session:
            detect_document_relationships(session, tenant_id, document_id)
            validate_document(session, tenant_id, document_id)

    print()
    print("=" * 70)
    print(f"  Seeded {len(created)} orders, plus one resend and one revision.")
    print()
    print("  Deliberately wrong, so a reviewer has something to find:")
    for order, document_id in created:
        if order["defect"]:
            print(f"    {order['po_number']:<12} {order['defect']}")
    print()
    spread: dict[str, int] = {}
    for order, _ in created:
        spread[order["format"]] = spread.get(order["format"], 0) + 1
    print("  Formats, as a buyer's inbox actually looks:")
    print("    " + ", ".join(f"{fmt} x{n}" for fmt, n in sorted(spread.items())))
    print()
    print("  Everything else reconciles and should approve with only the")
    print("  currency check to acknowledge.")
    print()
    print("  Open http://localhost:3000/review")
    print("=" * 70)


if __name__ == "__main__":
    main()
