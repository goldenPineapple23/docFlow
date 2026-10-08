"""Read-only: the G2 catalog import's state for the G1 test tenant. Counts, statuses, times only."""

from __future__ import annotations

import json
import sys

from docflow_core.db import platform_session, use_own_login
from sqlalchemy import text

T = sys.argv[1]
use_own_login("admin")
with platform_session() as s:
    imports = [r[0] for r in s.execute(
        text("SELECT to_jsonb(i) FROM catalog_imports i WHERE tenant_id = :t ORDER BY created_at"), {"t": T})]
    print(f"catalog_imports rows: {len(imports)}")
    for i in imports:
        small = {}
        for k, v in i.items():
            if isinstance(v, (dict, list)):
                small[k] = f"<{type(v).__name__} len {len(v)}>"
            elif isinstance(v, str) and len(v) > 80:
                small[k] = v[:12] + "..."
            else:
                small[k] = v
        print(json.dumps(small, indent=1, default=str))
        for k in ("report", "summary", "counts", "validation_report"):
            if isinstance(i.get(k), (dict, list)):
                print(f"{k}: {json.dumps(i[k], default=str)[:1500]}")
    items = s.execute(
        text("SELECT count(*), count(*) FILTER (WHERE deleted_at IS NULL), min(created_at), max(created_at) "
             "FROM items WHERE tenant_id = :t"), {"t": T}).one()
    print(f"items: total {items[0]}, live {items[1]}, created {items[2]} .. {items[3]}")
    tenant = s.execute(text("SELECT onboarding_status, status FROM tenants WHERE id = :t"), {"t": T}).one()
    print(f"tenant: onboarding_status={tenant[0]} status={tenant[1]}")
    acts = s.execute(
        text("SELECT to_jsonb(a) FROM admin_actions a WHERE created_at > now() - interval '3 hours' "
             "ORDER BY created_at")).all()
    print(f"admin_actions in the last 3 hours: {len(acts)}")
    for (a,) in acts:
        print("  " + json.dumps({k: a.get(k) for k in ("created_at", "action", "action_type", "target_type",
                                                    "acting_as_tenant_id", "tenant_id") if k in a}, default=str))
    alerts = s.execute(
        text("SELECT type, severity, created_at FROM founder_alerts WHERE created_at > now() - interval '3 hours' "
             "ORDER BY created_at")).all()
    print(f"founder_alerts in the last 3 hours: {len(alerts)}")
    for a in alerts:
        print(f"  {a[2]} {a[0]} {a[1]}")
