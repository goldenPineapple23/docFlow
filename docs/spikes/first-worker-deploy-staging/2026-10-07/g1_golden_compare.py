"""Read-only: the golden fixture's stored extraction against Section 8.3's values
(the same values as apps/api/tests/test_golden_fixture.py). No model call."""

from __future__ import annotations

import json
import sys
from decimal import Decimal

from docflow_core.db import platform_session, use_own_login
from sqlalchemy import text

DOC = sys.argv[1]
HEADER = {
    "po_number": "BCH-2291",
    "order_date": "2026-03-14",
    "requested_delivery_date": "2026-03-21",
    "buyer_name": "Acme's Test Coffee House",
    "buyer_contact_email": "orders@acmetestcoffee.example",
    "ship_to_address": "Acme's Test Coffee House, 1442 Test Street, Portland, OR 97204",
    "payment_terms": "Net 30",
    "order_total": Decimal("1356.00"),
    "currency": "USD",
    "notes": "Please deliver before 10am. Back door access only.",
}
LINES = {
    "CF-1001": ("Colombian Whole Bean 5lb", "12", "CS", "47.50", "570.00"),
    "CF-2210": ("Ethiopian Yirgacheffe 5lb", "6", "CS", "62.00", "372.00"),
    "SY-0045": ("Vanilla Syrup 750ml", "24", "EA", "8.25", "198.00"),
    "CUP-12": ("12oz Paper Cups (1000ct)", "4", "BOX", "54.00", "216.00"),
}
fails = 0


def check(where: str, name: str, got, want) -> None:
    global fails
    if isinstance(want, Decimal):
        ok = got is not None and Decimal(str(got)) == want
    else:
        ok = got == want
    if not ok:
        fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {where}.{name}" + ("" if ok else f"   got {got!r}   want {want!r}"))


def compare(where: str, header: dict, lines: list[dict], conf: dict | None,
            injection, inferred) -> None:
    print(f"\n-- {where}")
    missing = [k for k in HEADER if k not in header]
    for k, want in HEADER.items():
        if k in missing:
            global fails
            fails += 1
            print(f"  FAIL  {where}.{k}   no such field here")
        else:
            check(where, k, header[k], want)
    check(where, "injection_suspected", injection, False)
    check(where, "currency_inferred", inferred, True)
    cur = (conf or {}).get("currency")
    ok = cur is not None and float(cur) <= 0.6
    if not ok:
        fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {where}.confidence[currency] <= 0.6   got {cur!r}")
    check(where, "line count", len(lines), 4)
    total = Decimal("0")
    for line in lines:
        sku = line.get("sku")
        if sku not in LINES:
            fails += 1
            print(f"  FAIL  {where}.line sku {sku!r} not expected")
            continue
        d, q, u, p, t = LINES[sku]
        check(where, f"{sku}.description", line.get("description"), d)
        check(where, f"{sku}.quantity", line.get("quantity"), Decimal(q))
        check(where, f"{sku}.unit", line.get("unit"), u)
        check(where, f"{sku}.unit_price", line.get("unit_price"), Decimal(p))
        check(where, f"{sku}.line_total", line.get("line_total"), Decimal(t))
        if line.get("line_total") is not None:
            total += Decimal(str(line["line_total"]))
    check(where, "sum of line totals", total, Decimal("1356.00"))


use_own_login("admin")
with platform_session() as s:
    doc = s.execute(text("SELECT to_jsonb(d) FROM documents d WHERE id = :i"), {"i": DOC}).scalar_one()
    hdr = s.execute(text("SELECT to_jsonb(h) FROM document_headers h WHERE document_id = :i"), {"i": DOC}).scalar_one()
    lns = [r[0] for r in s.execute(
        text("SELECT to_jsonb(l) FROM document_lines l WHERE document_id = :i ORDER BY line_number"), {"i": DOC})]
    warns = [r[0] for r in s.execute(
        text("SELECT to_jsonb(w) FROM document_warnings w WHERE document_id = :i"), {"i": DOC})]

print(f"document {DOC} status={doc['status']}")
compare("stored rows", hdr, lns, hdr.get("header_confidence"),
        doc.get("injection_suspected"), hdr.get("currency_inferred"))

raw = doc.get("raw_json") or {}
print(f"\nraw_json top-level keys: {sorted(raw)}")
rh = raw.get("header") or {}
print(f"raw_json header keys: {sorted(rh)}")
print("raw header sample shapes: " + json.dumps({k: type(v).__name__ for k, v in list(rh.items())[:4]}))

print(f"\nchecks on the document ({len(warns)}):")
for w in warns:
    keys = {k: w[k] for k in w if k in ("rule", "code", "warning_type", "type", "severity", "field", "line_number", "message")}
    print("  " + json.dumps(keys, default=str))

print(f"\nRESULT: {'all match' if fails == 0 else str(fails) + ' difference(s)'}")
