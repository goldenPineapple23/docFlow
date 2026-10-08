"""Drill 4 (RUNBOOK 9.7 step 12.4): upload one small fictional order while the
documents worker is paused. Prints ids, statuses and times only."""

from __future__ import annotations

import secrets
import subprocess
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from docflow_core.config import get_settings
from docflow_core.db import platform_session, use_own_login
from sqlalchemy import text as sql

sys.path.insert(0, str(Path(__file__).parent))
from g3_busy_hour import API_APP, API_MACHINE, TENANT_ID, build_order  # noqa: E402

API = "http://127.0.0.1:18000"


def now() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3] + "Z"


def ok() -> bool:
    try:
        return httpx.get(f"{API}/healthz", timeout=4).status_code == 200
    except httpx.HTTPError:
        return False


use_own_login("admin")
s = get_settings()
fly = shutil.which("fly") or shutil.which("flyctl")
subprocess.run([fly, "machine", "stop", API_MACHINE, "--app", API_APP], capture_output=True, timeout=90)
time.sleep(4)
subprocess.run([fly, "machine", "start", API_MACHINE, "--app", API_APP], capture_output=True, timeout=90)
t = time.monotonic()
while not ok():
    if time.monotonic() - t > 120:
        sys.exit("the API did not answer; nothing uploaded")
    time.sleep(0.5)

with platform_session() as session:
    email, auth_user_id = session.execute(
        sql("SELECT email, auth_user_id FROM users WHERE tenant_id = :t AND role = 'owner'"), {"t": TENANT_ID}).one()
password = "DocFlow-d4-" + secrets.token_urlsafe(16)
admin = {"apikey": s.supabase_service_role_key, "Authorization": f"Bearer {s.supabase_service_role_key}"}
httpx.put(f"{s.supabase_url}/auth/v1/admin/users/{auth_user_id}", headers=admin,
          json={"password": password}, timeout=30).raise_for_status()
r = httpx.post(f"{s.supabase_url}/auth/v1/token?grant_type=password", headers={"apikey": s.supabase_anon_key},
               json={"email": email, "password": password}, timeout=30)
r.raise_for_status()
fname, text, _ = build_order(19, datetime.now(UTC).date())  # G3-...-20, the order that never arrived
up = httpx.post(f"{API}/documents/upload", headers={"Authorization": f"Bearer {r.json()['access_token']}"},
                files={"file": (fname, text.encode(), "text/plain")}, data={"batch_size": "1"}, timeout=120)
print(f"{now()} upload {fname} -> HTTP {up.status_code}")
if up.status_code != 200:
    sys.exit(2)
doc_id = up.json()["document_id"]
print(f"document {doc_id} status {up.json()['status']}")
Path(sys.argv[1]).write_text(doc_id, encoding="utf-8")
for _ in range(8):
    time.sleep(5)
    with platform_session() as session:
        row = session.execute(sql("SELECT to_jsonb(d) - 'raw_json' FROM documents d WHERE id = :i"), {"i": doc_id}).scalar_one()
    print(f"{now()} " + ", ".join(f"{k}={row.get(k)}" for k in row if k in (
        "status", "dispatched_at", "claimed_at", "processing_started_at", "processing_attempts", "dispatch_lane")))
