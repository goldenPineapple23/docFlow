"""
Migration 0040 and its reverse (Stage 5, viewer A3; DECISIONS.md D-232): what
the two files say, and the checks CI's round trip applies to them.

The round trip itself needs a database CI can drop things from
(scripts/ci/migration_0040_roundtrip.py, run by the core job). These tests
hold its comparisons to account without one, with a broken state for each
thing it must catch. What the checks refuse is proved against a real
database in the worker's tests (test_render_pages_db.py).
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
MIGRATION = REPO / "supabase" / "migrations" / "0040_document_pages.sql"
REVERSE = REPO / "supabase" / "reverse" / "0040_reverse.sql"


def _statements(path: Path) -> list[str]:
    """The file's SQL statements, comments removed, whitespace collapsed."""
    sql = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
    return [re.sub(r"\s+", " ", s).strip().lower() for s in sql.split(";") if s.strip()]


@pytest.fixture(scope="module")
def roundtrip():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "migration_0040_roundtrip", SCRIPTS / "migration_0040_roundtrip.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _column(name, data_type="text", udt="text", nullable="YES"):
    return {
        "table": "documents",
        "column": name,
        "data_type": data_type,
        "udt_name": udt,
        "is_nullable": nullable,
        "default": None,
    }


def _forward() -> dict:
    """A state shaped like the database's after 0040 (and after 0037's round
    trip, which leaves fault_keys last), with an older column, index and check
    so "nothing else changed" has something to hold."""
    return {
        "columns": [
            _column("id", "uuid", "uuid", "NO"),
            _column("page_count", "integer", "int4"),
            _column("pages", "jsonb", "jsonb"),
            _column("fault_keys", "ARRAY", "_text"),
        ],
        "indexes": {
            "documents_pkey": {
                "table": "documents",
                "unique": True,
                "method": "btree",
                "predicate": None,
                "columns": ["id"],
            },
        },
        "checks": {
            "documents_wait_cause_check": {"table": "documents", "definition": "CHECK ((wait_cause = ...))"},
            "documents_page_count_check": {
                "table": "documents",
                "definition": "CHECK (((page_count IS NULL) OR (page_count >= 0)))",
            },
            "documents_pages_both_or_neither": {
                "table": "documents",
                "definition": "CHECK (((page_count IS NULL) = (pages IS NULL)))",
            },
            "documents_pages_match_count": {
                "table": "documents",
                "definition": (
                    "CHECK (((page_count IS NULL) OR (pages IS NULL) OR CASE WHEN (jsonb_typeof(pages) = "
                    "'array'::text) THEN (jsonb_array_length(pages) = page_count) ELSE false END))"
                ),
            },
        },
    }


def _again() -> dict:
    """The forward-again state as a real database leaves it: 0040's two
    columns re-added at the end, behind fault_keys."""
    state = _forward()
    columns = state["columns"]
    state["columns"] = [columns[0], columns[3], columns[1], columns[2]]
    return state


# ── the two files ────────────────────────────────────────────────────────────


def test_0040_adds_two_nullable_columns_and_three_checks_and_nothing_else():
    assert _statements(MIGRATION) == [
        "alter table public.documents add column page_count integer constraint documents_page_count_check "
        "check (page_count is null or page_count >= 0), add column pages jsonb, add constraint "
        "documents_pages_both_or_neither check ((page_count is null) = (pages is null)), add constraint "
        "documents_pages_match_count check ( page_count is null or pages is null or case when "
        "jsonb_typeof(pages) = 'array' then jsonb_array_length(pages) = page_count else false end )"
    ]


def test_the_reverse_drops_exactly_what_0040_adds():
    assert _statements(REVERSE) == [
        "alter table public.documents drop constraint documents_pages_match_count",
        "alter table public.documents drop constraint documents_pages_both_or_neither",
        "alter table public.documents drop column pages",
        "alter table public.documents drop column page_count",
    ]


@pytest.mark.parametrize("path", [MIGRATION, REVERSE], ids=["forward", "reverse"])
def test_neither_file_touches_a_row_a_policy_or_a_grant(path):
    for statement in _statements(path):
        assert statement.split()[0] == "alter", statement
        for word in ("policy", "grant", "revoke", "update ", "delete ", "insert ", "truncate"):
            assert word not in statement, f"{path.name}: {statement!r} contains {word!r}"
        # A new column with NOT NULL or a default would write every row.
        assert "not null" not in statement and "default" not in statement, statement


def test_the_round_trip_expects_what_the_migration_declares(roundtrip):
    text = MIGRATION.read_text(encoding="utf-8").lower()
    assert roundtrip.EXPECTED_COLUMNS == {
        ("documents", "page_count"): ("integer", "int4", "YES"),
        ("documents", "pages"): ("jsonb", "jsonb", "YES"),
    }
    assert "add column page_count integer" in text and "add column pages jsonb" in text
    for name in roundtrip.EXPECTED_CHECKS:
        assert f"constraint {name} check" in text
    reverse = REVERSE.read_text(encoding="utf-8").lower()
    for name in roundtrip.EXPECTED_CHECKS:
        if name != "documents_page_count_check":  # a column check, dropped with its column
            assert f"drop constraint {name}" in reverse


# ── the round trip's comparisons ─────────────────────────────────────────────


def test_a_correct_round_trip_passes(roundtrip, capsys):
    forward = _forward()
    assert roundtrip.check_states(forward, roundtrip.without_0040(forward), _again(), ("p",) * 3) == 0
    out = capsys.readouterr().out
    assert f"::notice title=0040 state (forward)::sha256={roundtrip.sha256(forward)}" in out
    assert "0 problem(s)" in out


def test_a_second_forward_that_left_the_columns_where_they_were_passes_too(roundtrip, capsys):
    forward = _forward()
    assert roundtrip.check_states(forward, roundtrip.without_0040(forward), _forward(), ("p",) * 3) == 0
    assert "0 problem(s)" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("damage", "expected"),
    [
        (lambda s: s["columns"].pop(2), "forward: documents.pages is missing"),
        (lambda s: s["columns"][1].update(is_nullable="NO"), "forward: documents.page_count is"),
        (lambda s: s["columns"][2].update(udt_name="json", data_type="json"), "forward: documents.pages is"),
        (
            lambda s: s["checks"].pop("documents_pages_both_or_neither"),
            "forward: check documents_pages_both_or_neither is missing",
        ),
        (
            lambda s: s["checks"]["documents_pages_match_count"].update(definition="CHECK (true)"),
            "forward: check documents_pages_match_count reads",
        ),
    ],
    ids=["column missing", "column not nullable", "wrong type", "check missing", "check changed"],
)
def test_a_forward_state_that_is_not_what_0040_declares_fails(roundtrip, capsys, damage, expected):
    forward = _forward()
    damage(forward)
    reversed_ = roundtrip.without_0040(forward)
    assert roundtrip.check_states(forward, reversed_, copy.deepcopy(forward), ("p",) * 3) == 1
    out = capsys.readouterr().out
    assert expected in out and "::error title=0040 round trip:" in out


def test_a_reverse_that_leaves_something_behind_fails(roundtrip, capsys):
    forward = _forward()
    left = roundtrip.without_0040(forward)
    left["checks"]["documents_pages_match_count"] = forward["checks"]["documents_pages_match_count"]
    assert roundtrip.check_states(forward, left, _again(), ("p",) * 3) == 1
    assert "reverse: check documents_pages_match_count is still there" in capsys.readouterr().out


def test_a_reverse_that_removes_more_than_0040_added_fails(roundtrip, capsys):
    forward = _forward()
    too_much = roundtrip.without_0040(forward)
    too_much["columns"] = [c for c in too_much["columns"] if c["column"] != "fault_keys"]
    del too_much["checks"]["documents_wait_cause_check"]
    assert roundtrip.check_states(forward, too_much, _again(), ("p",) * 3) == 1
    out = capsys.readouterr().out
    assert "reverse: the columns of documents changed beyond 0040's" in out
    assert "reverse: the checks of documents changed beyond 0040's" in out


def test_a_second_forward_with_another_column_moved_fails(roundtrip, capsys):
    forward = _forward()
    again = _again()
    again["columns"] = [again["columns"][1], again["columns"][0], *again["columns"][2:]]
    assert roundtrip.check_states(forward, roundtrip.without_0040(forward), again, ("p",) * 3) == 1
    assert "forward again: the state differs" in capsys.readouterr().out


def test_a_second_forward_with_a_column_missing_fails(roundtrip, capsys):
    forward = _forward()
    again = _again()
    again["columns"] = [c for c in again["columns"] if c["column"] != "fault_keys"]
    assert roundtrip.check_states(forward, roundtrip.without_0040(forward), again, ("p",) * 3) == 1
    assert "forward again: the state differs" in capsys.readouterr().out


def test_a_policy_or_grant_that_changed_on_the_way_fails(roundtrip, capsys):
    forward = _forward()
    assert roundtrip.check_states(forward, roundtrip.without_0040(forward), _again(), ("p", "q", "p")) == 1
    assert "the policies or the function grants changed" in capsys.readouterr().out
