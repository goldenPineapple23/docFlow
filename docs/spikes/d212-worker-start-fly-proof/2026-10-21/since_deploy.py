"""Read-only: what the staging worker wrote since the deploy (2026-10-21
16:36:00Z). Alerts by type/severity/time; e-mails by template/status and
whether the address is the founder's (a yes/no, never the address); model
runs; worker starts; the six counts. Run from apps/api with its venv."""

import os
import sys

from docflow_core.db import platform_session
from sqlalchemy import text

SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-10-21 16:36:00+00"
print("since", SINCE)
founder = os.environ.get("FOUNDER_ALERT_EMAIL", "")
if not founder:
    for line in open("../../.env", encoding="utf-8"):
        if line.startswith("FOUNDER_ALERT_EMAIL="):
            founder = line.split("=", 1)[1].strip().strip('"')

with platform_session() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    print("transaction_read_only:", s.execute(text("SHOW transaction_read_only")).scalar_one())
    print("read at", s.execute(text("SELECT now()::text")).scalar_one())
    rows = s.execute(text(
        "SELECT created_at::text, type, severity, coalesce(split_part(dedupe_key, ':', 1), '-') "
        "FROM founder_alerts WHERE created_at >= :t ORDER BY created_at"), {"t": SINCE}).all()
    print(f"alerts since deploy: {len(rows)}")
    for r in rows:
        print("  ", " | ".join(r))
    rows = s.execute(text(
        "SELECT template, status, (lower(to_address) = lower(:f)) AS to_founder, count(*) "
        "FROM email_outbox WHERE created_at >= :t GROUP BY 1, 2, 3 ORDER BY 1"),
        {"t": SINCE, "f": founder}).all()
    print(f"e-mail rows since deploy: {sum(r[3] for r in rows)} (founder address known: {bool(founder)})")
    for r in rows:
        print(f"   {r[0]} | {r[1]} | to founder: {r[2]} | {r[3]}")
    for label, q in (
        ("extraction runs since deploy", "SELECT count(*) FROM extraction_runs WHERE created_at >= :t"),
        ("documents updated since deploy", "SELECT count(*) FROM documents WHERE updated_at >= :t"),
        ("tenants updated since deploy", "SELECT count(*) FROM tenants WHERE updated_at >= :t"),
    ):
        sp = s.begin_nested()
        try:
            print(f"{label}: {s.execute(text(q), {'t': SINCE}).scalar_one()}")
            sp.commit()
        except Exception as exc:  # noqa: BLE001
            sp.rollback()
            print(f"{label}: NOT READ ({type(exc).__name__})")
    print("worker starts last hour:", s.execute(text("SELECT public.worker_starts_last_hour()")).scalar_one())
    print("dispatcher:", s.execute(text(
        "SELECT last_pass_at::text || ' | waiting ' || waiting FROM public.dispatcher_status()")).scalar_one())
    for label, q in (
        ("tenants", "SELECT count(*) FROM tenants"),
        ("founder alerts", "SELECT count(*) FROM founder_alerts"),
        ("e-mails", "SELECT count(*) FROM email_outbox"),
        ("scheduled jobs", "SELECT count(*) FROM scheduled_jobs"),
        ("platform-admin rows", "SELECT count(*) FROM platform_admins"),
        ("users with no tenant", "SELECT count(*) FROM users WHERE tenant_id IS NULL"),
    ):
        print(f"C {label}: {s.execute(text(q)).scalar_one()}")
    s.rollback()
