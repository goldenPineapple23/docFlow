"""RUNBOOK 9.7 step 14: the 500 + 1 run on Fly staging.

    python load_500_plus_1.py --list                             # what it would send; touches nothing
    python load_500_plus_1.py --make-tenant <state.json>         # the backfill's tenant, nothing else
    python load_500_plus_1.py --run <state.json> <result.json>   # the run (about 2 hours)

--make-tenant makes one new Scale-tier test tenant ("Acme Test Load <hex>")
with one owner, by direct insert as docflow_admin (as G1 did; no Console audit
row). The founder then imports the G2 fixture catalog into it in the Console
(the only catalog import path; founder, 2026-10-08), so matching runs.

What --run does, in order:
  1. Refuses to start unless: the Fly API answers /healthz over the proxy and
     the dispatcher is not stale; no document on staging is pending or
     processing; the model provider is marked up.
  2. Refuses to start unless the backfill's tenant has a committed catalog
     (live items), then gives both tenants' throwaway owners a new password.
  3. Uploads 500 distinct short text orders into it, one request per file with
     batch_size=500 (what the upload page sends for 500 chosen files: bulk lane).
  4. Uploads 1 order into the second tenant (the G1 test tenant), batch_size=1.
  5. Watches until all 501 have left pending/processing, then prints the
     measurements. Approves, exports and deletes nothing.

It stops the worker (`fly scale count worker=0`) and exits on any of:
  - recorded model spend since the first Fly deploy reaching $15.00;
  - a document of either tenant in any state but pending / processing /
    needs_review;
  - any new founder alert above "info", for any tenant or none;
  - an upload that is refused or fails (not retried, except one new sign-in
    after a 401);
  - the API not answering;
  - no document finishing for 10 minutes while some still wait.
After a stop every document stays as it is; the waiting ones stay pending.

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
from uuid import uuid4

API = "http://localhost:18000"
WORKER_APP = "docflow-worker-staging"
TENANT_B = "40594983-03ff-47b1-ac40-f636488b8de7"  # "Acme Test G1 2a60cc": Starter, 29 documents
BACKFILL = 500
TOTAL_STOP_USD = Decimal("15.00")
SPEND_SINCE = "2026-10-06"  # the first Fly deploy: everything the deployed worker has spent
STALL_STOP_S = 600
POLL_S = 2.0
PLUS_ONE_POLL_S = 0.5
PROGRESS_EVERY_S = 60
IN_PROGRESS = {"pending", "processing"}
# The "+1" pass marks (given to the founder before the run).
PLUS_ONE_MAX_BACKFILL_CLAIMS_AHEAD = 0  # 1 is explained only by the save-instant race
PLUS_ONE_SECONDS = 60

BUYERS = [
    ("Acme Test Bakery", "210 Test Avenue, Salem, OR 97301", "orders@acmetestbakery.example"),
    ("Acme Test Diner", "88 Test Road, Eugene, OR 97401", "purchasing@acmetestdiner.example"),
    ("Acme Test Roasters", "5 Test Lane, Bend, OR 97701", "buy@acmetestroasters.example"),
    ("Acme Test Bistro", "730 Test Boulevard, Medford, OR 97501", "orders@acmetestbistro.example"),
    ("Acme Test Tea Room", "19 Test Court, Astoria, OR 97103", "orders@acmetesttearoom.example"),
]
ITEMS = [
    ("CF-1001", "Colombian Whole Bean 5lb", "CS", Decimal("47.50")),
    ("CF-2210", "Ethiopian Yirgacheffe 5lb", "CS", Decimal("62.00")),
    ("SY-0045", "Vanilla Syrup 750ml", "EA", Decimal("8.25")),
    ("CUP-12", "12oz Paper Cups (1000ct)", "BOX", Decimal("54.00")),
]


def build_order(i: int, today: date, prefix: str = "L5") -> tuple[str, str, Decimal]:
    """Order i (0-based): file name, text, total. Fixed by i and the day, so
    --list shows exactly what --run sends. The PO number differs for every i,
    so all the files differ (no two share a hash)."""
    name, address, email = BUYERS[i % len(BUYERS)]
    po = f"{prefix}-{today:%m%d}-{i + 1:03d}"
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
    sizes, hashes = [], set()
    import hashlib

    for i in range(BACKFILL):
        fname, text, total = build_order(i, today)
        sizes.append(len(text.encode()))
        hashes.add(hashlib.sha256(text.encode()).hexdigest())
        if i < 3 or i >= BACKFILL - 2:
            print(f"{i + 1:>3}  {fname:<16} {BUYERS[i % len(BUYERS)][0]:<20} {2 + i % 3} line(s)  total ${total:,.2f}  {sizes[-1]} bytes")
        elif i == 3:
            print("  ...")
    fname, text, total = build_order(0, today, "P1")
    print(f" +1  {fname:<16} {BUYERS[0][0]:<20} 2 line(s)  total ${total:,.2f}  {len(text.encode())} bytes  (second tenant)")
    hashes.add(hashlib.sha256(text.encode()).hexdigest())
    print(f"{BACKFILL} + 1 files, {len(hashes)} distinct hashes, {min(sizes)}-{max(sizes)} bytes each")
    print("first order in full (fictional):\n")
    print(build_order(0, today)[1])


def make_tenant(state_path: str) -> None:
    import httpx
    from docflow_core.config import get_settings
    from docflow_core.db import platform_session, use_own_login
    from sqlalchemy import text as sql

    use_own_login("admin")
    s = get_settings()
    with platform_session() as session:
        scale = session.execute(
            sql("SELECT id, document_allowance FROM tiers WHERE name = 'Scale' AND is_current")
        ).all()
    if len(scale) != 1:
        sys.exit(f"expected one current Scale tier, found {len(scale)}; nothing done")
    tag = secrets.token_hex(3)
    tenant_a, user_a = uuid4(), uuid4()
    a_name, a_email = f"Acme Test Load {tag}", f"load-{tag}@example.test"
    admin = {"apikey": s.supabase_service_role_key, "Authorization": f"Bearer {s.supabase_service_role_key}"}
    r = httpx.post(f"{s.supabase_url}/auth/v1/admin/users", headers=admin,
                   json={"email": a_email, "password": "DocFlow-load-" + secrets.token_urlsafe(16),
                         "email_confirm": True}, timeout=30)
    r.raise_for_status()
    with platform_session() as session:
        session.execute(
            sql("INSERT INTO tenants (id, name, status, onboarding_status, tier_id, created_at, updated_at, status_changed_at) "
                "VALUES (:id, :name, 'active', 'tenant_created', :tier, now(), now(), now())"),
            {"id": str(tenant_a), "name": a_name, "tier": str(scale[0][0])},
        )
        session.execute(
            sql("INSERT INTO users (id, tenant_id, email, role, auth_user_id, is_active, created_at, updated_at) "
                "VALUES (:id, :t, :email, 'owner', :a, true, now(), now())"),
            {"id": str(user_a), "t": str(tenant_a), "email": a_email, "a": r.json()["id"]},
        )
    state = {"tenant_a": str(tenant_a), "tenant_a_name": a_name, "made_at": now(),
             "scale_tier": str(scale[0][0]), "allowance": scale[0][1]}
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    print(f"tenant {tenant_a} ({a_name}) made {state['made_at']}, Scale tier {scale[0][0]} allowance {scale[0][1]}")


def run(state_path: str, out_path: str, resume: bool = False) -> None:
    """`resume`: carry on a run whose watching process ended early (added
    2026-10-08 after the run began, in case the terminal ends the process).
    It keeps the first start's clock and spend baseline from <result.json>,
    uploads only the files the tenant doesn't have yet, and never uploads the
    + 1 twice."""
    import httpx
    from docflow_core import model_provider, usage
    from docflow_core.config import get_settings
    from docflow_core.db import platform_session, use_own_login
    from sqlalchemy import text as sql

    use_own_login("admin")
    s = get_settings()
    fly = shutil.which("fly") or shutil.which("flyctl")
    if not fly:
        sys.exit("fly is not on PATH")
    with open(state_path, encoding="utf-8") as fh:
        state = json.load(fh)
    A, B = state["tenant_a"], TENANT_B
    record: dict = {"started": now(), **state, "tenant_b": TENANT_B, "stops": None}
    prev: dict = {}
    if resume:
        with open(out_path, encoding="utf-8") as fh:
            prev = json.load(fh)
        record = {**prev, "stops": None, "resumed_at": [*prev.get("resumed_at", []), now()]}

    def save() -> None:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, default=str)

    def stop_worker(reason: str, code: int) -> None:
        print(f"\n{now()} STOP: {reason}")
        print(f"{now()} stopping the worker: fly scale count worker=0 --app {WORKER_APP}")
        r = subprocess.run([fly, "scale", "count", "worker=0", "--app", WORKER_APP, "--yes"],
                           capture_output=True, text=True, timeout=180)
        print(f"{now()} fly exit {r.returncode}; the Healthchecks check will report 'down' within about 12 minutes")
        record["stops"] = {"at": now(), "reason": reason, "fly_exit": r.returncode}
        save()
        sys.exit(code)

    def healthz() -> dict | None:
        try:
            r = httpx.get(f"{API}/healthz", timeout=6)
            return r.json() if r.status_code == 200 else None
        except httpx.HTTPError:
            return None

    # ── 1. Refuse to start unless staging is as expected ────────────────────
    hz = healthz()
    if hz is None:
        sys.exit("the API does not answer /healthz on the proxy; nothing done")
    if hz["dispatcher"]["stale"]:
        sys.exit(f"the dispatcher is stale ({hz['dispatcher']}); nothing done")
    with platform_session() as session:
        t0 = session.execute(sql("SELECT now()")).scalar_one()
        busy = session.execute(
            sql("SELECT count(*) FROM documents WHERE status IN ('pending','processing') AND deleted_at IS NULL")
        ).scalar_one()
        provider = session.execute(
            sql("SELECT status FROM public.provider_state(:p)"), {"p": model_provider.PROVIDER}
        ).scalar()
        prior = Decimal(session.execute(
            sql("SELECT coalesce(sum(est_cost_usd), 0)::text FROM extraction_runs WHERE created_at >= :d"),
            {"d": SPEND_SINCE},
        ).scalar_one())
        a_email, a_auth = session.execute(
            sql("SELECT email, auth_user_id FROM users WHERE tenant_id = :t AND role = 'owner'"), {"t": A}
        ).one()
        b_email, b_auth = session.execute(
            sql("SELECT email, auth_user_id FROM users WHERE tenant_id = :t AND role = 'owner'"), {"t": B}
        ).one()
        a_items = session.execute(
            sql("SELECT count(*) FROM items WHERE tenant_id = :t AND deleted_at IS NULL"), {"t": A}
        ).scalar_one()
        a_docs = session.execute(sql("SELECT count(*) FROM documents WHERE tenant_id = :t"), {"t": A}).scalar_one()
    if resume:
        t0 = datetime.fromisoformat(prev["t0"])
        prior = Decimal(prev["prior_spend"])
        print(f"RESUMING {now()}: first start {prev['started']}, {a_docs} of the backfill already uploaded")
    elif busy:
        sys.exit(f"{busy} document(s) already pending or processing on staging; nothing done")
    elif a_docs:
        sys.exit(f"the backfill's tenant already has {a_docs} document(s); nothing done")
    if provider == "down":
        sys.exit("the model provider is marked down; nothing done")
    if not a_items:
        sys.exit("the backfill's tenant has no catalog items yet (the founder's Console import); nothing done")
    if prior >= TOTAL_STOP_USD:
        sys.exit(f"recorded spend is already ${prior}; nothing done")
    print(f"start {record['started']} (database clock {t0}); /healthz {hz}")
    print(f"backfill tenant {A} ({state['tenant_a_name']}): {a_items} live catalog item(s)")
    print(f"recorded model spend since {SPEND_SINCE}: ${prior}; this run stops at ${TOTAL_STOP_USD} total")
    record.update({"t0": t0, "prior_spend": str(prior), "healthz_before": hz, "catalog_items": a_items})

    # ── 2. Both owners are throwaways made by script: give each a new password ─
    admin = {"apikey": s.supabase_service_role_key, "Authorization": f"Bearer {s.supabase_service_role_key}"}
    passwords = {a_email: "DocFlow-load-" + secrets.token_urlsafe(16), b_email: "DocFlow-load-" + secrets.token_urlsafe(16)}
    for email, auth_id in ((a_email, a_auth), (b_email, b_auth)):
        httpx.put(f"{s.supabase_url}/auth/v1/admin/users/{auth_id}", headers=admin,
                  json={"password": passwords[email]}, timeout=30).raise_for_status()
    save()

    def bearer(email: str) -> dict[str, str]:
        r = httpx.post(f"{s.supabase_url}/auth/v1/token?grant_type=password",
                       headers={"apikey": s.supabase_anon_key},
                       json={"email": email, "password": passwords[email]}, timeout=30)
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def check_stops() -> Decimal:
        """Every stop condition read from the database. Returns the run's spend."""
        with platform_session() as session:
            total = Decimal(session.execute(
                sql("SELECT coalesce(sum(est_cost_usd), 0)::text FROM extraction_runs WHERE created_at >= :d"),
                {"d": SPEND_SINCE},
            ).scalar_one())
            bad = session.execute(
                sql("SELECT id, status FROM documents WHERE tenant_id IN (:a, :b) AND created_at >= :t0 "
                    "AND status NOT IN ('pending','processing','needs_review') LIMIT 5"),
                {"a": A, "b": B, "t0": t0},
            ).all()
            alerts = session.execute(
                sql("SELECT type, severity, created_at FROM founder_alerts "
                    "WHERE created_at >= :t0 AND severity <> 'info' ORDER BY created_at LIMIT 5"),
                {"t0": t0},
            ).all()
        if total >= TOTAL_STOP_USD:
            stop_worker(f"recorded spend ${total} has reached ${TOTAL_STOP_USD}", 10)
        if bad:
            stop_worker("document(s) not pending/processing/needs_review: "
                        + ", ".join(f"{str(d)[:8]} {st}" for d, st in bad), 11)
        if alerts:
            stop_worker("new founder alert(s): " + ", ".join(f"{t} {sev} {at}" for t, sev, at in alerts), 12)
        return total - prior

    def upload(email: str, headers: dict[str, str], fname: str, body: str, batch: int) -> tuple[str, dict[str, str]]:
        for attempt in (1, 2):
            try:
                up = httpx.post(f"{API}/documents/upload", headers=headers,
                                files={"file": (fname, body.encode(), "text/plain")},
                                data={"batch_size": str(batch)}, timeout=120)
            except httpx.HTTPError as exc:
                stop_worker(f"upload of {fname} failed: {type(exc).__name__} (not retried)", 13)
            if up.status_code == 401 and attempt == 1:
                headers = bearer(email)  # the sign-in ran out; a refused request stored nothing
                continue
            if up.status_code != 200:
                detail = up.json().get("detail") if "json" in up.headers.get("content-type", "") else None
                code = detail.get("code") if isinstance(detail, dict) else None
                stop_worker(f"upload of {fname} refused: HTTP {up.status_code} code {code}", 14)
            body_json = up.json()
            if "possible_duplicate_of" in body_json:
                stop_worker(f"upload of {fname} was linked as a possible duplicate (the files should all differ)", 15)
            return body_json["document_id"], headers
        raise AssertionError("unreachable")

    # ── 3. The 500 ──────────────────────────────────────────────────────────
    today = datetime.now(UTC).date()
    headers_a = bearer(a_email)
    t_up = time.monotonic()
    record.setdefault("uploads_started", now())
    with platform_session() as session:
        have = {r[0] for r in session.execute(
            sql("SELECT original_filename FROM documents WHERE tenant_id = :a"), {"a": A})}
    for i in range(BACKFILL):
        fname, body, _total = build_order(i, today)
        if fname in have:
            continue
        _doc, headers_a = upload(a_email, headers_a, fname, body, BACKFILL)
        if (i + 1) % 25 == 0:
            spent = check_stops()
            if healthz() is None:
                stop_worker("the API stopped answering /healthz during the uploads", 16)
            print(f"{now()} uploaded {i + 1}/{BACKFILL} in {time.monotonic() - t_up:.0f}s; run spend ${spent}")
    record.setdefault("uploads_ended", now())
    record.setdefault("upload_seconds", round(time.monotonic() - t_up, 1))
    save()

    # ── 4. The + 1, from the second tenant, while the backfill is queued ────
    fname, body, _total = build_order(0, today, "P1")
    with platform_session() as session:
        waiting = session.execute(
            sql("SELECT count(*) FROM documents WHERE tenant_id = :a AND status = 'pending' AND dispatched_at IS NULL"),
            {"a": A},
        ).scalar_one()
        already = session.execute(
            sql("SELECT id FROM documents WHERE tenant_id = :b AND original_filename = :f AND created_at >= :t0"),
            {"b": B, "f": fname, "t0": t0},
        ).scalar()
    t_one: float | None = None
    if already is not None:  # a resumed run: it went up before the first process ended
        one_id = str(already)
        record.setdefault("plus_one", {"document_id": one_id})
        print(f"{now()} + 1 was already uploaded: {one_id}")
    else:
        t_one = time.monotonic()
        one_id, _h = upload(b_email, bearer(b_email), fname, body, 1)
        record["plus_one"] = {"document_id": one_id, "uploaded_at": now(), "backfill_waiting_at_upload": waiting}
        print(f"{now()} + 1 uploaded: {one_id}; {waiting} of the backfill still waiting undispatched")
    save()

    # ── 5. Watch ────────────────────────────────────────────────────────────
    done_at: dict[str, str] = {}
    one_seconds: float | None = None
    one_done = False
    largest_live_unclaimed = float(prev.get("largest_live_unclaimed_s_so_far", 0.0))
    last_done = last_progress = time.monotonic()
    while True:
        with platform_session() as session:
            rows = session.execute(
                sql("SELECT id, status, CASE WHEN status = 'pending' AND dispatched_at IS NOT NULL "
                    "THEN EXTRACT(EPOCH FROM now() - dispatched_at) END "
                    "FROM documents WHERE tenant_id IN (:a, :b) AND created_at >= :t0"),
                {"a": A, "b": B, "t0": t0},
            ).all()
        stamp = now()
        for doc, status, unclaimed in rows:
            if unclaimed is not None:
                largest_live_unclaimed = max(largest_live_unclaimed, float(unclaimed))
            if status not in IN_PROGRESS and str(doc) not in done_at:
                done_at[str(doc)] = stamp
                last_done = time.monotonic()
                if str(doc) == one_id:
                    one_done = True
                    if t_one is not None:
                        one_seconds = round(time.monotonic() - t_one, 1)
                    print(f"{stamp} + 1 is {status} {one_seconds}s after its upload (this process's clock)")
        spent = check_stops()
        if len(done_at) >= BACKFILL + 1:
            break
        if time.monotonic() - last_done > STALL_STOP_S:
            stop_worker(f"no document finished for {STALL_STOP_S // 60} minutes with {len(rows) - len(done_at)} left", 17)
        if time.monotonic() - last_progress >= PROGRESS_EVERY_S:
            last_progress = time.monotonic()
            hz = healthz()
            if hz is None:
                stop_worker("the API stopped answering /healthz", 16)
            print(f"{stamp} done {len(done_at)}/{BACKFILL + 1}; run spend ${spent}; "
                  f"largest unclaimed age seen live {largest_live_unclaimed:.1f}s; dispatcher {hz['dispatcher']}")
        time.sleep(POLL_S if one_done else PLUS_ONE_POLL_S)

    # ── 6. Measurements, all from the database's own stamps ─────────────────
    record["ended"] = now()
    with platform_session() as session:
        docs = session.execute(
            sql("SELECT id, tenant_id, status, dispatch_lane, processing_attempts, created_at, dispatched_at, "
                "processing_started_at, EXTRACT(EPOCH FROM processing_started_at - dispatched_at), "
                "EXTRACT(EPOCH FROM dispatched_at - created_at) "
                "FROM documents WHERE tenant_id IN (:a, :b) AND created_at >= :t0 ORDER BY created_at"),
            {"a": A, "b": B, "t0": t0},
        ).all()
        runs = session.execute(
            sql("SELECT count(*), sum(r.est_cost_usd)::text, min(r.est_cost_usd)::text, max(r.est_cost_usd)::text, "
                "sum(r.input_tokens), sum(r.output_tokens), "
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY r.latency_ms), "
                "percentile_cont(0.95) WITHIN GROUP (ORDER BY r.latency_ms) "
                "FROM extraction_runs r JOIN documents d ON d.id = r.document_id "
                "WHERE d.tenant_id IN (:a, :b) AND d.created_at >= :t0 AND r.est_cost_usd IS NOT NULL"),
            {"a": A, "b": B, "t0": t0},
        ).one()
        per_doc = session.execute(
            sql("SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY c), percentile_cont(0.95) WITHIN GROUP (ORDER BY c), "
                "max(c) FROM (SELECT sum(r.est_cost_usd) AS c FROM extraction_runs r JOIN documents d ON d.id = r.document_id "
                "WHERE d.tenant_id IN (:a, :b) AND d.created_at >= :t0 AND r.est_cost_usd IS NOT NULL "
                "GROUP BY r.document_id) x"),
            {"a": A, "b": B, "t0": t0},
        ).one()
        one = session.execute(
            sql("SELECT status, dispatch_lane, processing_attempts, created_at, dispatched_at, processing_started_at, "
                "processed_at, EXTRACT(EPOCH FROM processed_at - created_at) "
                "FROM documents WHERE id = :i"), {"i": one_id}
        ).one()
        ahead = session.execute(
            sql("SELECT count(*) FROM documents WHERE tenant_id = :a AND processing_started_at > :c "
                "AND processing_started_at < :p"), {"a": A, "c": one[3], "p": one[5]}
        ).scalar_one()
        alerts = session.execute(
            sql("SELECT type, severity, created_at FROM founder_alerts WHERE created_at >= :t0 ORDER BY created_at"),
            {"t0": t0},
        ).all()
        allowance = usage.allowance_for(session, A)
        last_claim = session.execute(
            sql("SELECT max(processing_started_at), min(created_at) FROM documents WHERE tenant_id = :a"), {"a": A}
        ).one()

    by_status: dict[str, int] = {}
    for d in docs:
        by_status[d[2]] = by_status.get(d[2], 0) + 1
    unclaimed = sorted(((float(d[8]), str(d[0])[:8], d[6]) for d in docs if d[8] is not None), reverse=True)
    waits = sorted((float(d[9]) for d in docs if d[9] is not None), reverse=True)
    retried = [str(d[0])[:8] for d in docs if d[4] != 1]
    lanes = {(str(d[1])[:8], d[3]) for d in docs}

    print(f"\n== 500 + 1 ended {record['ended']}")
    print(f"documents: {len(docs)}; by status {by_status}; lanes {sorted(lanes)}")
    print(f"uploads: {record['upload_seconds']}s for {BACKFILL}")
    print(f"backfill: first upload {last_claim[1]}, last claim {last_claim[0]}")
    print(f"runs costed: {runs[0]}; total ${runs[1]}; per run ${runs[2]}-${runs[3]}; tokens in {runs[4]} out {runs[5]}; "
          f"model latency ms p50 {runs[6]:.0f} p95 {runs[7]:.0f}")
    print(f"cost per document: p50 ${per_doc[0]:.4f} p95 ${per_doc[1]:.4f} max ${per_doc[2]:.4f}")
    print(f"documents with processing_attempts other than 1: {len(retried)} {retried[:10]}")
    print(f"unclaimed age (claim minus dispatch): largest {unclaimed[0][0]:.2f}s; five largest "
          + ", ".join(f"{u:.2f}s {i} dispatched {at:%H:%M:%S}" for u, i, at in unclaimed[:5]))
    print(f"largest unclaimed age seen live while watching: {largest_live_unclaimed:.1f}s")
    print(f"longest wait before dispatch (the backfill's own queue): {waits[0]:.0f}s")
    print(f"allowance for the backfill's tenant: {allowance}")
    print(f"founder alerts since the start: {[(t, sev, str(at)) for t, sev, at in alerts] or 'none'}")
    print(f"\n+ 1 ({one_id}): {one[0]}, lane {one[1]}, attempts {one[2]}")
    print(f"   saved {one[3]}  dispatched {one[4]}  claimed {one[5]}")
    print(f"   backfill documents claimed between its save and its claim: {ahead} "
          f"(pass mark {PLUS_ONE_MAX_BACKFILL_CLAIMS_AHEAD}) -> {'PASS' if ahead <= PLUS_ONE_MAX_BACKFILL_CLAIMS_AHEAD else 'LOOK'}")
    db_seconds = round(float(one[7]), 1) if one[7] is not None else None
    timed = db_seconds if db_seconds is not None else one_seconds
    print(f"   processed {one[6]}; save to processed, database stamps: {db_seconds}s; "
          f"upload request to seen done, this process's clock: {one_seconds}s")
    print(f"   {one[0]} in {timed}s (expected within {PLUS_ONE_SECONDS}s) -> "
          f"{'PASS' if one[0] == 'needs_review' and timed is not None and timed <= PLUS_ONE_SECONDS else 'LOOK'}")
    record.update({
        "by_status": by_status, "total_cost": runs[1], "runs_costed": runs[0],
        "largest_unclaimed_s": unclaimed[0][0], "largest_live_unclaimed_s": largest_live_unclaimed,
        "five_largest_unclaimed": unclaimed[:5], "longest_wait_s": waits[0], "retried": retried,
        "alerts": [(t, sev, at) for t, sev, at in alerts],
        "plus_one": {**record["plus_one"], "status": one[0], "created_at": one[3], "dispatched_at": one[4],
                     "claimed_at": one[5], "processed_at": one[6], "db_seconds": db_seconds,
                     "backfill_claims_ahead": ahead, "seconds": one_seconds},
        "done_at": done_at,
    })
    save()


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--list":
        list_orders()
    elif len(sys.argv) == 3 and sys.argv[1] == "--make-tenant":
        make_tenant(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] in ("--run", "--resume"):
        run(sys.argv[2], sys.argv[3], resume=sys.argv[1] == "--resume")
    else:
        sys.exit(__doc__)
