"""G3 busy hour (RUNBOOK 9.7 step 9): 20 short text orders, one every 3 minutes,
through the Fly API (over `fly proxy` on port 18000) into the G1 test tenant.

    python g3_busy_hour.py --list                 # print the 20 orders' summary; touches nothing
    python g3_busy_hour.py --run <result.json>    # the timed hour

Every order is fictional (CLAUDE.md Section 0 rule 4). Prints ids, statuses,
times, token counts and cost only -- never a document's content.
"""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

API = "http://localhost:18000"
API_APP = "docflow-api-staging"
API_MACHINE = "8e7d7eb762d918"
TENANT_ID = "40594983-03ff-47b1-ac40-f636488b8de7"  # "Acme Test G1 2a60cc"
ORDERS = 20
SLOT_SECONDS = 180
POLL_TIMEOUT_S = 150
SPEND_STOP_USD = Decimal("1.00")
TERMINAL = {"needs_review", "failed", "quarantined", "dead_letter", "rejected"}

BUYERS = [
    ("Acme Test Bakery", "210 Test Avenue, Salem, OR 97301", "orders@acmetestbakery.example"),
    ("Acme Test Diner", "88 Test Road, Eugene, OR 97401", "purchasing@acmetestdiner.example"),
    ("Acme Test Roasters", "5 Test Lane, Bend, OR 97701", "buy@acmetestroasters.example"),
    ("Acme Test Bistro", "730 Test Boulevard, Medford, OR 97501", "orders@acmetestbistro.example"),
    ("Acme Test Tea Room", "19 Test Court, Astoria, OR 97103", "orders@acmetesttearoom.example"),
]
# The four items of the catalog imported in G2.
ITEMS = [
    ("CF-1001", "Colombian Whole Bean 5lb", "CS", Decimal("47.50")),
    ("CF-2210", "Ethiopian Yirgacheffe 5lb", "CS", Decimal("62.00")),
    ("SY-0045", "Vanilla Syrup 750ml", "EA", Decimal("8.25")),
    ("CUP-12", "12oz Paper Cups (1000ct)", "BOX", Decimal("54.00")),
]


def build_order(i: int, today: date) -> tuple[str, str, Decimal]:
    """Order i (0-based): file name, text, total. Fixed by i, so --list shows
    exactly what --run sends on the same day."""
    name, address, email = BUYERS[i % len(BUYERS)]
    po = f"G3-{today:%m%d}-{i + 1:02d}"
    deliver = today + timedelta(days=5 + i % 6)
    chosen = [ITEMS[(i + k) % len(ITEMS)] for k in range(2 + i % 3)]
    rows, total = [], Decimal("0")
    for k, (sku, desc, uom, price) in enumerate(chosen):
        qty = 2 + (i * 3 + k * 5) % 11
        line = price * qty
        total += line
        rows.append(f"{sku:<11} {desc:<30} {qty:>4}    {uom:<4} {'$' + format(price, '.2f'):>9} {'$' + format(line, ',.2f'):>11}")
    text = "\n".join(
        [
            "PURCHASE ORDER",
            "",
            name,
            address,
            email,
            "",
            f"PO Number: {po}",
            f"Date: {today:%B %d, %Y}",
            f"Requested Delivery: {deliver:%m/%d/%Y}",
            "Terms: Net 30",
            "",
            f"Ship To: {name}, {address}",
            "",
            "ITEM        DESCRIPTION                     QTY   UOM   UNIT PRICE   TOTAL",
            *rows,
            "",
            f"                                             ORDER TOTAL:  {'$' + format(total, ',.2f'):>15}",
            "",
        ]
    )
    return f"{po}.txt", text, total


def now() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3] + "Z"


def list_orders() -> None:
    today = datetime.now(UTC).date()
    grand = Decimal("0")
    for i in range(ORDERS):
        fname, text, total = build_order(i, today)
        grand += total
        print(f"{i + 1:>2}  +{i * SLOT_SECONDS // 60:>2} min  {fname:<16} {BUYERS[i % len(BUYERS)][0]:<20} "
              f"{2 + i % 3} line(s)  total ${total:,.2f}  {len(text.encode())} bytes")
    print(f"{ORDERS} orders, one every {SLOT_SECONDS // 60} minutes; order totals sum to ${grand:,.2f}")


def run(out_path: str) -> None:
    import httpx
    from docflow_core.config import get_settings
    from docflow_core.db import platform_session, use_own_login
    from sqlalchemy import text as sql

    use_own_login("admin")
    s = get_settings()
    fly = shutil.which("fly") or shutil.which("flyctl")
    if not fly:
        sys.exit("fly is not on PATH")

    def healthz_ok() -> bool:
        try:
            return httpx.get(f"{API}/healthz", timeout=4).status_code == 200
        except httpx.HTTPError:
            return False

    def ensure_api_up() -> float | None:
        """None if it was already answering; else seconds from the start
        command to the first 200 from /healthz."""
        if healthz_ok():
            return None
        t = time.monotonic()
        subprocess.run([fly, "machine", "start", API_MACHINE, "--app", API_APP],
                       check=True, capture_output=True, timeout=90)
        while not healthz_ok():
            if time.monotonic() - t > 90:
                sys.exit("the API did not answer within 90 s of being started; stopping")
            time.sleep(0.25)
        return round(time.monotonic() - t, 2)

    with platform_session() as session:
        row = session.execute(
            sql("SELECT email, auth_user_id FROM users WHERE tenant_id = :t AND role = 'owner'"),
            {"t": TENANT_ID},
        ).one()
    email, auth_user_id = row[0], str(row[1])
    password = "DocFlow-g3-" + secrets.token_urlsafe(16)
    admin = {"apikey": s.supabase_service_role_key, "Authorization": f"Bearer {s.supabase_service_role_key}"}
    httpx.put(f"{s.supabase_url}/auth/v1/admin/users/{auth_user_id}", headers=admin,
              json={"password": password}, timeout=30).raise_for_status()

    def bearer() -> dict[str, str]:
        r = httpx.post(f"{s.supabase_url}/auth/v1/token?grant_type=password",
                       headers={"apikey": s.supabase_anon_key},
                       json={"email": email, "password": password}, timeout=30)
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    today = datetime.now(UTC).date()
    record: dict = {"tenant_id": TENANT_ID, "started": now(), "orders": []}
    spent = Decimal("0")
    t0 = time.monotonic()
    print(f"busy hour starts {record['started']}")

    def save() -> None:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, default=str)

    for i in range(ORDERS):
        wait = t0 + i * SLOT_SECONDS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        fname, text, _total = build_order(i, today)
        cold = ensure_api_up()
        t_up0 = time.monotonic()
        up = httpx.post(f"{API}/documents/upload", headers=bearer(),
                        files={"file": (fname, text.encode(), "text/plain")},
                        data={"batch_size": "1"}, timeout=120)
        entry: dict = {"n": i + 1, "file": fname, "at": now(), "api_cold_start_s": cold, "http": up.status_code}
        if up.status_code != 200:
            detail = up.json().get("detail") if "json" in up.headers.get("content-type", "") else None
            entry["code"] = detail.get("code") if isinstance(detail, dict) else None
            record["orders"].append(entry)
            save()
            print(f"{i + 1:>2} {now()} upload HTTP {up.status_code} code {entry['code']}; stopping")
            sys.exit(2)
        doc_id = up.json()["document_id"]
        status = None
        while True:
            with platform_session() as session:
                status = session.execute(sql("SELECT status FROM documents WHERE id = :i"), {"i": doc_id}).scalar_one()
            elapsed = time.monotonic() - t_up0
            if status in TERMINAL or elapsed > POLL_TIMEOUT_S:
                break
            time.sleep(2)
        with platform_session() as session:
            runs = session.execute(
                sql("SELECT input_tokens, output_tokens, est_cost_usd::text, latency_ms FROM extraction_runs "
                    "WHERE document_id = :i AND succeeded IS NOT NULL ORDER BY created_at"), {"i": doc_id}).all()
        cost = sum((Decimal(r[2]) for r in runs if r[2] is not None), Decimal("0"))
        spent += cost
        entry.update({"document_id": doc_id, "status": status, "seconds": round(elapsed, 1), "cost": str(cost),
                      "runs": [{"in": r[0], "out": r[1], "cost": r[2], "latency_ms": r[3]} for r in runs]})
        record["orders"].append(entry)
        save()
        print(f"{i + 1:>2} {entry['at']} {doc_id} {status} in {entry['seconds']}s cost ${cost} "
              f"cold_start {cold} spent ${spent}")
        if status != "needs_review":
            print("   not needs_review; stopping")
            sys.exit(3)
        if spent >= SPEND_STOP_USD:
            print("   spend stop reached; stopping")
            sys.exit(4)

    remaining = t0 + ORDERS * SLOT_SECONDS - time.monotonic()
    print(f"last order done {now()}; waiting {max(0, remaining):.0f}s to the end of the hour")
    if remaining > 0:
        time.sleep(remaining)
    record["ended"] = now()
    secs = [o["seconds"] for o in record["orders"]]
    colds = [o["api_cold_start_s"] for o in record["orders"] if o["api_cold_start_s"] is not None]
    record["summary"] = {"orders": len(secs), "mean_seconds": round(sum(secs) / len(secs), 1), "min_seconds": min(secs),
                         "max_seconds": max(secs), "total_cost": str(spent), "api_cold_starts": colds}
    save()
    print(f"busy hour ends {record['ended']}: {json.dumps(record['summary'])}")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--list":
        list_orders()
    elif len(sys.argv) == 3 and sys.argv[1] == "--run":
        run(sys.argv[2])
    else:
        sys.exit(__doc__)
