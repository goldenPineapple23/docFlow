"""
A snapshot of who may use what in the database: every RLS policy on `public`
(table, name, command, roles, both expressions) and the EXECUTE grantees of
every SECURITY DEFINER function in `public` (Stage 3e; founder's Q5
condition 2).

It is the "backup" for migration 0036, which changes no rows: taken on
staging before 0036 is applied, it is what the reverse script must restore
exactly. `migration_roundtrip.py` compares against it in CI, and against
the state after 0036 (`supabase/reverse/0036_post_snapshot.json`), which is
what a database must show once 0036 is applied (RUNBOOK 10.2 step 5).

Read-only. Any login may run it: pg_policies and pg_proc are readable by all.

    python scripts/ci/policy_snapshot.py <database-url> > snapshot.json
    python scripts/ci/policy_snapshot.py --sha256 <snapshot.json> [<snapshot.json> ...]
    python scripts/ci/policy_snapshot.py --diff <got.json> <want.json>

Two snapshots are the same state exactly when their SHA-256s are equal. The
hash is taken over the canonical text (the form this script writes), not
over a file's bytes, so a checkout's line endings can't change it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import psycopg

# The function owner's own grant is not a decision anyone made.
_OWNERS = {"postgres", "supabase_admin"}


def snapshot(conn: psycopg.Connection) -> dict[str, Any]:
    policies = [
        {
            "table": t,
            "name": n,
            "cmd": cmd,
            "roles": sorted(roles),
            "using": qual,
            "with_check": check,
        }
        for t, n, cmd, roles, qual, check in conn.execute(
            "SELECT tablename, policyname, cmd, roles::text[], qual, with_check "
            "FROM pg_policies WHERE schemaname = 'public' ORDER BY tablename, policyname"
        ).fetchall()
    ]
    functions: dict[str, list[str]] = {}
    for signature, grantee in conn.execute(
        "SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')', "
        "       CASE a.grantee WHEN 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        # A NULL ACL means the default: the owner and PUBLIC.
        "LEFT JOIN LATERAL aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
        "       ON a.privilege_type = 'EXECUTE' "
        "WHERE n.nspname = 'public' AND p.prosecdef"
    ).fetchall():
        grantees = functions.setdefault(signature, [])
        if grantee is not None and grantee not in _OWNERS:
            grantees.append(grantee)
    return {
        "policies": policies,
        "security_definer_functions": {k: sorted(v) for k, v in sorted(functions.items())},
    }


def canonical(state: dict[str, Any]) -> str:
    """The one text form of a snapshot: what this script writes to a file."""
    return json.dumps(state, indent=2, sort_keys=True) + "\n"


def sha256(state: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(state).encode("utf-8")).hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def differences(label: str, got: dict[str, Any], want: dict[str, Any]) -> list[str]:
    """One line per policy or function that differs; empty when the two are the same state."""
    out: list[str] = []

    def key(policy: dict[str, Any]) -> tuple[str, str]:
        return (policy["table"], policy["name"])

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


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "--sha256":
        for name in argv[2:]:
            print(f"{sha256(load(Path(name)))}  {name}")
        return 0
    if len(argv) == 4 and argv[1] == "--diff":
        problems = differences("got vs want", load(Path(argv[2])), load(Path(argv[3])))
        for line in problems:
            print(line)
        print(f"{len(problems)} difference(s)")
        return 1 if problems else 0
    if len(argv) != 2 or argv[1].startswith("--"):
        print(__doc__, file=sys.stderr)
        return 2
    with psycopg.connect(argv[1], prepare_threshold=None) as conn:
        sys.stdout.write(canonical(snapshot(conn)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
