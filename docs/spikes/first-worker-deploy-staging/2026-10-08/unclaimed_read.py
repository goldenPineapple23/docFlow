"""Read-only: unclaimed age (claim minus dispatch) of the backfill's documents
around a given time, and the run's figures so far. Names, times and seconds only.

    python unclaimed_read.py <state.json> "<UTC time, e.g. 2026-10-08 15:56:51.8+00>" [seconds either side]
"""

import json
import sys

from docflow_core.db import platform_session, use_own_login
from sqlalchemy import text

A = json.load(open(sys.argv[1], encoding="utf-8"))["tenant_a"]
AT = sys.argv[2]
SIDE = int(sys.argv[3]) if len(sys.argv) > 3 else 30
use_own_login("admin")
with platform_session() as s:
    print(f"documents dispatched within {SIDE}s of {AT}:")
    for r in s.execute(text(
        "SELECT original_filename, dispatched_at, processing_started_at, processed_at, "
        "EXTRACT(EPOCH FROM processing_started_at - dispatched_at), EXTRACT(EPOCH FROM processed_at - processing_started_at) "
        "FROM documents WHERE tenant_id = :a AND dispatched_at BETWEEN CAST(:at AS timestamptz) - make_interval(secs => :s) "
        "AND CAST(:at AS timestamptz) + make_interval(secs => :s) ORDER BY dispatched_at"), {"a": A, "at": AT, "s": SIDE}):
        print(f"   {r[0]}  dispatched {r[1]:%H:%M:%S.%f}  claimed {r[2]:%H:%M:%S.%f}  unclaimed {float(r[4]):6.2f}s  "
              f"read {float(r[5]):5.2f}s" if r[2] and r[3] else f"   {r[0]}  dispatched {r[1]:%H:%M:%S.%f}  not finished")
    row = s.execute(text(
        "SELECT count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY u), percentile_cont(0.95) WITHIN GROUP (ORDER BY u), max(u) "
        "FROM (SELECT EXTRACT(EPOCH FROM processing_started_at - dispatched_at) AS u FROM documents "
        "WHERE tenant_id = :a AND processing_started_at IS NOT NULL AND dispatched_at IS NOT NULL) x"), {"a": A}).one()
    print(f"\nso far, {row[0]} claimed documents: unclaimed age median {float(row[1]):.3f}s, p95 {float(row[2]):.3f}s, largest {float(row[3]):.3f}s")
    print("eight largest unclaimed ages so far:")
    for r in s.execute(text(
        "SELECT original_filename, dispatched_at, EXTRACT(EPOCH FROM processing_started_at - dispatched_at) FROM documents "
        "WHERE tenant_id = :a AND processing_started_at IS NOT NULL AND dispatched_at IS NOT NULL ORDER BY 3 DESC LIMIT 8"), {"a": A}):
        print(f"   {float(r[2]):6.2f}s  {r[0]}  dispatched {r[1]:%H:%M:%S.%f}")
