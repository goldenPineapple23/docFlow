"""
Give one buyer enough approved history for approved-example prompting
(Section 7.13; slice 5.10, D-141) -- through the real pipeline, not by
writing "approved" rows by hand.

For the Phase 5 exit criterion: "with example prompting on for a test buyer
with 10+ approved documents, the golden fixture still extracts exactly".
This makes the test buyer. Each order:

  1. is a fictional Bella's Coffee House purchase order (the Section 8.3
     golden fixture's buyer), numbered BCH-2301 upward -- never BCH-2291,
     the golden fixture itself, so the fixture can be uploaded afterwards
     and read WITH these as its examples;
  2. goes through the real worker task (`parse_and_extract`: parsing, the
     model call, buyer identification, matching, validation) -- so it costs
     one real extraction each, about $0.01;
  3. is approved with the real `approve_document` by a real user of the
     tenant, acknowledging any warning with a note that says it was seeded.

Run with the WORKER's venv (it runs the worker task in-process), from the
repo root, after migration 0025 is applied:

    apps/worker/.venv/Scripts/python.exe scripts/seed_example_history.py <tenant-id> [count]

Then, in the Console: Tenant -> Example prompting -> turn it on, and upload
docs/sample_po.txt as that tenant. Its review screen should say it was read
with 3 earlier orders, and every value should still be the golden fixture's.

All data is fictional (CLAUDE.md Section 0 rule 4). Logs nothing but IDs.
"""

from __future__ import annotations

import hashlib
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "worker"))

from app.tasks.parse_and_extract import parse_and_extract
from docflow_core.db import platform_session, tenant_session
from docflow_core.review import (
    WarningAcknowledgement,
    approve_document,
    unacknowledged_warnings,
)
from docflow_core.storage import save_file
from sqlalchemy import text

_LINES = [
    ("CF-1001", "Colombian Whole Bean 5lb", "CS", "47.50"),
    ("CF-2210", "Ethiopian Yirgacheffe 5lb", "CS", "62.00"),
    ("SY-0045", "Vanilla Syrup 750ml", "EA", "8.25"),
    ("CUP-12", "12oz Paper Cups (1000ct)", "BOX", "54.00"),
    ("LID-12", "12oz Lids (1000ct)", "BOX", "31.50"),
]
_MONTHS = ["April", "May", "June", "July", "August", "September"]


def _po(n: int) -> bytes:
    """A Bella's order laid out exactly like docs/sample_po.txt, with its own
    number, dates, quantities and note."""
    month_index = n % len(_MONTHS)
    day = 3 + (n * 2) % 24
    month_number = 4 + month_index
    picked = [_LINES[(n + k) % len(_LINES)] for k in range(3)]
    rows, total = [], Decimal(0)
    for k, (sku, desc, unit, price) in enumerate(picked):
        qty = 2 + (n * 3 + k * 5) % 19
        line_total = qty * Decimal(price)  # never a float (Section 7)
        total += line_total
        rows.append(f"{sku:<12}{desc:<33}{qty:>3}    {unit:<4}  {'$' + price:>8}   ${line_total:,.2f}")
    return (
        "PURCHASE ORDER\n\n"
        "Bella's Coffee House\n1442 Oak Street, Portland, OR 97204\norders@bellascoffee.com\n\n"
        f"PO Number: BCH-{2300 + n}\n"
        f"Date: {_MONTHS[month_index]} {day}, 2026\n"
        f"Requested Delivery: {month_number:02d}/{min(day + 7, 28):02d}/2026\n"
        "Terms: Net 30\n\n"
        "Ship To: Bella's Coffee House, 1442 Oak Street, Portland, OR 97204\n\n"
        "ITEM        DESCRIPTION                     QTY   UOM   UNIT PRICE   TOTAL\n"
        + "\n".join(rows)
        + f"\n\n                                             ORDER TOTAL:         ${total:,.2f}\n\n"
        f"Note: Seeded example order {n} -- fictional.\n"
    ).encode("utf-8")


def _approver(tenant_id: UUID) -> UUID:
    with platform_session() as session:
        row = session.execute(
            text(
                "SELECT id FROM users WHERE tenant_id = :t AND is_active AND deleted_at IS NULL "
                "AND role IN ('owner', 'admin', 'reviewer') ORDER BY created_at LIMIT 1"
            ),
            {"t": str(tenant_id)},
        ).first()
    if row is None:
        raise SystemExit("This tenant has no active owner, admin or reviewer to approve with.")
    return UUID(str(row[0]))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    tenant_id = UUID(sys.argv[1])
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    approver = _approver(tenant_id)

    for n in range(1, count + 1):
        content = _po(n)
        document_id = uuid4()
        path = save_file(tenant_id, "seed.txt", content)
        with tenant_session(tenant_id) as session:
            session.execute(
                text(
                    "INSERT INTO documents (id, tenant_id, original_filename, storage_path, source, "
                    "status, content_sha256, created_at) VALUES (:id, :t, :name, :path, 'upload', "
                    "'pending', :sha, now())"
                ),
                {
                    "id": str(document_id),
                    "t": str(tenant_id),
                    "name": f"bella-seed-{2300 + n}.txt",
                    "path": path,
                    "sha": hashlib.sha256(content).hexdigest(),
                },
            )
        parse_and_extract(str(tenant_id), str(document_id))

        with tenant_session(tenant_id) as session:
            status = session.execute(
                text("SELECT status FROM documents WHERE id = :id"), {"id": str(document_id)}
            ).scalar_one()
            if status != "needs_review":
                print(f"{document_id}: {status} -- not approved")
                continue
            acks = [
                WarningAcknowledgement(
                    warning_id=w["id"], code=w["code"], text=str(w["detail"] or w["code"]),
                    note="Seeded example history (scripts/seed_example_history.py).",
                )
                for w in unacknowledged_warnings(session, document_id)
            ]
            approve_document(session, tenant_id, document_id, user_id=approver, acknowledgements=acks)
        print(f"{document_id}: approved ({len(acks)} warning(s) acknowledged)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
