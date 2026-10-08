"""Read-only look at staging before the 500 + 1 run. Ids, counts, times and
cost only -- never a document's content."""

from __future__ import annotations

from datetime import UTC, datetime

from docflow_core.db import platform_session, use_own_login
from sqlalchemy import text

G1 = "40594983-03ff-47b1-ac40-f636488b8de7"
use_own_login("admin")
print(f"read at {datetime.now(UTC):%Y-%m-%d %H:%M:%S}Z")
with platform_session() as s:
    print("\ncurrent tiers:")
    for r in s.execute(text("SELECT id, name, document_allowance FROM tiers WHERE is_current ORDER BY document_allowance")):
        print("  ", r[0], r[1], r[2])

    print("\ndo the stamps survive needs_review? (G1 tenant, newest 5)")
    for r in s.execute(
        text(
            "SELECT id, status, dispatch_lane, created_at, dispatched_at, processing_started_at "
            "FROM documents WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 5"
        ),
        {"t": G1},
    ):
        print("  ", str(r[0])[:8], r[1], r[2], r[3], r[4], r[5])

    print("\nG1 tenant by status:")
    for r in s.execute(text("SELECT status, count(*) FROM documents WHERE tenant_id = :t GROUP BY status"), {"t": G1}):
        print("  ", r[0], r[1])

    print("\nnot finished, any tenant (pending / processing):")
    rows = s.execute(
        text("SELECT tenant_id, status, count(*) FROM documents WHERE status IN ('pending','processing') "
             "AND deleted_at IS NULL GROUP BY tenant_id, status")
    ).all()
    print("  ", rows or "none")

    print("\nmodel spend recorded, finished runs, by UTC day (last 3 days):")
    for r in s.execute(
        text("SELECT (created_at AT TIME ZONE 'UTC')::date AS day, count(*), sum(est_cost_usd)::text "
             "FROM extraction_runs WHERE est_cost_usd IS NOT NULL AND created_at > now() - interval '3 days' "
             "GROUP BY 1 ORDER BY 1")
    ):
        print("  ", r[0], r[1], "runs", "$" + str(r[2]))

    print("\nprovider state:")
    from docflow_core import model_provider

    for r in s.execute(text("SELECT status FROM public.provider_state(:p)"), {"p": model_provider.PROVIDER}):
        print("  ", model_provider.PROVIDER, r[0])

    print("\nfounder_alerts not acknowledged, newest 6:")
    for r in s.execute(
        text("SELECT type, severity, created_at, tenant_id FROM founder_alerts WHERE acknowledged_at IS NULL "
             "ORDER BY created_at DESC LIMIT 6")
    ):
        print("  ", r[0], r[1], r[2], str(r[3])[:8] if r[3] else None)
