"""
Migration 0037, forward -> reverse -> forward, on the CI database (Stage 4,
PR 1a; D-203: "a reverse script and the CI round trip as for 0036").

Run after `supabase start` has applied every migration, 0037 included:
  1. read the forward state: the columns and indexes of founder_alerts and
     documents. It must hold 0037's two columns and two indexes, each as
     0037 declares it (EXPECTED below);
  2. run supabase/reverse/0037_reverse.sql. The four objects must be gone,
     and every other column and index of both tables must be exactly as it
     was: the reverse removes what 0037 added and nothing else;
  3. run 0037 again. The state must equal step 1's.
Across all three the policies and the function grants must not change
(scripts/ci/policy_snapshot.py): 0037 and its reverse touch neither.

CI only. Prints each problem and exits 1 on any. The forward state's SHA-256
is a GitHub annotation on every run.

Environment:
  SUPERUSER_DATABASE_URL  the local stack's `postgres` connection string
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[2]
FORWARD = ROOT / "supabase" / "migrations" / "0037_alert_state_key.sql"
REVERSE = ROOT / "supabase" / "reverse" / "0037_reverse.sql"

# The two tables 0037 touches; named in the two queries below.
TABLES = ("documents", "founder_alerts")

# What 0037 adds, as the database must show it.
# column: (data_type, udt_name, is_nullable)
EXPECTED_COLUMNS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("founder_alerts", "state_key"): ("text", "text", "YES"),
    ("documents", "fault_keys"): ("ARRAY", "_text", "YES"),
}
EXPECTED_INDEXES: dict[str, dict[str, Any]] = {
    # Unique over every row: no predicate, so an acknowledged alert still
    # holds its key (the point of the migration).
    "idx_founder_alerts_state_key": {
        "table": "founder_alerts",
        "unique": True,
        "method": "btree",
        "columns": ["state_key"],
        "partial": False,
    },
    "idx_documents_fault_keys": {
        "table": "documents",
        "unique": False,
        "method": "gin",
        "columns": ["fault_keys"],
        "partial": True,
    },
}

_COLUMNS_SQL = """
    SELECT table_name, column_name, data_type, udt_name, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name IN ('documents', 'founder_alerts')
    ORDER BY table_name, ordinal_position
"""
_INDEXES_SQL = """
    SELECT c.relname, t.relname, i.indisunique, am.amname,
           pg_get_expr(i.indpred, i.indrelid),
           ARRAY(
               SELECT a.attname
               FROM unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
               JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
               ORDER BY k.ord
           )
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    JOIN pg_class t ON t.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    JOIN pg_am am ON am.oid = c.relam
    WHERE n.nspname = 'public' AND t.relname IN ('documents', 'founder_alerts')
    ORDER BY c.relname
"""


def read_state(conn: Any) -> dict[str, Any]:
    """The columns and indexes of the two tables 0037 touches. Read-only."""
    columns = [
        {
            "table": row[0],
            "column": row[1],
            "data_type": row[2],
            "udt_name": row[3],
            "is_nullable": row[4],
            "default": row[5],
        }
        for row in conn.execute(_COLUMNS_SQL).fetchall()
    ]
    indexes = {
        row[0]: {
            "table": row[1],
            "unique": bool(row[2]),
            "method": row[3],
            "predicate": row[4],
            "columns": list(row[5]),
        }
        for row in conn.execute(_INDEXES_SQL).fetchall()
    }
    return {"columns": columns, "indexes": indexes}


def canonical(state: dict[str, Any]) -> str:
    return json.dumps(state, indent=1, sort_keys=True) + "\n"


def sha256(state: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(state).encode("utf-8")).hexdigest()


def _column(state: dict[str, Any], table: str, column: str) -> dict[str, Any] | None:
    return next((c for c in state["columns"] if c["table"] == table and c["column"] == column), None)


def check_forward(state: dict[str, Any]) -> list[str]:
    """Problems with the state after 0037: each of its four objects must be
    there, as declared."""
    problems = []
    for (table, column), (data_type, udt_name, nullable) in EXPECTED_COLUMNS.items():
        found = _column(state, table, column)
        if found is None:
            problems.append(f"forward: {table}.{column} is missing")
            continue
        got = (found["data_type"], found["udt_name"], found["is_nullable"])
        if got != (data_type, udt_name, nullable) or found["default"] is not None:
            problems.append(
                f"forward: {table}.{column} is {got} default {found['default']!r}, "
                f"expected {(data_type, udt_name, nullable)} with no default"
            )
    for name, expected in EXPECTED_INDEXES.items():
        found = state["indexes"].get(name)
        if found is None:
            problems.append(f"forward: index {name} is missing")
            continue
        got = {
            "table": found["table"],
            "unique": found["unique"],
            "method": found["method"],
            "columns": found["columns"],
            "partial": found["predicate"] is not None,
        }
        if got != expected:
            problems.append(f"forward: index {name} is {got}, expected {expected}")
    return problems


def without_0037(state: dict[str, Any]) -> dict[str, Any]:
    """The state with 0037's four objects taken out: what the reverse must leave."""
    return {
        "columns": [c for c in state["columns"] if (c["table"], c["column"]) not in EXPECTED_COLUMNS],
        "indexes": {name: index for name, index in state["indexes"].items() if name not in EXPECTED_INDEXES},
    }


def check_reversed(forward: dict[str, Any], reversed_: dict[str, Any]) -> list[str]:
    """Problems with the state after the reverse: 0037's objects gone, and
    nothing else different from the forward state."""
    problems = []
    for table, column in EXPECTED_COLUMNS:
        if _column(reversed_, table, column) is not None:
            problems.append(f"reverse: {table}.{column} is still there")
    for name in EXPECTED_INDEXES:
        if name in reversed_["indexes"]:
            problems.append(f"reverse: index {name} is still there")
    want = without_0037(forward)
    got = without_0037(reversed_)
    if got["columns"] != want["columns"]:
        before = {(c["table"], c["column"]): c for c in want["columns"]}
        after = {(c["table"], c["column"]): c for c in got["columns"]}
        for key in sorted(set(before) | set(after)):
            if before.get(key) != after.get(key):
                problems.append(
                    f"reverse: column {key[0]}.{key[1]} changed: {before.get(key)} -> {after.get(key)}"
                )
        if not any("column" in p and "changed" in p for p in problems):
            problems.append("reverse: the order of the columns changed")
    for name in sorted(set(want["indexes"]) | set(got["indexes"])):
        if want["indexes"].get(name) != got["indexes"].get(name):
            problems.append(
                f"reverse: index {name} changed: {want['indexes'].get(name)} -> {got['indexes'].get(name)}"
            )
    return problems


def check_states(
    forward: dict[str, Any],
    reversed_: dict[str, Any],
    again: dict[str, Any],
    policies: tuple[Any, Any, Any],
) -> int:
    """Compare the three states; print the result, with annotations.
    `policies` is the policy-and-grant snapshot at each of the three."""
    problems = check_forward(forward) + check_reversed(forward, reversed_)
    if again != forward:
        problems.append("forward again: the state differs from the first forward state")
    if not (policies[0] == policies[1] == policies[2]):
        problems.append("the policies or the function grants changed across the round trip")
    print(
        f"::notice title=0037 state (forward)::sha256={sha256(forward)} "
        f"columns={len(forward['columns'])} indexes={len(forward['indexes'])}"
    )
    for line in problems:
        print(line)
    if problems:
        escaped = "\n".join(problems).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=0037 round trip: {len(problems)} problem(s)::{escaped}")
    print(
        f"migration_0037_roundtrip: forward {len(forward['indexes'])} indexes, "
        f"reverse {len(reversed_['indexes'])}, forward again {len(again['indexes'])}; "
        f"{len(problems)} problem(s)"
    )
    return 1 if problems else 0


def main() -> int:
    import psycopg
    from policy_snapshot import snapshot

    url = os.environ["SUPERUSER_DATABASE_URL"]

    def take() -> tuple[dict[str, Any], Any]:
        with psycopg.connect(url) as conn:
            return read_state(conn), snapshot(conn)

    def run(path: Path) -> None:
        with psycopg.connect(url) as conn:  # one transaction, committed on exit
            conn.execute(path.read_text(encoding="utf-8"))

    forward, policies_forward = take()
    run(REVERSE)
    reversed_, policies_reversed = take()
    run(FORWARD)
    again, policies_again = take()
    return check_states(forward, reversed_, again, (policies_forward, policies_reversed, policies_again))


if __name__ == "__main__":
    sys.exit(main())
