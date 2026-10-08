"""After the 500 + 1 (founder, 2026-10-08): two 22 MB scanned PDFs back to back
into the backfill's tenant, with the worker machine's memory sampled about five
times a second. Reports each document's peak and what the documents process
holds afterwards. Prints ids, statuses, times, tokens, cost and memory figures
only -- never a document's content.

    python mem_two_scans.py <state.json> <scratchpad> <result.json>
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

STATE, SP, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
API = "http://localhost:18000"
WORKER_APP, PARSE_APP = "docflow-worker-staging", "docflow-parse-staging"
FILES = [SP / "mem-scan-1.pdf", SP / "mem-scan-2.pdf"]
SAMPLES = SP / "worker_memory_samples_2026-10-08.txt"
TOTAL_STOP_USD = Decimal("15.00")
SPEND_SINCE = "2026-10-06"
WAIT_S = 900
AFTER_READINGS_S = (0, 60, 180)
IN_PROGRESS = {"pending", "processing"}
FLY = shutil.which("fly") or shutil.which("flyctl")

# One line a tick: epoch, machine memory used (MemTotal - MemAvailable, kB),
# cgroup usage (bytes), then pid:VmRSS(kB) for each documents-worker process
# (the Celery processes reading `interactive,bulk`; found again every 25 ticks,
# since a recycled child has a new pid).
SAMPLER = r"""
n=0; pids=""
while [ $n -lt 6000 ]; do
  if [ $((n % 25)) -eq 0 ]; then
    pids=""
    for d in /proc/[0-9]*; do
      case "$({ tr '\0' ' ' < $d/cmdline; } 2>/dev/null)" in *interactive,bulk*) pids="$pids ${d#/proc/}";; esac
    done
  fi
  line="$(date +%s.%N | cut -c1-14) $(awk '/^MemTotal/{t=$2}/^MemAvailable/{a=$2}END{print t-a}' /proc/meminfo) $(cat /sys/fs/cgroup/memory/memory.usage_in_bytes)"
  for p in $pids; do line="$line $p:$(awk '/^VmRSS/{print $2}' /proc/$p/status 2>/dev/null)"; done
  echo "$line"
  sleep 0.2
  n=$((n+1))
done
"""
WORKER_PROBE = r"""
echo "usage $(cat /sys/fs/cgroup/memory/memory.usage_in_bytes)"
awk '/^MemTotal/{t=$2}/^MemAvailable/{a=$2}END{print "total_kB " t " used_kB " t-a " avail_kB " a}' /proc/meminfo
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


def ssh_args(app: str, script: str, *extra: str) -> list[str]:
    b64 = base64.b64encode(script.replace("\r", "").encode()).decode()
    return [FLY, "ssh", "console", "--app", app, *extra, "-C", f"sh -c 'echo {b64} | base64 -d | sh'"]


def probe(app: str, script: str, *extra: str) -> list[str]:
    r = subprocess.run(ssh_args(app, script, *extra), capture_output=True, text=True, timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    return [ln.strip() for ln in out.splitlines() if ln.strip() and not ln.startswith("Connecting")]


def main() -> None:
    use_own_login("admin")
    s = get_settings()
    tenant = json.loads(STATE.read_text(encoding="utf-8"))["tenant_a"]
    with platform_session() as session:
        busy = session.execute(
            sql("SELECT count(*) FROM documents WHERE status IN ('pending','processing') AND deleted_at IS NULL")
        ).scalar_one()
        prior = Decimal(session.execute(
            sql("SELECT coalesce(sum(est_cost_usd), 0)::text FROM extraction_runs WHERE created_at >= :d"),
            {"d": SPEND_SINCE},
        ).scalar_one())
        email, auth_user_id = session.execute(
            sql("SELECT email, auth_user_id FROM users WHERE tenant_id = :t AND role = 'owner'"), {"t": tenant}
        ).one()
    if busy:
        sys.exit(f"{busy} document(s) still pending or processing; nothing done")
    if prior + Decimal("0.50") >= TOTAL_STOP_USD:
        sys.exit(f"recorded spend ${prior} is within $0.50 of the ${TOTAL_STOP_USD} stop; nothing done")
    if httpx.get(f"{API}/healthz", timeout=6).status_code != 200:
        sys.exit("the API does not answer /healthz; nothing done")

    password = "DocFlow-mem-" + secrets.token_urlsafe(16)
    admin = {"apikey": s.supabase_service_role_key, "Authorization": f"Bearer {s.supabase_service_role_key}"}
    httpx.put(f"{s.supabase_url}/auth/v1/admin/users/{auth_user_id}", headers=admin,
              json={"password": password}, timeout=30).raise_for_status()
    r = httpx.post(f"{s.supabase_url}/auth/v1/token?grant_type=password",
                   headers={"apikey": s.supabase_anon_key}, json={"email": email, "password": password}, timeout=30)
    r.raise_for_status()
    bearer = {"Authorization": f"Bearer {r.json()['access_token']}"}

    record: dict = {"started": now(), "tenant": tenant, "prior_spend": str(prior), "documents": []}

    def save() -> None:
        OUT.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")

    print(f"== start {record['started']}; recorded spend ${prior}")
    record["worker_before"] = probe(WORKER_APP, WORKER_PROBE, "--process-group", "worker")
    record["parse_before"] = probe(PARSE_APP, PARSE_PROBE)
    print("== worker before:"); [print("   " + ln) for ln in record["worker_before"]]
    print("== parse before (empty if its machine is stopped):"); [print("   " + ln) for ln in record["parse_before"]]

    sampler_out = SAMPLES.open("w", encoding="utf-8")
    sampler = subprocess.Popen(ssh_args(WORKER_APP, SAMPLER, "--process-group", "worker"),
                               stdout=sampler_out, stderr=subprocess.STDOUT)
    time.sleep(6)

    try:
        ids: list[str] = []
        for path in FILES:  # back to back: the second goes up as soon as the first upload returns
            content = path.read_bytes()
            t0 = time.monotonic()
            up = httpx.post(f"{API}/documents/upload", headers=bearer,
                            files={"file": (path.name, content, "application/pdf")},
                            data={"batch_size": "1"}, timeout=600)
            took = round(time.monotonic() - t0, 1)
            if up.status_code != 200:
                detail = up.json().get("detail") if "json" in up.headers.get("content-type", "") else None
                print(f"   {now()} {path.name}: upload HTTP {up.status_code} code "
                      f"{detail.get('code') if isinstance(detail, dict) else None}; stopping")
                sys.exit(2)
            body = up.json()
            if "possible_duplicate_of" in body:
                print(f"   {now()} {path.name}: linked as a possible duplicate; stopping")
                sys.exit(3)
            ids.append(body["document_id"])
            record["documents"].append({"file": path.name, "bytes": len(content), "document_id": body["document_id"],
                                        "upload_seconds": took, "uploaded_at": now()})
            save()
            print(f"   {now()} {path.name} ({len(content) / 1048576:.2f} MB) uploaded in {took}s -> {body['document_id']}")

        t_wait = time.monotonic()
        seen: dict[str, str] = {}
        while len(seen) < len(ids) and time.monotonic() - t_wait < WAIT_S:
            with platform_session() as session:
                rows = session.execute(
                    sql("SELECT id, status FROM documents WHERE id IN (:a, :b)"), {"a": ids[0], "b": ids[1]}
                ).all()
            for doc, status in rows:
                if status not in IN_PROGRESS and str(doc) not in seen:
                    seen[str(doc)] = status
                    print(f"   {now()} {str(doc)[:8]} {status}")
            time.sleep(1)

        for after in AFTER_READINGS_S:
            wait = after - (0 if after == 0 else AFTER_READINGS_S[AFTER_READINGS_S.index(after) - 1])
            time.sleep(wait)
            lines = probe(WORKER_APP, WORKER_PROBE, "--process-group", "worker")
            record[f"worker_after_{after}s"] = lines
            print(f"== worker {after}s after the second document ({now()}):"); [print("   " + ln) for ln in lines]
        record["parse_after"] = probe(PARSE_APP, PARSE_PROBE)
        print("== parse after:"); [print("   " + ln) for ln in record["parse_after"]]
    finally:
        sampler.terminate()
        sampler_out.close()

    # ── Each document's own window, from the database's stamps, against the samples ──
    samples = []
    for ln in SAMPLES.read_text(encoding="utf-8").splitlines():
        parts = ln.split()
        try:
            ts, used_kb, cgroup = float(parts[0]), int(parts[1]), int(parts[2])
        except (ValueError, IndexError):
            continue
        rss = {p.split(":")[0]: int(p.split(":")[1]) for p in parts[3:] if p.count(":") == 1 and p.split(":")[1].isdigit()}
        samples.append((ts, used_kb, cgroup, rss))
    print(f"\n== {len(samples)} samples")
    with platform_session() as session:
        for entry in record["documents"]:
            d = session.execute(
                sql("SELECT status, processing_attempts, EXTRACT(EPOCH FROM dispatched_at), "
                    "EXTRACT(EPOCH FROM processing_started_at) FROM documents WHERE id = :i"),
                {"i": entry["document_id"]},
            ).one()
            runs = session.execute(
                sql("SELECT input_tokens, output_tokens, est_cost_usd::text, latency_ms, succeeded, "
                    "EXTRACT(EPOCH FROM created_at) FROM extraction_runs WHERE document_id = :i ORDER BY created_at"),
                {"i": entry["document_id"]},
            ).all()
            entry.update({"status": d[0], "attempts": d[1], "dispatched_epoch": float(d[2]) if d[2] else None,
                          "claimed_epoch": float(d[3]) if d[3] else None,
                          "runs": [{"in": r[0], "out": r[1], "cost": r[2], "latency_ms": r[3], "ok": r[4]} for r in runs]})
    docs = record["documents"]
    for k, entry in enumerate(docs):
        start = entry["claimed_epoch"]
        # A document's window ends when the next one is claimed; the last one's, 15 s after its final status.
        end = docs[k + 1]["claimed_epoch"] if k + 1 < len(docs) and docs[k + 1]["claimed_epoch"] else (samples[-1][0] if samples else None)
        window = [x for x in samples if start and end and start <= x[0] <= end]
        if not window:
            print(f"{entry['file']}: {entry['status']}; no samples in its window")
            continue
        peak_used = max(x[1] for x in window)
        peak_cgroup = max(x[2] for x in window)
        peak_rss = max((max(x[3].values()) for x in window if x[3]), default=0)
        entry.update({"peak_machine_used_MiB": round(peak_used / 1024), "peak_cgroup_MiB": round(peak_cgroup / 1048576),
                      "peak_documents_process_rss_MiB": round(peak_rss / 1024), "samples": len(window)})
        cost = sum((Decimal(r["cost"]) for r in entry["runs"] if r["cost"]), Decimal("0"))
        print(f"{entry['file']}: {entry['status']}, attempts {entry['attempts']}, cost ${cost}; "
              f"peaks: documents process {entry['peak_documents_process_rss_MiB']} MiB, "
              f"machine used {entry['peak_machine_used_MiB']} MiB, cgroup {entry['peak_cgroup_MiB']} MiB ({len(window)} samples)")
    record["ended"] = now()
    save()
    print(f"== end {record['ended']}")


main()
