"""
Migration 0038 and its reverse (Stage 4, Q14; DECISIONS.md D-206): what the
two files say, and the checks CI's round trip applies to them.

The round trip itself needs a database CI can drop things from
(scripts/ci/migration_0038_roundtrip.py, run by the core job). These tests
hold its comparisons to account without one, with a broken state for each
thing it must catch.
"""

from __future__ import annotations

import copy
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "scripts" / "ci"
MIGRATION = REPO / "supabase" / "migrations" / "0038_database_wait.sql"
REVERSE = REPO / "supabase" / "reverse" / "0038_reverse.sql"

FOUR = "check (wait_cause in ('model_provider', 'storage', 'parse_service', 'database'))"
THREE = "check (wait_cause in ('model_provider', 'storage', 'parse_service'))"


def _statements(path: Path) -> list[str]:
    """The file's SQL statements, comments removed, whitespace collapsed."""
    sql = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
    return [re.sub(r"\s+", " ", s).strip().lower() for s in sql.split(";") if s.strip()]


@pytest.fixture(scope="module")
def roundtrip():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "migration_0038_roundtrip", SCRIPTS / "migration_0038_roundtrip.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _definition(causes: list[str]) -> str:
    """The check as pg_get_constraintdef prints it."""
    items = ", ".join(f"'{c}'::text" for c in causes)
    return f"CHECK ((wait_cause = ANY (ARRAY[{items}])))"


def _forward() -> dict:
    """A state shaped like the database's after 0038, with one older column,
    index and check so "nothing else changed" has something to hold."""
    return {
        "columns": [
            {
                "table": "documents",
                "column": "wait_cause",
                "data_type": "text",
                "udt_name": "text",
                "is_nullable": "YES",
                "default": None,
            }
        ],
        "indexes": {
            "idx_document_lines_matched_item": {
                "table": "document_lines",
                "unique": False,
                "method": "btree",
                "predicate": "(matched_item_id IS NOT NULL)",
                "columns": ["tenant_id", "matched_item_id"],
            },
            "idx_document_lines_matched_item_id": {
                "table": "document_lines",
                "unique": False,
                "method": "btree",
                "predicate": "(matched_item_id IS NOT NULL)",
                "columns": ["matched_item_id"],
            },
        },
        "checks": {
            "document_lines_match_method_requires_item": {
                "table": "document_lines",
                "definition": "CHECK (((matched_item_id IS NULL) = (match_method IS NULL)))",
            },
            "documents_wait_cause_check": {
                "table": "documents",
                "definition": _definition(["model_provider", "storage", "parse_service", "database"]),
            },
        },
    }


def _reversed() -> dict:
    state = copy.deepcopy(_forward())
    state["indexes"].pop("idx_document_lines_matched_item_id")
    state["checks"]["documents_wait_cause_check"]["definition"] = _definition(
        ["model_provider", "storage", "parse_service"]
    )
    return state


# ── the two files ────────────────────────────────────────────────────────────


def test_0038_widens_the_check_and_adds_one_index_and_nothing_else():
    assert _statements(MIGRATION) == [
        "alter table public.documents drop constraint documents_wait_cause_check",
        f"alter table public.documents add constraint documents_wait_cause_check {FOUR}",
        "create index idx_document_lines_matched_item_id on public.document_lines (matched_item_id) "
        "where matched_item_id is not null",
    ]


def test_the_reverse_clears_database_waits_then_puts_the_three_causes_back_and_drops_the_index():
    assert _statements(REVERSE) == [
        "update public.documents set wait_cause = null, waiting_since = null, last_wait_error = null "
        "where wait_cause = 'database'",
        "alter table public.documents drop constraint documents_wait_cause_check",
        f"alter table public.documents add constraint documents_wait_cause_check {THREE}",
        "drop index if exists public.idx_document_lines_matched_item_id",
    ]


@pytest.mark.parametrize("path", [MIGRATION, REVERSE], ids=["forward", "reverse"])
def test_neither_file_touches_a_policy_or_a_grant_and_only_the_reverse_changes_rows(path):
    for statement in _statements(path):
        for word in ("policy", "grant", "revoke", "delete ", "insert ", "truncate"):
            assert word not in statement, f"{path.name}: {statement!r} contains {word!r}"
        if statement.startswith("update"):
            # The one row change: documents waiting on the database (D-206 item 6).
            assert path == REVERSE and statement.endswith("where wait_cause = 'database'"), statement


def test_the_round_trip_expects_what_the_migration_declares(roundtrip):
    text = MIGRATION.read_text(encoding="utf-8").lower()
    assert f"constraint {roundtrip.CHECK}" in text
    assert f"index {roundtrip.INDEX} on public.{roundtrip.EXPECTED_INDEX['table']}" in text
    assert roundtrip.CAUSES_FORWARD - roundtrip.CAUSES_REVERSED == {"database"}


# ── the round trip's comparisons ─────────────────────────────────────────────


def test_a_correct_round_trip_passes(roundtrip, capsys):
    forward = _forward()
    assert roundtrip.check_states(forward, _reversed(), _forward(), ("p", "p", "p"), []) == 0
    out = capsys.readouterr().out
    assert f"::notice title=0038 state (forward)::sha256={roundtrip.sha256(forward)}" in out
    assert "0 problem(s)" in out


@pytest.mark.parametrize(
    ("which", "damage", "expected"),
    [
        (
            "forward",
            lambda s: s["checks"]["documents_wait_cause_check"].update(
                definition=_definition(["model_provider", "storage", "parse_service"])
            ),
            "forward: documents_wait_cause_check allows",
        ),
        ("forward", lambda s: s["indexes"].pop("idx_document_lines_matched_item_id"), "is missing"),
        (
            "forward",
            lambda s: s["indexes"]["idx_document_lines_matched_item_id"].update(predicate=None),
            "forward: index idx_document_lines_matched_item_id is",
        ),
        (
            "reversed",
            lambda s: s["checks"]["documents_wait_cause_check"].update(
                definition=_definition(["model_provider", "storage", "parse_service", "database"])
            ),
            "reverse: documents_wait_cause_check allows",
        ),
        (
            "reversed",
            lambda s: s["indexes"].update(
                idx_document_lines_matched_item_id=_forward()["indexes"]["idx_document_lines_matched_item_id"]
            ),
            "reverse: index idx_document_lines_matched_item_id is still there",
        ),
        (
            "reversed",
            lambda s: s["checks"].pop("document_lines_match_method_requires_item"),
            "reverse: the checks of documents or document_lines changed",
        ),
        (
            "reversed",
            lambda s: s["indexes"].pop("idx_document_lines_matched_item"),
            "reverse: the indexes of documents or document_lines changed",
        ),
    ],
    ids=[
        "forward check narrow",
        "forward index missing",
        "forward index not partial",
        "reverse check still wide",
        "reverse index left",
        "reverse took another check",
        "reverse took another index",
    ],
)
def test_a_round_trip_that_is_wrong_fails(roundtrip, capsys, which, damage, expected):
    forward, reversed_ = _forward(), _reversed()
    damage(forward if which == "forward" else reversed_)
    assert roundtrip.check_states(forward, reversed_, copy.deepcopy(forward), ("p",) * 3, []) == 1
    out = capsys.readouterr().out
    assert expected in out and "::error title=0038 round trip:" in out


def test_a_waiting_document_the_reverse_mishandled_fails(roundtrip, capsys):
    problems = ["waiting rows: after the reverse the document reads ('pending', 'database', ...)"]
    assert roundtrip.check_states(_forward(), _reversed(), _forward(), ("p",) * 3, problems) == 1
    assert "waiting rows:" in capsys.readouterr().out


def test_a_policy_change_across_the_round_trip_fails(roundtrip, capsys):
    assert roundtrip.check_states(_forward(), _reversed(), _forward(), ("p", "q", "p"), []) == 1
    assert "the policies or the function grants changed" in capsys.readouterr().out
