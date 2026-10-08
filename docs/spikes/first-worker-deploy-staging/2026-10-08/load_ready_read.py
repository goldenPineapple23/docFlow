"""Read-only: is the backfill's tenant ready (catalog committed), and is staging quiet?"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

from docflow_core.db import platform_session, use_own_login
from sqlalchemy import text

T = json.load(open(sys.argv[1], encoding="utf-8"))["tenant_a"]
use_own_login("admin")
print(f"read at {datetime.now(UTC):%H:%M:%S}Z, tenant {T}")
with platform_session() as s:
    for r in s.execute(
        text("SELECT id, status, row_count, original_filename, file_sha256, created_at, committed_at "
             "FROM catalog_imports WHERE tenant_id = :t ORDER BY created_at"), {"t": T}
    ):
        print("  import", str(r[0])[:8], r[1], "rows", r[2], r[3], str(r[4])[:12], "created", r[5], "committed", r[6])
    items = s.execute(
        text("SELECT count(*), count(*) FILTER (WHERE deleted_at IS NULL) FROM items WHERE tenant_id = :t"), {"t": T}
    ).one()
    print(f"  items: total {items[0]}, live {items[1]}")
    t = s.execute(text("SELECT onboarding_status, status, tier_id FROM tenants WHERE id = :t"), {"t": T}).one()
    print(f"  tenant: onboarding_status={t[0]} status={t[1]} tier={t[2]}")
    busy = s.execute(
        text("SELECT count(*) FROM documents WHERE status IN ('pending','processing') AND deleted_at IS NULL")
    ).scalar_one()
    print(f"  pending or processing on staging: {busy}")
