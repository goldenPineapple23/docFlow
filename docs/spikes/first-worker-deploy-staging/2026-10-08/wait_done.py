"""Read-only: exit when the backfill's tenant has at least N documents out of
pending/processing. Prints the count and the time; nothing else."""

import json
import sys
import time
from datetime import UTC, datetime

from docflow_core.db import platform_session, use_own_login
from sqlalchemy import text

T = json.load(open(sys.argv[1], encoding="utf-8"))["tenant_a"]
N = int(sys.argv[2])
use_own_login("admin")
while True:
    with platform_session() as s:
        rows = dict(s.execute(text("SELECT status, count(*) FROM documents WHERE tenant_id = :t GROUP BY status"), {"t": T}).all())
    done = sum(v for k, v in rows.items() if k not in ("pending", "processing"))
    if done >= N:
        print(f"{datetime.now(UTC):%H:%M:%S}Z done {done} (mark {N}); by status {rows}")
        break
    time.sleep(15)
