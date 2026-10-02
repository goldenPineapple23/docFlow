"""
A snapshot of who may use what in the database: every RLS policy on `public`
(table, name, command, roles, both expressions) and the EXECUTE grantees of
every SECURITY DEFINER function in `public` (Stage 3e; founder's Q5
condition 2).

It is the "backup" for migration 0036, which changes no rows: taken on
staging before 0036 is applied, it is what the reverse script must restore
exactly. `migration_roundtrip.py` compares against it in CI.

Read-only. Any login may run it: pg_policies and pg_proc are readable by all.

    python scripts/ci/policy_snapshot.py <database-url> > snapshot.json
"""

from __future__ import annotations

import json
import sys
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


def main() -> int:
    with psycopg.connect(sys.argv[1], prepare_threshold=None) as conn:
        json.dump(snapshot(conn), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
