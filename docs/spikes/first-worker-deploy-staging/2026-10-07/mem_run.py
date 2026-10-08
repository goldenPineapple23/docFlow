"""RUNBOOK 9.7 step 11, "under real documents": upload heavy documents one at a
time through the Fly API and, after each, read the worker's per-process peaks
and the parse machine's memory. Prints ids, statuses, times, tokens, cost and
memory figures only -- never a document's content.

    python mem_run.py <repo> <scratchpad> <result.json>
"""

from __future__ import annotations

import base64
import json
import secrets
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from docflow_core.config import get_settings
from docflow_core.db import platform_session, use_own_login
from sqlalchemy import text as sql

REPO, SP, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
API = "http://127.0.0.1:18000"
API_APP, API_MACHINE = "docflow-api-staging", "8e7d7eb762d918"
PARSE_APP, PARSE_MACHINE = "docflow-parse-staging", "863662ce743978"
WORKER_APP = "docflow-worker-staging"
TENANT_ID = "40594983-03ff-47b1-ac40-f636488b8de7"
TERMINAL = {"needs_review", "failed", "quarantined", "dead_letter", "rejected"}
FIX = REPO / "apps/parse/tests/fixtures/positive"
DOCS = [  # label, path, mime, seconds to wait for a final status
    ("legacy .doc", FIX / "po.doc", "application/msword", 240),
    ("scanned PDF, 1 page", FIX / "po-scanned.pdf", "application/pdf", 240),
    ("multi-page .tif", FIX / "po.tif", "image/tiff", 240),
    ("photo .jpg", FIX / "po.jpg", "image/jpeg", 240),
    ("scanned PDF, 24 pages, 22 MB", SP / "mem-big-scanned.pdf", "application/pdf", 600),
    ("scanned PDF, 24 pages, 24.4 MB", SP / "mem-cap-scanned.pdf", "application/pdf", 900),
]
FLY = shutil.which("fly") or shutil.which("flyctl")

WORKER_PROBE = r"""
echo "usage $(cat /sys/fs/cgroup/memory/memory.usage_in_bytes)"
awk '/^MemTotal/{t=$2}/^MemAvailable/{a=$2}END{print "used_kB " t-a " avail_kB " a}' /proc/meminfo
for d in /proc/[0-9]*; do
  p=${d#/proc/}
  [ "$(stat -c %U "$d" 2>/dev/null)" = "docflow" ] || continue
  echo "proc $p ppid $(awk '/^PPid/{print $2}' $d/status) rss $(awk '/^VmRSS/{print $2}' $d/status) hwm $(awk '/^VmHWM/{print $2}' $d/status) $(tr '\0' ' ' < $d/cmdline | cut -c1-90)"
done
"""
PARSE_PROBE = r"""
echo "uptime_s $(cut -d' ' -f1 /proc/uptime)"
echo "root_usage $(cat /sys/fs/cgroup/memory/memory.usage_in_bytes) root_max_usage $(cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes)"
echo "jobs_usage $(cat /sys/fs/cgroup/memory/docflow-jobs/memory.usage_in_bytes 2>/dev/null) jobs_max_usage $(cat /sys/fs/cgroup/memory/docflow-jobs/memory.max_usage_in_bytes 2>/dev/null)"
awk '/^MemTotal/{t=$2}/^MemAvailable/{a=$2}END{print "used_kB " t-a " avail_kB " a}' /proc/meminfo
"""


def now() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3] + "Z"


def fly(*args: str, timeout: int = 120) -> str:
    r = subprocess.run([FLY, *args], capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "") + (r.stderr or "")


def probe(app: str, script: str, *extra: str) -> list[str]:
    b64 = base64.b64encode(script.replace("\r", "").encode()).decode()
    out = fly("ssh", "console", "--app", app, *extra, "-C", f"sh -c 'echo {b64} | base64 -d | sh'")
    return [ln.strip() for ln in out.splitlines() if ln.strip() and not ln.startswith("Connecting")]


def healthz_ok() -> bool:
    try:
        return httpx.get(f"{API}/healthz", timeout=4).status_code == 200
    except httpx.HTTPError:
        return False


def fresh_api() -> None:
    """Stop and start the API machine, so an upload has the whole idle window
    ahead of it (Fly stops the machine a few minutes after a start)."""
    fly("machine", "stop", API_MACHINE, "--app", API_APP)
    time.sleep(4)
    fly("machine", "start", API_MACHINE, "--app", API_APP)
    t = time.monotonic()
    while not healthz_ok():
        if time.monotonic() - t > 120:
            sys.exit("the API did not answer within 120 s of a start; stopping")
        time.sleep(0.5)


def main() -> None:
    use_own_login("admin")
    s = get_settings()
    with platform_session() as session:
        email, auth_user_id = session.execute(
            sql("SELECT email, auth_user_id FROM users WHERE tenant_id = :t AND role = 'owner'"), {"t": TENANT_ID}
        ).one()
    password = "DocFlow-mem-" + secrets.token_urlsafe(16)
    admin = {"apikey": s.supabase_service_role_key, "Authorization": f"Bearer {s.supabase_service_role_key}"}
    httpx.put(f"{s.supabase_url}/auth/v1/admin/users/{auth_user_id}", headers=admin,
              json={"password": password}, timeout=30).raise_for_status()

    def bearer() -> dict[str, str]:
        r = httpx.post(f"{s.supabase_url}/auth/v1/token?grant_type=password",
                       headers={"apikey": s.supabase_anon_key}, json={"email": email, "password": password}, timeout=30)
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    record: dict = {"started": now(), "documents": []}
    spent = Decimal("0")

    def save() -> None:
        OUT.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")

    print(f"== start {now()}")
    print("== worker before:"); [print("   " + ln) for ln in probe(WORKER_APP, WORKER_PROBE, "--process-group", "worker")]
    fly("machine", "start", PARSE_MACHINE, "--app", PARSE_APP)
    time.sleep(8)
    print("== parse before:"); [print("   " + ln) for ln in probe(PARSE_APP, PARSE_PROBE)]

    for label, path, mime, wait_s in DOCS:
        content = path.read_bytes()
        print(f"\n== {label}: {path.name}, {len(content)} bytes ({len(content) / 1048576:.2f} MB)")
        fresh_api()
        entry: dict = {"label": label, "file": path.name, "bytes": len(content), "epoch_start": time.time(), "at": now()}
        t0 = time.monotonic()
        try:
            up = httpx.post(f"{API}/documents/upload", headers=bearer(),
                            files={"file": (path.name, content, mime)}, data={"batch_size": "1"}, timeout=600)
        except httpx.HTTPError as exc:
            entry["upload_error"] = type(exc).__name__
            record["documents"].append(entry); save()
            print(f"   {now()} upload failed: {type(exc).__name__}; stopping (not retried)")
            sys.exit(2)
        entry["upload_seconds"] = round(time.monotonic() - t0, 1)
        entry["http"] = up.status_code
        if up.status_code != 200:
            detail = up.json().get("detail") if "json" in up.headers.get("content-type", "") else None
            entry["code"] = detail.get("code") if isinstance(detail, dict) else None
            record["documents"].append(entry); save()
            print(f"   {now()} upload HTTP {up.status_code} code {entry['code']} after {entry['upload_seconds']}s; next document")
            continue
        doc_id = up.json()["document_id"]
        print(f"   {now()} uploaded in {entry['upload_seconds']}s -> {doc_id} {up.json()['status']}"
              + (f" possible_duplicate_of {up.json()['possible_duplicate_of']}" if "possible_duplicate_of" in up.json() else ""))
        last = None
        while True:
            with platform_session() as session:
                status = session.execute(sql("SELECT status FROM documents WHERE id = :i"), {"i": doc_id}).scalar_one()
            el = time.monotonic() - t0
            if status != last:
                print(f"   {now()} +{el:6.1f}s {status}")
                last = status
            if status in TERMINAL or el > wait_s:
                break
            time.sleep(2)
        entry.update({"document_id": doc_id, "status": status, "seconds": round(el, 1), "epoch_end": time.time()})
        with platform_session() as session:
            runs = [dict(r._mapping) for r in session.execute(
                sql("SELECT run_kind, run_state, succeeded, error_code, input_tokens, output_tokens, "
                    "counted_input_tokens, est_cost_usd::text AS cost, latency_ms, created_at "
                    "FROM extraction_runs WHERE document_id = :i ORDER BY created_at"), {"i": doc_id})]
            doc = dict(session.execute(
                sql("SELECT status, processing_attempts, to_jsonb(d) - 'raw_json' AS row FROM documents d WHERE id = :i"),
                {"i": doc_id}).one()._mapping)
        row = doc["row"]
        entry["runs"] = runs
        entry["doc"] = {k: row.get(k) for k in ("status", "processing_attempts", "error_code", "failure_code",
                                                "parse_lost_attempts", "quarantine_reason") if k in row}
        for r in runs:
            print(f"   run {r['run_kind']} state={r['run_state']} ok={r['succeeded']} err={r['error_code']} "
                  f"in={r['input_tokens']} out={r['output_tokens']} counted={r['counted_input_tokens']} "
                  f"cost=${r['cost']} latency_ms={r['latency_ms']}")
            if r["cost"] is not None and r["run_state"] != "started":
                spent += Decimal(r["cost"])
        print(f"   final {status} after {entry['seconds']}s; document fields {entry['doc']}; spent so far ${spent}")
        entry["worker"] = probe(WORKER_APP, WORKER_PROBE, "--process-group", "worker")
        entry["parse"] = probe(PARSE_APP, PARSE_PROBE)
        print("   worker after:"); [print("      " + ln) for ln in entry["worker"]]
        print("   parse after:"); [print("      " + ln) for ln in entry["parse"]]
        record["documents"].append(entry); save()
        if spent >= Decimal("1.00"):
            print("   spend stop ($1.00) reached; stopping")
            break

    record["ended"] = now()
    record["spent"] = str(spent)
    save()
    print(f"\n== end {record['ended']} spent ${spent}")


main()
