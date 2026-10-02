"""
Migration 0036, forward -> reverse -> forward, on the CI database (Stage 3e;
founder's Q5 condition 2: "the reverse script is tested, not just checked in").

Run after `supabase start` has applied every migration, 0036 included:
  1. snapshot the forward state;
  2. run supabase/reverse/0036_reverse.sql; the snapshot must equal
     supabase/reverse/0036_pre_snapshot.json (staging before 0036, read
     2026-10-02), so the reverse restores exactly what was there;
  3. run 0036 again; the snapshot must equal step 1's.

docflow_app is created NOLOGIN first if it doesn't exist, because the
pre-0036 state grants it EXECUTE and the reverse grants it back.

CI only. Prints each difference and exits 1 on any.

Environment:
  SUPERUSER_DATABASE_URL  the local stack's `postgres` connection string
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy_snapshot import snapshot

ROOT = Path(__file__).resolve().parents[2]
FORWARD = ROOT / "supabase" / "migrations" / "0036_separate_logins.sql"
REVERSE = ROOT / "supabase" / "reverse" / "0036_reverse.sql"
PRE = ROOT / "supabase" / "reverse" / "0036_pre_snapshot.json"


def _run(url: str, path: Path) -> None:
    with psycopg.connect(url) as conn:  # one transaction, committed on exit
        conn.execute(path.read_text(encoding="utf-8"))


def _take(url: str) -> dict[str, Any]:
    with psycopg.connect(url) as conn:
        return snapshot(conn)


def _differences(label: str, got: dict[str, Any], want: dict[str, Any]) -> list[str]:
    out: list[str] = []
    key = lambda p: (p["table"], p["name"])
    got_p = {key(p): p for p in got["policies"]}
    want_p = {key(p): p for p in want["policies"]}
    for k in sorted(set(got_p) | set(want_p)):
        if got_p.get(k) != want_p.get(k):
            out.append(f"{label}: policy {k[0]}.{k[1]}: got {got_p.get(k)} want {want_p.get(k)}")
    got_f, want_f = got["security_definer_functions"], want["security_definer_functions"]
    for k in sorted(set(got_f) | set(want_f)):
        if got_f.get(k) != want_f.get(k):
            out.append(f"{label}: function {k}: got {got_f.get(k)} want {want_f.get(k)}")
    return out


def main() -> int:
    url = os.environ["SUPERUSER_DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'docflow_app'").fetchone() is None:
            conn.execute("CREATE ROLE docflow_app NOLOGIN NOBYPASSRLS")

    forward = _take(url)
    _run(url, REVERSE)
    reversed_ = _take(url)
    _run(url, FORWARD)
    again = _take(url)

    pre = json.loads(PRE.read_text(encoding="utf-8"))
    problems = _differences("reverse vs staging pre-0036", reversed_, pre)
    problems += _differences("forward again vs forward", again, forward)
    for line in problems:
        print(line)
    print(
        f"migration_roundtrip: forward {len(forward['policies'])} policies, "
        f"reverse {len(reversed_['policies'])}, forward again {len(again['policies'])}; "
        f"{len(problems)} difference(s)"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
