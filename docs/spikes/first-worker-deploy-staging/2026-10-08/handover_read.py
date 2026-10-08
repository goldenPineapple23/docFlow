"""Read-only: the upload pause at the watcher handover, from the documents' own
created_at stamps (database clock). Counts and times only."""

import json
import sys

from docflow_core.db import platform_session, use_own_login
from sqlalchemy import text

T = json.load(open(sys.argv[1], encoding="utf-8"))["tenant_a"]
use_own_login("admin")
with platform_session() as s:
    print("database clock", s.execute(text("SELECT now()")).scalar_one())
    print("by status", s.execute(text("SELECT status, count(*) FROM documents WHERE tenant_id = :t GROUP BY status"), {"t": T}).all())
    print("uploaded", s.execute(text("SELECT count(*), count(DISTINCT original_filename), count(*) FILTER (WHERE is_possible_duplicate) "
                                     "FROM documents WHERE tenant_id = :t"), {"t": T}).one(), "(rows, distinct names, possible duplicates)")
    print("three longest gaps between consecutive uploads (seconds, upload before the gap, upload after):")
    for r in s.execute(text(
        "SELECT EXTRACT(EPOCH FROM created_at - prev), prev, created_at FROM ("
        "SELECT created_at, lag(created_at) OVER (ORDER BY created_at) AS prev FROM documents WHERE tenant_id = :t) x "
        "WHERE prev IS NOT NULL ORDER BY 1 DESC LIMIT 3"), {"t": T}):
        print(f"   {float(r[0]):.1f}s  {r[1]}  ->  {r[2]}")
    print("median gap", s.execute(text(
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY g) FROM (SELECT EXTRACT(EPOCH FROM created_at - "
        "lag(created_at) OVER (ORDER BY created_at)) AS g FROM documents WHERE tenant_id = :t) x WHERE g IS NOT NULL"), {"t": T}).scalar_one())
