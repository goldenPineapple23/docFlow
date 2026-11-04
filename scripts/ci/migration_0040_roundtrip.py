"""
Migration 0040, forward -> reverse -> forward, on the CI database (Stage 5,
viewer A3; D-232: a reverse script and the CI round trip, as for 0037).

Run after `supabase start` has applied every migration, 0040 included:
  1. read the forward state: the columns, indexes and check constraints of
     documents. It must hold 0040's two columns and three checks, each as
     0040 declares it (EXPECTED below);
  2. run supabase/reverse/0040_reverse.sql. The two columns and three checks
     must be gone, and every other column, index and check of documents must
     be as it was;
  3. run 0040 again. The state must equal step 1's, except that 0040's two
     columns may now sit at the end of the table: run again, they are added
     behind any column added since (scripts/ci/roundtrip_columns.py). Any
     other column moved, or any column missing, fails.
Across all three the policies and the function grants must not change
(scripts/ci/policy_snapshot.py): 0040 and its reverse touch neither.

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
FORWARD = ROOT / "supabase" / "migrations" / "0040_document_pages.sql"
REVERSE = ROOT / "supabase" / "reverse" / "0040_reverse.sql"

# (table, column) -> (data_type, udt_name, is_nullable), with no default.
EXPECTED_COLUMNS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("documents", "page_count"): ("integer", "int4", "YES"),
    ("documents", "pages"): ("jsonb", "jsonb", "YES"),
}
# Each check, and words its definition must hold as pg_get_constraintdef prints it.
EXPECTED_CHECKS: dict[str, tuple[str, ...]] = {
    "documents_page_count_check": ("page_count >= 0",),
    "documents_pages_both_or_neither": ("page_count IS NULL", "pages IS NULL"),
    "documents_pages_match_count": ("jsonb_typeof(pages)", "jsonb_array_length(pages) = page_count"),
}

_COLUMNS_SQL = """
    SELECT table_name, column_name, data_type, udt_name, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'documents'
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
    WHERE n.nspname = 'public' AND t.relname = 'documents'
    ORDER BY c.relname
"""
_CHECKS_SQL = """
    SELECT con.conname, t.relname, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class t ON t.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public' AND t.relname = 'documents' AND con.contype = 'c'
    ORDER BY con.conname
"""


def read_state(conn: Any) -> dict[str, Any]:
    """The columns, indexes and check constraints of documents. Read-only."""
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
    checks = {row[0]: {"table": row[1], "definition": row[2]} for row in conn.execute(_CHECKS_SQL).fetchall()}
    return {"columns": columns, "indexes": indexes, "checks": checks}


def canonical(state: dict[str, Any]) -> str:
    return json.dumps(state, indent=1, sort_keys=True) + "\n"


def sha256(state: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(state).encode("utf-8")).hexdigest()


def _column(state: dict[str, Any], table: str, column: str) -> dict[str, Any] | None:
    return next((c for c in state["columns"] if c["table"] == table and c["column"] == column), None)


def check_forward(state: dict[str, Any]) -> list[str]:
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
    for name, words in EXPECTED_CHECKS.items():
        found = state["checks"].get(name)
        if found is None:
            problems.append(f"forward: check {name} is missing")
        elif any(word not in found["definition"] for word in words):
            problems.append(f"forward: check {name} reads {found['definition']!r}")
    return problems


def without_0040(state: dict[str, Any]) -> dict[str, Any]:
    """The state with 0040's two columns and three checks taken out: what the
    reverse must leave exactly as it was."""
    return {
        "columns": [c for c in state["columns"] if (c["table"], c["column"]) not in EXPECTED_COLUMNS],
        "indexes": state["indexes"],
        "checks": {name: check for name, check in state["checks"].items() if name not in EXPECTED_CHECKS},
    }


def check_reversed(forward: dict[str, Any], reversed_: dict[str, Any]) -> list[str]:
    problems = []
    for table, column in EXPECTED_COLUMNS:
        if _column(reversed_, table, column) is not None:
            problems.append(f"reverse: {table}.{column} is still there")
    for name in EXPECTED_CHECKS:
        if name in reversed_["checks"]:
            problems.append(f"reverse: check {name} is still there")
    want, got = without_0040(forward), without_0040(reversed_)
    for part in ("columns", "indexes", "checks"):
        if want[part] != got[part]:
            problems.append(f"reverse: the {part} of documents changed beyond 0040's")
    return problems


def check_states(
    forward: dict[str, Any],
    reversed_: dict[str, Any],
    again: dict[str, Any],
    policies: tuple[Any, Any, Any],
) -> int:
    from roundtrip_columns import same_after_rerun

    problems = check_forward(forward) + check_reversed(forward, reversed_)
    if not same_after_rerun(forward, again, set(EXPECTED_COLUMNS)):
        problems.append("forward again: the state differs from the first forward state")
    if not (policies[0] == policies[1] == policies[2]):
        problems.append("the policies or the function grants changed across the round trip")
    print(
        f"::notice title=0040 state (forward)::sha256={sha256(forward)} "
        f"columns={len(forward['columns'])} indexes={len(forward['indexes'])} checks={len(forward['checks'])}"
    )
    for line in problems:
        print(line)
    if problems:
        escaped = "\n".join(problems).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=0040 round trip: {len(problems)} problem(s)::{escaped}")
    print(
        f"migration_0040_roundtrip: forward {len(forward['columns'])} columns, "
        f"reverse {len(reversed_['columns'])}, forward again {len(again['columns'])}; "
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
