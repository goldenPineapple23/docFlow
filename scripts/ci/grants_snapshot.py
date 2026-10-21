"""
A snapshot of every grant in `public` that migration 0039 changes, and the
neighbours it must leave alone (Phase 5.5 Stage 5, M7; DECISIONS.md D-214):

- every RLS policy on `public` (as scripts/ci/policy_snapshot.py reads them);
- every function in `public`: SECURITY DEFINER or not, its settings
  (`search_path`), a SHA-256 of its body (CRLF read as LF), and who may
  EXECUTE it;
- every table and sequence in `public`: who holds which privilege;
- the default privileges `postgres` and `supabase_admin` hold in `public`
  and in every schema: what a NEW table, sequence or function gets.

policy_snapshot.py stays as it is: 0036's two committed states are in its
form, and CI compares against them. This one is wider because 0039 changes
table, sequence and default privileges and non-definer functions, which that
snapshot never read.

The owners' own grants are left out (`postgres`, `supabase_admin`): they are
not a decision anyone made, and they name whichever role ran the migrations.

Read-only (the connection is set read-only before the first query). Any login
may run it: the catalogs are readable by every role.

    python scripts/ci/grants_snapshot.py <database-url> > snapshot.json
    python scripts/ci/grants_snapshot.py --sha256 <snapshot.json> [<snapshot.json> ...]
    python scripts/ci/grants_snapshot.py --diff <got.json> <want.json>

Two snapshots are the same state exactly when their SHA-256s are equal. The
hash is taken over the canonical text, never over a file's bytes.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

# The owners' own grants (see the module docstring).
_OWNERS = {"postgres", "supabase_admin"}
# Whose default privileges are read: the role that runs DocFlow's migrations
# in the SQL Editor, and the platform's own.
_GRANTORS = ("postgres", "supabase_admin")

_POLICIES_SQL = (
    "SELECT tablename, policyname, cmd, roles::text[], qual, with_check "
    "FROM pg_policies WHERE schemaname = 'public' ORDER BY tablename, policyname"
)
_FUNCTIONS_SQL = """
    SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
           p.prosecdef,
           coalesce(p.proconfig, '{}'::text[]),
           p.prosrc,
           ARRAY(
               SELECT CASE a.grantee WHEN 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END
               FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
               WHERE a.privilege_type = 'EXECUTE'
           )::text[]
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
"""
_RELATIONS_SQL = """
    SELECT c.relname, c.relkind,
           ARRAY(
               SELECT (CASE a.grantee WHEN 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END)
                      || '=' || a.privilege_type
               FROM aclexplode(coalesce(c.relacl, acldefault(
                   CASE WHEN c.relkind = 'S' THEN 's' ELSE 'r' END::"char", c.relowner))) a
           )::text[]
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
"""
_DEFAULTS_SQL = """
    SELECT pg_get_userbyid(d.defaclrole), coalesce(n.nspname, '(every schema)'), d.defaclobjtype,
           ARRAY(
               SELECT (CASE a.grantee WHEN 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END)
                      || '=' || a.privilege_type
               FROM aclexplode(d.defaclacl) a
           )::text[]
    FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
    WHERE pg_get_userbyid(d.defaclrole) = ANY(%s)
      AND (n.nspname = 'public' OR d.defaclnamespace = 0)
"""

# pg_default_acl's object-type letters, spelled out.
_OBJECT_TYPES = {"r": "tables", "S": "sequences", "f": "functions", "T": "types", "n": "schemas"}


def _grouped(entries: list[str]) -> dict[str, list[str]]:
    """["anon=SELECT", "anon=UPDATE", ...] -> {"anon": ["SELECT", "UPDATE"]},
    the owners' own entries left out."""
    out: dict[str, list[str]] = {}
    for entry in entries:
        grantee, privilege = entry.split("=", 1)
        if grantee in _OWNERS:
            continue
        out.setdefault(grantee, []).append(privilege)
    return {grantee: sorted(set(privileges)) for grantee, privileges in sorted(out.items())}


def snapshot(conn: Any) -> dict[str, Any]:
    policies = [
        {"table": t, "name": n, "cmd": cmd, "roles": sorted(roles), "using": qual, "with_check": check}
        for t, n, cmd, roles, qual, check in conn.execute(_POLICIES_SQL).fetchall()
    ]
    functions = {
        signature: {
            "security_definer": bool(definer),
            "config": sorted(config),
            # Line endings normalised: staging's bodies were pasted into the
            # SQL Editor from a Windows checkout (CRLF), CI's come from Linux.
            "body_sha256": hashlib.sha256(source.replace("\r\n", "\n").encode("utf-8")).hexdigest(),
            "execute": sorted(g for g in set(grantees) if g not in _OWNERS),
        }
        for signature, definer, config, source, grantees in conn.execute(_FUNCTIONS_SQL).fetchall()
    }
    relations = {
        name: {"kind": "sequence" if kind == "S" else "table", "grants": _grouped(list(entries))}
        for name, kind, entries in conn.execute(_RELATIONS_SQL).fetchall()
    }
    defaults = {
        f"{grantor} in {schema}: {_OBJECT_TYPES.get(kind, kind)}": _grouped(list(entries))
        for grantor, schema, kind, entries in conn.execute(_DEFAULTS_SQL, (list(_GRANTORS),)).fetchall()
    }
    return {
        "policies": policies,
        "functions": dict(sorted(functions.items())),
        "relations": dict(sorted(relations.items())),
        "default_privileges": dict(sorted(defaults.items())),
    }


def canonical(state: dict[str, Any]) -> str:
    """The one text form of a snapshot: what this script writes to a file."""
    return json.dumps(state, indent=1, sort_keys=True) + "\n"


def sha256(state: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(state).encode("utf-8")).hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def differences(label: str, got: dict[str, Any], want: dict[str, Any]) -> list[str]:
    """One line per policy, function, relation or default that differs."""
    out: list[str] = []

    def by_key(policies: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        return {(p["table"], p["name"]): p for p in policies}

    got_p, want_p = by_key(got["policies"]), by_key(want["policies"])
    for k in sorted(set(got_p) | set(want_p)):
        if got_p.get(k) != want_p.get(k):
            out.append(f"{label}: policy {k[0]}.{k[1]}: got {got_p.get(k)} want {want_p.get(k)}")
    for part in ("functions", "relations", "default_privileges"):
        g, w = got[part], want[part]
        for k in sorted(set(g) | set(w)):
            if g.get(k) != w.get(k):
                out.append(f"{label}: {part} {k}: got {g.get(k)} want {w.get(k)}")
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
    import psycopg

    with psycopg.connect(argv[1], prepare_threshold=None) as conn:
        conn.read_only = True
        sys.stdout.write(canonical(snapshot(conn)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
