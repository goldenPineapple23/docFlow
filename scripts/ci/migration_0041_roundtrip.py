"""
Migration 0041, forward -> reverse -> forward, on the CI database (B1's demo
half; D-230; docs/designs/b1.md section 4: a reverse script and the CI round
trip, as for 0040).

Run after `supabase start` has applied every migration, 0041 included:
  1. read the forward state: the columns, indexes, check constraints and
     foreign keys of raw_emails, documents, intake_rejections and
     extraction_runs. It must hold everything 0041 declares (EXPECTED_* below);
  2. run supabase/reverse/0041_reverse.sql. Everything 0041 added must be
     gone, the two value lists and extraction_runs.document_id must read as
     they did before it, and everything else on the four tables must be as it
     was;
  3. run 0041 again. The state must equal step 1's, except that 0041's own
     columns may now sit at the end of their tables: run again, they are added
     behind any column added since (scripts/ci/roundtrip_columns.py). Any
     other column moved, or any column missing, fails.
Across all three the policies and the function grants must not change
(scripts/ci/policy_snapshot.py): 0041 and its reverse touch neither.

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
FORWARD = ROOT / "supabase" / "migrations" / "0041_mail_body.sql"
REVERSE = ROOT / "supabase" / "reverse" / "0041_reverse.sql"

TABLES = ("raw_emails", "documents", "intake_rejections", "extraction_runs")

_TEXT = ("text", "text", "YES", None)
_UUID = ("uuid", "uuid", "YES", None)
_BOOL = ("boolean", "bool", "YES", None)
_CONFIDENCE = ("numeric", "numeric", "YES", None)

# (table, column) -> (data_type, udt_name, is_nullable, column_default), in the
# order 0041 adds them.
EXPECTED_COLUMNS: dict[tuple[str, str], tuple[str, str, str, str | None]] = {
    ("raw_emails", "body_storage_path"): _TEXT,
    ("raw_emails", "body_kind"): _TEXT,
    ("raw_emails", "body_sha256"): _TEXT,
    ("raw_emails", "body_bytes"): ("integer", "int4", "YES", None),
    ("raw_emails", "body_html_path"): _TEXT,
    ("raw_emails", "body_text_path"): _TEXT,
    ("raw_emails", "body_state"): ("text", "text", "NO", "'none'::text"),
    ("raw_emails", "body_label"): _TEXT,
    ("raw_emails", "body_label_confidence"): _CONFIDENCE,
    ("raw_emails", "body_contains_order_lines"): _BOOL,
    ("raw_emails", "body_order_lines_confidence"): _CONFIDENCE,
    ("raw_emails", "body_injection_suspected"): _BOOL,
    ("raw_emails", "body_labelled_at"): ("timestamp with time zone", "timestamptz", "YES", None),
    ("raw_emails", "date_header"): _TEXT,
    ("documents", "raw_email_id"): _UUID,
    ("documents", "email_part"): _TEXT,
    ("intake_rejections", "raw_email_id"): _UUID,
    ("intake_rejections", "mail_label"): _TEXT,
    ("extraction_runs", "raw_email_id"): _UUID,
}

_SIX_LABELS = ("'order'", "'change'", "'cancellation'", "'quote_request'", "'status_question'", "'not_an_order'")

# Each check 0041 adds, and words its definition must hold as
# pg_get_constraintdef prints it.
EXPECTED_CHECKS: dict[str, tuple[str, ...]] = {
    "raw_emails_body_kind_check": ("'text'", "'html'"),
    "raw_emails_body_bytes_check": ("body_bytes >= 0",),
    "raw_emails_body_state_check": ("'none'", "'pending'", "'labelled'", "'held'", "'released'"),
    "raw_emails_body_label_check": _SIX_LABELS,
    "raw_emails_body_label_confidence_check": ("body_label_confidence >= ", "body_label_confidence <= "),
    "raw_emails_body_order_lines_confidence_check": (
        "body_order_lines_confidence >= ",
        "body_order_lines_confidence <= ",
    ),
    "raw_emails_body_stored_together": ("body_storage_path IS NULL", "body_kind IS NULL", "body_sha256 IS NULL",
                                        "body_bytes IS NULL"),
    "documents_email_part_check": ("'attachment'", "'body'"),
    "intake_rejections_mail_label_check": _SIX_LABELS,
    "extraction_runs_document_or_email": ("document_id IS NOT NULL", "raw_email_id IS NOT NULL"),
}

# The two checks 0041 replaces: the words only the forward definition holds.
# The reverse must leave them out and keep every other word.
CHANGED_CHECKS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # name: (words before and after, words only after 0041)
    "raw_emails_outcome_check": (("'processed'", "'quarantined'", "'rejected'"), ("'received'",)),
    "extraction_runs_run_kind_check": (("'extraction'", "'buyer_routing'"), ("'mail_label'",)),
}

# Each foreign key 0041 adds: (table, the column, the table it points at, what
# a delete of that row does).
EXPECTED_FOREIGN_KEYS: dict[str, tuple[str, str, str, str]] = {
    "documents_raw_email_id_fkey": ("documents", "raw_email_id", "raw_emails", "SET NULL"),
    "intake_rejections_raw_email_id_fkey": ("intake_rejections", "raw_email_id", "raw_emails", "SET NULL"),
    "extraction_runs_raw_email_id_fkey": ("extraction_runs", "raw_email_id", "raw_emails", "CASCADE"),
}

# Each index 0041 adds: (table, columns, predicate words, unique).
EXPECTED_INDEXES: dict[str, tuple[str, list[str], str, bool]] = {
    "idx_documents_raw_email": ("documents", ["raw_email_id"], "raw_email_id IS NOT NULL", False),
    # At most one body document per e-mail (founder, 2026-11-17).
    "idx_documents_one_body_per_email": ("documents", ["raw_email_id"], "email_part = 'body'", True),
    "idx_extraction_runs_raw_email": ("extraction_runs", ["raw_email_id"], "raw_email_id IS NOT NULL", False),
}

# The one existing column 0041 changes: nullable after it, NOT NULL before.
LOOSENED_COLUMN = ("extraction_runs", "document_id")

_TABLE_LIST = ", ".join(f"'{table}'" for table in TABLES)

_COLUMNS_SQL = f"""
    SELECT table_name, column_name, data_type, udt_name, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name IN ({_TABLE_LIST})
    ORDER BY table_name, ordinal_position
"""
_INDEXES_SQL = f"""
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
    WHERE n.nspname = 'public' AND t.relname IN ({_TABLE_LIST})
    ORDER BY c.relname
"""
_CONSTRAINTS_SQL = f"""
    SELECT con.conname, t.relname, con.contype, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class t ON t.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public' AND t.relname IN ({_TABLE_LIST}) AND con.contype IN ('c', 'f')
    ORDER BY t.relname, con.conname
"""


def read_state(conn: Any) -> dict[str, Any]:
    """The columns, indexes, checks and foreign keys of the four tables.
    Read-only. Checks and foreign keys are keyed `table.name`: a name may
    repeat across tables."""
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
    checks: dict[str, Any] = {}
    foreign_keys: dict[str, Any] = {}
    for name, table, kind, definition in conn.execute(_CONSTRAINTS_SQL).fetchall():
        target = checks if kind == "c" else foreign_keys
        target[f"{table}.{name}"] = {"table": table, "definition": definition}
    return {"columns": columns, "indexes": indexes, "checks": checks, "foreign_keys": foreign_keys}


def canonical(state: dict[str, Any]) -> str:
    return json.dumps(state, indent=1, sort_keys=True) + "\n"


def sha256(state: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(state).encode("utf-8")).hexdigest()


def _column(state: dict[str, Any], table: str, column: str) -> dict[str, Any] | None:
    return next((c for c in state["columns"] if c["table"] == table and c["column"] == column), None)


def _named(part: dict[str, Any], name: str) -> dict[str, Any] | None:
    """A check or foreign key by its bare name, on whichever table holds it."""
    return next((value for key, value in part.items() if key.split(".", 1)[1] == name), None)


def check_forward(state: dict[str, Any]) -> list[str]:
    problems = []
    for (table, column), expected in EXPECTED_COLUMNS.items():
        found = _column(state, table, column)
        if found is None:
            problems.append(f"forward: {table}.{column} is missing")
            continue
        got = (found["data_type"], found["udt_name"], found["is_nullable"], found["default"])
        if got != expected:
            problems.append(f"forward: {table}.{column} is {got}, expected {expected}")
    for name, words in EXPECTED_CHECKS.items():
        found = _named(state["checks"], name)
        if found is None:
            problems.append(f"forward: check {name} is missing")
        elif any(word not in found["definition"] for word in words):
            problems.append(f"forward: check {name} reads {found['definition']!r}")
    for name, (kept, added) in CHANGED_CHECKS.items():
        found = _named(state["checks"], name)
        if found is None:
            problems.append(f"forward: check {name} is missing")
        elif any(word not in found["definition"] for word in (*kept, *added)):
            problems.append(f"forward: check {name} reads {found['definition']!r}")
    for name, (table, column, target, on_delete) in EXPECTED_FOREIGN_KEYS.items():
        found = _named(state["foreign_keys"], name)
        wanted = (f"FOREIGN KEY ({column}) REFERENCES {target}(id)", f"ON DELETE {on_delete}")
        if found is None:
            problems.append(f"forward: foreign key {name} is missing")
        elif found["table"] != table or any(word not in found["definition"] for word in wanted):
            problems.append(f"forward: foreign key {name} on {found['table']} reads {found['definition']!r}")
    for name, (table, index_columns, predicate, unique) in EXPECTED_INDEXES.items():
        index = state["indexes"].get(name)
        if index is None:
            problems.append(f"forward: index {name} is missing")
        elif (
            index["table"] != table
            or index["columns"] != index_columns
            or index["unique"] is not unique
            or predicate not in (index["predicate"] or "")
        ):
            problems.append(f"forward: index {name} reads {index!r}")
    loosened = _column(state, *LOOSENED_COLUMN)
    if loosened is None or loosened["is_nullable"] != "YES":
        problems.append(f"forward: {'.'.join(LOOSENED_COLUMN)} must be nullable")
    return problems


def without_0041(state: dict[str, Any]) -> dict[str, Any]:
    """The state with everything 0041 adds taken out and the three things it
    changes left out of the comparison: what the reverse must leave exactly as
    it was. The three are checked by name in `check_reversed`."""
    own_checks = set(EXPECTED_CHECKS) | set(CHANGED_CHECKS)
    return {
        "columns": [
            c
            for c in state["columns"]
            if (c["table"], c["column"]) not in EXPECTED_COLUMNS and (c["table"], c["column"]) != LOOSENED_COLUMN
        ],
        "indexes": {name: index for name, index in state["indexes"].items() if name not in EXPECTED_INDEXES},
        "checks": {key: check for key, check in state["checks"].items() if key.split(".", 1)[1] not in own_checks},
        "foreign_keys": {
            key: fk for key, fk in state["foreign_keys"].items() if key.split(".", 1)[1] not in EXPECTED_FOREIGN_KEYS
        },
    }


def check_reversed(forward: dict[str, Any], reversed_: dict[str, Any]) -> list[str]:
    problems = []
    for table, column in EXPECTED_COLUMNS:
        if _column(reversed_, table, column) is not None:
            problems.append(f"reverse: {table}.{column} is still there")
    for name in EXPECTED_CHECKS:
        if _named(reversed_["checks"], name) is not None:
            problems.append(f"reverse: check {name} is still there")
    for name in EXPECTED_FOREIGN_KEYS:
        if _named(reversed_["foreign_keys"], name) is not None:
            problems.append(f"reverse: foreign key {name} is still there")
    for name in EXPECTED_INDEXES:
        if name in reversed_["indexes"]:
            problems.append(f"reverse: index {name} is still there")
    for name, (kept, added) in CHANGED_CHECKS.items():
        found = _named(reversed_["checks"], name)
        if found is None:
            problems.append(f"reverse: check {name} is missing")
        elif any(word not in found["definition"] for word in kept) or any(
            word in found["definition"] for word in added
        ):
            problems.append(f"reverse: check {name} reads {found['definition']!r}")
    tightened = _column(reversed_, *LOOSENED_COLUMN)
    if tightened is None or tightened["is_nullable"] != "NO":
        problems.append(f"reverse: {'.'.join(LOOSENED_COLUMN)} must be NOT NULL again")
    want, got = without_0041(forward), without_0041(reversed_)
    for part in ("columns", "indexes", "checks", "foreign_keys"):
        if want[part] != got[part]:
            problems.append(f"reverse: the {part} of the four tables changed beyond 0041's")
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
        f"::notice title=0041 state (forward)::sha256={sha256(forward)} "
        f"columns={len(forward['columns'])} indexes={len(forward['indexes'])} checks={len(forward['checks'])} "
        f"foreign_keys={len(forward['foreign_keys'])}"
    )
    for line in problems:
        print(line)
    if problems:
        escaped = "\n".join(problems).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=0041 round trip: {len(problems)} problem(s)::{escaped}")
    print(
        f"migration_0041_roundtrip: forward {len(forward['columns'])} columns, "
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
