"""
Migration 0038, forward -> reverse -> forward, on the CI database (Stage 4,
Q14; D-206: a reverse script and the CI round trip, as for 0037).

Run after `supabase start` has applied every migration, 0038 included:
  0. what the reverse does with a document already waiting on 'database',
     in a transaction that is rolled back: a fake tenant and one pending
     document waiting on 'database'; after the reverse its wait_cause,
     waiting_since and last_wait_error are cleared and its status and
     retry_at are kept. Nothing of this step is committed;
  1. read the forward state: the columns, indexes and check constraints of
     documents and document_lines. The wait_cause check must allow exactly
     the four causes, and 0038's index must be there as declared;
  2. run supabase/reverse/0038_reverse.sql. The check must allow exactly
     the three causes of 0035, the index must be gone, and every other
     column, index and check of both tables must be as it was;
  3. run 0038 again. The state must equal step 1's.
Across all three the policies and the function grants must not change
(scripts/ci/policy_snapshot.py): 0038 and its reverse touch neither.

CI only. Prints each problem and exits 1 on any. The forward state's SHA-256
is a GitHub annotation on every run.

Environment:
  SUPERUSER_DATABASE_URL  the local stack's `postgres` connection string
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[2]
FORWARD = ROOT / "supabase" / "migrations" / "0038_database_wait.sql"
REVERSE = ROOT / "supabase" / "reverse" / "0038_reverse.sql"

CHECK = "documents_wait_cause_check"
CAUSES_FORWARD = {"model_provider", "storage", "parse_service", "database"}
CAUSES_REVERSED = {"model_provider", "storage", "parse_service"}
INDEX = "idx_document_lines_matched_item_id"
EXPECTED_INDEX = {
    "table": "document_lines",
    "unique": False,
    "method": "btree",
    "columns": ["matched_item_id"],
    "partial": True,
}

_COLUMNS_SQL = """
    SELECT table_name, column_name, data_type, udt_name, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name IN ('documents', 'document_lines')
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
    WHERE n.nspname = 'public' AND t.relname IN ('documents', 'document_lines')
    ORDER BY c.relname
"""
_CHECKS_SQL = """
    SELECT con.conname, t.relname, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class t ON t.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public' AND t.relname IN ('documents', 'document_lines') AND con.contype = 'c'
    ORDER BY con.conname
"""


def read_state(conn: Any) -> dict[str, Any]:
    """The columns, indexes and check constraints of the two tables 0038 touches. Read-only."""
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


def causes(state: dict[str, Any]) -> set[str] | None:
    """The wait causes the check allows, read from its definition."""
    found = state["checks"].get(CHECK)
    if found is None:
        return None
    return set(re.findall(r"'([a-z_]+)'::text", found["definition"]))


def check_forward(state: dict[str, Any]) -> list[str]:
    problems = []
    if causes(state) != CAUSES_FORWARD:
        problems.append(f"forward: {CHECK} allows {causes(state)}, expected {sorted(CAUSES_FORWARD)}")
    found = state["indexes"].get(INDEX)
    if found is None:
        problems.append(f"forward: index {INDEX} is missing")
    else:
        got = {
            "table": found["table"],
            "unique": found["unique"],
            "method": found["method"],
            "columns": found["columns"],
            "partial": found["predicate"] is not None,
        }
        if got != EXPECTED_INDEX:
            problems.append(f"forward: index {INDEX} is {got}, expected {EXPECTED_INDEX}")
    return problems


def without_0038(state: dict[str, Any]) -> dict[str, Any]:
    """The state with 0038's index and the wait_cause check taken out: what
    the reverse must leave exactly as it was."""
    return {
        "columns": state["columns"],
        "indexes": {name: index for name, index in state["indexes"].items() if name != INDEX},
        "checks": {name: check for name, check in state["checks"].items() if name != CHECK},
    }


def check_reversed(forward: dict[str, Any], reversed_: dict[str, Any]) -> list[str]:
    problems = []
    if causes(reversed_) != CAUSES_REVERSED:
        problems.append(f"reverse: {CHECK} allows {causes(reversed_)}, expected {sorted(CAUSES_REVERSED)}")
    if INDEX in reversed_["indexes"]:
        problems.append(f"reverse: index {INDEX} is still there")
    want, got = without_0038(forward), without_0038(reversed_)
    for part in ("columns", "indexes", "checks"):
        if want[part] != got[part]:
            problems.append(f"reverse: the {part} of documents or document_lines changed beyond 0038's")
    return problems


def check_waiting_rows(conn: Any) -> list[str]:
    """Step 0: the reverse on a document waiting on 'database', rolled back."""
    tenant_id, document_id = uuid.uuid4(), uuid.uuid4()
    problems: list[str] = []
    with conn.transaction(force_rollback=True):
        conn.execute(
            "INSERT INTO tenants (id, name, status, onboarding_status, created_at, updated_at, status_changed_at) "
            "VALUES (%s, 'Acme Test 0038 Reverse', 'active', 'tenant_created', now(), now(), now())",
            (tenant_id,),
        )
        conn.execute(
            "INSERT INTO documents (id, tenant_id, original_filename, storage_path, source, status, "
            "content_sha256, created_at, waiting_since, wait_cause, retry_at, last_wait_error) "
            "VALUES (%s, %s, 'po.txt', 'tenants/ci/po.txt', 'upload', 'pending', encode(sha256('0038'::bytea), 'hex'), now(), "
            "now() - interval '1 hour', 'database', now() + interval '2 minutes', 'database:matching:08006')",
            (document_id, tenant_id),
        )
        retry_at = conn.execute("SELECT retry_at FROM documents WHERE id = %s", (document_id,)).fetchone()[0]
        conn.execute(REVERSE.read_text(encoding="utf-8"))
        row = conn.execute(
            "SELECT status, wait_cause, waiting_since, last_wait_error, retry_at FROM documents WHERE id = %s",
            (document_id,),
        ).fetchone()
        if row is None:
            problems.append("waiting rows: the reverse removed the document")
        elif row != ("pending", None, None, None, retry_at):
            problems.append(
                f"waiting rows: after the reverse the document reads {row[:4]} retry_at kept={row[4] == retry_at}, "
                "expected ('pending', None, None, None) with retry_at kept"
            )
    return problems


def check_states(
    forward: dict[str, Any],
    reversed_: dict[str, Any],
    again: dict[str, Any],
    policies: tuple[Any, Any, Any],
    row_problems: list[str],
) -> int:
    problems = row_problems + check_forward(forward) + check_reversed(forward, reversed_)
    if again != forward:
        problems.append("forward again: the state differs from the first forward state")
    if not (policies[0] == policies[1] == policies[2]):
        problems.append("the policies or the function grants changed across the round trip")
    print(
        f"::notice title=0038 state (forward)::sha256={sha256(forward)} "
        f"columns={len(forward['columns'])} indexes={len(forward['indexes'])} checks={len(forward['checks'])}"
    )
    for line in problems:
        print(line)
    if problems:
        escaped = "\n".join(problems).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=0038 round trip: {len(problems)} problem(s)::{escaped}")
    print(
        f"migration_0038_roundtrip: forward {len(forward['indexes'])} indexes, "
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

    with psycopg.connect(url, autocommit=True) as conn:
        row_problems = check_waiting_rows(conn)
    forward, policies_forward = take()
    run(REVERSE)
    reversed_, policies_reversed = take()
    run(FORWARD)
    again, policies_again = take()
    return check_states(
        forward, reversed_, again, (policies_forward, policies_reversed, policies_again), row_problems
    )


if __name__ == "__main__":
    sys.exit(main())
