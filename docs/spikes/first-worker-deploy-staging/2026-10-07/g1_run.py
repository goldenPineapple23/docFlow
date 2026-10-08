"""G1 (RUNBOOK 9.7 step 9): three uploads through the Fly API to needs_review.

Prints ids, statuses, times, token counts and cost only -- never content.
"""

from __future__ import annotations

import json
import secrets
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from docflow_core.config import get_settings
from docflow_core.db import platform_session, use_own_login
from sqlalchemy import text

REPO = Path(sys.argv[1])
OUT = Path(sys.argv[2])
API = "http://localhost:18000"
FILES = [
    ("golden fixture", REPO / "apps/api/tests/fixtures/golden/sample_po.txt", "text/plain"),
    ("legacy .doc", REPO / "apps/parse/tests/fixtures/positive/po.doc", "application/msword"),
    ("scanned image", REPO / "apps/parse/tests/fixtures/positive/po.png", "image/png"),
]
TERMINAL = {"needs_review", "failed", "quarantined", "dead_letter", "rejected"}
TIMEOUT_S = 420


def now() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3] + "Z"


def main() -> None:
    use_own_login("admin")
    s = get_settings()
    run = secrets.token_hex(3)
    tenant_id, user_id = uuid4(), uuid4()
    name = f"Acme Test G1 {run}"
    email = f"g1-{run}@example.test"
    password = "DocFlow-g1-" + secrets.token_urlsafe(16)

    admin = {"apikey": s.supabase_service_role_key, "Authorization": f"Bearer {s.supabase_service_role_key}"}
    r = httpx.post(
        f"{s.supabase_url}/auth/v1/admin/users",
        headers=admin,
        json={"email": email, "password": password, "email_confirm": True},
        timeout=30,
    )
    r.raise_for_status()
    auth_user_id = r.json()["id"]

    with platform_session() as session:
        session.execute(
            text(
                "INSERT INTO tenants (id, name, status, onboarding_status, created_at, updated_at, status_changed_at) "
                "VALUES (:id, :name, 'active', 'tenant_created', now(), now(), now())"
            ),
            {"id": str(tenant_id), "name": name},
        )
        session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, role, auth_user_id, is_active, created_at, updated_at) "
                "VALUES (:id, :t, :email, 'owner', :a, true, now(), now())"
            ),
            {"id": str(user_id), "t": str(tenant_id), "email": email, "a": auth_user_id},
        )

    record: dict = {"tenant_id": str(tenant_id), "tenant_name": name, "auth_user_id": auth_user_id,
                    "email": email, "documents": []}
    OUT.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"tenant {tenant_id} ({name}) created {now()}")

    r = httpx.post(
        f"{s.supabase_url}/auth/v1/token?grant_type=password",
        headers={"apikey": s.supabase_anon_key},
        json={"email": email, "password": password},
        timeout=30,
    )
    r.raise_for_status()
    bearer = {"Authorization": f"Bearer {r.json()['access_token']}"}
    print(f"signed in {now()}")

    for label, path, mime in FILES:
        content = path.read_bytes()
        print(f"\n== {label}: {path.name}, {len(content)} bytes")
        t0 = time.monotonic()
        up = httpx.post(
            f"{API}/documents/upload",
            headers=bearer,
            files={"file": (path.name, content, mime)},
            data={"batch_size": "1"},
            timeout=120,
        )
        t_up = time.monotonic() - t0
        print(f"   upload {now()} -> HTTP {up.status_code} in {t_up:.2f}s")
        if up.status_code != 200:
            body = up.json() if up.headers.get("content-type", "").startswith("application/json") else {}
            code = (body.get("detail") or {}).get("code") if isinstance(body.get("detail"), dict) else None
            print(f"   REFUSED, catalog code {code}; stopping")
            record["documents"].append({"label": label, "http": up.status_code, "code": code})
            OUT.write_text(json.dumps(record, indent=2), encoding="utf-8")
            sys.exit(2)
        doc_id = up.json()["document_id"]
        print(f"   document {doc_id} status {up.json()['status']}")

        last = None
        seen: list[tuple[str, float]] = []
        while True:
            with platform_session() as session:
                status = session.execute(
                    text("SELECT status FROM documents WHERE id = :id"), {"id": doc_id}
                ).scalar_one()
            el = time.monotonic() - t0
            if status != last:
                print(f"   {now()} +{el:6.1f}s  {status}")
                seen.append((status, round(el, 1)))
                last = status
            if status in TERMINAL or el > TIMEOUT_S:
                break
            time.sleep(1.5)

        with platform_session() as session:
            runs = session.execute(
                text(
                    "SELECT model_id, input_tokens, output_tokens, est_cost_usd::text, latency_ms, "
                    "succeeded, error_code FROM extraction_runs WHERE document_id = :id ORDER BY created_at"
                ),
                {"id": doc_id},
            ).all()
            warn = session.execute(
                text("SELECT count(*) FROM document_warnings WHERE document_id = :id"), {"id": doc_id}
            ).scalar_one()
            lines = session.execute(
                text("SELECT count(*) FROM document_lines WHERE document_id = :id"), {"id": doc_id}
            ).scalar_one()
        for m in runs:
            print(f"   run: model={m[0]} in={m[1]} out={m[2]} cost=${m[3]} latency_ms={m[4]} ok={m[5]} err={m[6]}")
        print(f"   final {last} after {seen[-1][1]}s; {len(runs)} run(s); {lines} line(s); {warn} check(s)")
        record["documents"].append({
            "label": label, "document_id": doc_id, "final": last, "seconds": seen[-1][1],
            "upload_seconds": round(t_up, 2), "transitions": seen,
            "runs": [dict(zip(("model_id", "in", "out", "cost", "latency_ms", "ok", "err"), m, strict=True)) for m in runs],
        })
        OUT.write_text(json.dumps(record, indent=2), encoding="utf-8")
        if last != "needs_review":
            print("   NOT needs_review; stopping")
            sys.exit(3)

    print(f"\nG1 done {now()}")


main()
