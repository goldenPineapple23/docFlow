"""Read-only: the + 1's stamps and the order mark, from the database."""

import json
import sys

from docflow_core.db import platform_session, use_own_login
from sqlalchemy import text

state = json.load(open(sys.argv[1], encoding="utf-8"))
result = json.load(open(sys.argv[2], encoding="utf-8"))
A, one_id = state["tenant_a"], result["plus_one"]["document_id"]
use_own_login("admin")
with platform_session() as s:
    one = s.execute(text(
        "SELECT status, dispatch_lane, processing_attempts, created_at, dispatched_at, processing_started_at, processed_at, "
        "EXTRACT(EPOCH FROM dispatched_at - created_at), EXTRACT(EPOCH FROM processing_started_at - dispatched_at), "
        "EXTRACT(EPOCH FROM processed_at - processing_started_at), EXTRACT(EPOCH FROM processed_at - created_at) "
        "FROM documents WHERE id = :i"), {"i": one_id}).one()
    print(f"+ 1 {one_id}: {one[0]}, lane {one[1]}, attempts {one[2]}")
    print(f"  saved      {one[3]}")
    print(f"  dispatched {one[4]}   (+{float(one[7]):.2f}s waiting for the slot)")
    print(f"  claimed    {one[5]}   (+{float(one[8]):.2f}s unclaimed)")
    print(f"  processed  {one[6]}   (+{float(one[9]):.2f}s being read)")
    print(f"  save to processed: {float(one[10]):.2f}s")
    ahead = s.execute(text(
        "SELECT count(*) FROM documents WHERE tenant_id = :a AND processing_started_at > :c AND processing_started_at < :p"),
        {"a": A, "c": one[3], "p": one[5]}).scalar_one()
    print(f"  backfill documents claimed between its save and its claim: {ahead}")
    print("  backfill claims around it:")
    for r in s.execute(text(
        "SELECT original_filename, dispatched_at, processing_started_at, processed_at FROM documents WHERE tenant_id = :a "
        "AND processing_started_at BETWEEN :lo AND :hi ORDER BY processing_started_at"),
        {"a": A, "lo": one[3].replace(second=max(0, one[3].second - 20)) if one[3].second >= 20 else one[3], "hi": one[6]}):
        print(f"     {r[0]}  dispatched {r[1]:%H:%M:%S.%f}  claimed {r[2]:%H:%M:%S.%f}  processed {r[3] and format(r[3], '%H:%M:%S.%f')}")
    before = s.execute(text(
        "SELECT original_filename, processing_started_at, processed_at FROM documents WHERE tenant_id = :a "
        "AND processing_started_at < :c ORDER BY processing_started_at DESC LIMIT 1"), {"a": A, "c": one[3]}).one()
    print(f"  in flight when it was saved: {before[0]} claimed {before[1]:%H:%M:%S.%f} processed {before[2] and format(before[2], '%H:%M:%S.%f')}")
    after = s.execute(text(
        "SELECT original_filename, dispatched_at, processing_started_at FROM documents WHERE tenant_id = :a "
        "AND processing_started_at > :p ORDER BY processing_started_at LIMIT 1"), {"a": A, "p": one[5]}).first()
    print(f"  next backfill claim after it: {after and (after[0], format(after[1], '%H:%M:%S.%f'), format(after[2], '%H:%M:%S.%f'))}")
