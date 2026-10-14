"""
Migration 0037 and its reverse (Stage 4, PR 1a; DECISIONS.md D-203): what the
two files say, and the checks CI's round trip applies to them.

The round trip itself needs a database CI can drop things from
(scripts/ci/migration_0037_roundtrip.py, run by the core job). These tests
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
MIGRATION = REPO / "supabase" / "migrations" / "0037_alert_state_key.sql"
REVERSE = REPO / "supabase" / "reverse" / "0037_reverse.sql"


def _statements(path: Path) -> list[str]:
    """The file's SQL statements, comments removed, whitespace collapsed."""
    sql = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
    return [re.sub(r"\s+", " ", s).strip().lower() for s in sql.split(";") if s.strip()]


@pytest.fixture(scope="module")
def roundtrip():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "migration_0037_roundtrip", SCRIPTS / "migration_0037_roundtrip.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _forward() -> dict:
    """A state shaped like the database's after 0037, with one older column
    and index on each table so "nothing else changed" has something to hold."""

    def column(table, name, data_type="text", udt="text", nullable="YES", default=None):
        return {
            "table": table,
            "column": name,
            "data_type": data_type,
            "udt_name": udt,
            "is_nullable": nullable,
            "default": default,
        }

    return {
        "columns": [
            column("documents", "id", "uuid", "uuid", "NO"),
            column("documents", "fault_keys", "ARRAY", "_text"),
            column("founder_alerts", "dedupe_key"),
            column("founder_alerts", "state_key"),
        ],
        "indexes": {
            "documents_pkey": {
                "table": "documents",
                "unique": True,
                "method": "btree",
                "predicate": None,
                "columns": ["id"],
            },
            "idx_documents_fault_keys": {
                "table": "documents",
                "unique": False,
                "method": "gin",
                "predicate": "(fault_keys IS NOT NULL)",
                "columns": ["fault_keys"],
            },
            "idx_founder_alerts_open_dedupe": {
                "table": "founder_alerts",
                "unique": True,
                "method": "btree",
                "predicate": "((dedupe_key IS NOT NULL) AND (acknowledged_at IS NULL))",
                "columns": ["dedupe_key"],
            },
            "idx_founder_alerts_state_key": {
                "table": "founder_alerts",
                "unique": True,
                "method": "btree",
                "predicate": None,
                "columns": ["state_key"],
            },
        },
    }


# ── the two files ────────────────────────────────────────────────────────────


def test_0037_adds_two_nullable_columns_and_two_indexes_and_nothing_else():
    assert _statements(MIGRATION) == [
        "alter table public.founder_alerts add column state_key text",
        "create unique index idx_founder_alerts_state_key on public.founder_alerts (state_key)",
        "alter table public.documents add column fault_keys text[]",
        "create index idx_documents_fault_keys on public.documents using gin (fault_keys) "
        "where fault_keys is not null",
    ]


def test_the_state_key_index_covers_acknowledged_alerts_too():
    """The whole point (D-198's defect): 0011's dedupe index stops at
    `acknowledged_at is null`. This one has no predicate at all."""
    index = next(s for s in _statements(MIGRATION) if "idx_founder_alerts_state_key" in s)
    assert " where " not in index and "acknowledged" not in index


def test_the_reverse_drops_exactly_what_0037_adds():
    assert _statements(REVERSE) == [
        "drop index if exists public.idx_documents_fault_keys",
        "alter table public.documents drop column if exists fault_keys",
        "drop index if exists public.idx_founder_alerts_state_key",
        "alter table public.founder_alerts drop column if exists state_key",
    ]


@pytest.mark.parametrize("path", [MIGRATION, REVERSE], ids=["forward", "reverse"])
def test_neither_file_touches_a_row_a_policy_or_a_grant(path):
    for statement in _statements(path):
        first = statement.split()[0]
        assert first in ("alter", "create", "drop"), statement
        for word in ("policy", "grant", "revoke", "update ", "delete ", "insert ", "truncate"):
            assert word not in statement, f"{path.name}: {statement!r} contains {word!r}"
        if first == "alter":
            # A new column with NOT NULL or a default would write every row.
            assert "not null" not in statement and "default" not in statement, statement


def test_the_round_trip_expects_what_the_migration_declares(roundtrip):
    """The script's expectations and the migration's text name the same
    objects, so one can't change without the other."""
    text = MIGRATION.read_text(encoding="utf-8").lower()
    for table, column in roundtrip.EXPECTED_COLUMNS:
        assert f"alter table public.{table} add column {column} " in text
    for name, index in roundtrip.EXPECTED_INDEXES.items():
        assert f"index {name} on public.{index['table']}" in text
    assert set(roundtrip.TABLES) == {t for t, _ in roundtrip.EXPECTED_COLUMNS}


# ── the round trip's comparisons ─────────────────────────────────────────────


def test_a_correct_round_trip_passes(roundtrip, capsys):
    forward = _forward()
    assert roundtrip.check_states(forward, roundtrip.without_0037(forward), _forward(), ("p", "p", "p")) == 0
    out = capsys.readouterr().out
    assert f"::notice title=0037 state (forward)::sha256={roundtrip.sha256(forward)}" in out
    assert "0 problem(s)" in out


@pytest.mark.parametrize(
    ("damage", "expected"),
    [
        (lambda s: s["columns"].pop(3), "forward: founder_alerts.state_key is missing"),
        (
            lambda s: s["indexes"].pop("idx_documents_fault_keys"),
            "forward: index idx_documents_fault_keys is missing",
        ),
        (
            # A state-key index that skips acknowledged rows is the defect again.
            lambda s: s["indexes"]["idx_founder_alerts_state_key"].update(
                predicate="(acknowledged_at IS NULL)"
            ),
            "forward: index idx_founder_alerts_state_key is",
        ),
        (
            lambda s: s["indexes"]["idx_founder_alerts_state_key"].update(unique=False),
            "forward: index idx_founder_alerts_state_key is",
        ),
        (lambda s: s["columns"][3].update(is_nullable="NO"), "forward: founder_alerts.state_key is"),
    ],
    ids=["column missing", "index missing", "index partial", "index not unique", "column not nullable"],
)
def test_a_forward_state_that_is_not_what_0037_declares_fails(roundtrip, capsys, damage, expected):
    forward = _forward()
    damage(forward)
    assert (
        roundtrip.check_states(forward, roundtrip.without_0037(forward), copy.deepcopy(forward), ("p",) * 3)
        == 1
    )
    out = capsys.readouterr().out
    assert expected in out and "::error title=0037 round trip:" in out


def test_a_reverse_that_leaves_something_behind_fails(roundtrip, capsys):
    forward = _forward()
    left = roundtrip.without_0037(forward)
    left["indexes"]["idx_founder_alerts_state_key"] = forward["indexes"]["idx_founder_alerts_state_key"]
    assert roundtrip.check_states(forward, left, _forward(), ("p",) * 3) == 1
    assert "reverse: index idx_founder_alerts_state_key is still there" in capsys.readouterr().out


def test_a_reverse_that_removes_more_than_0037_added_fails(roundtrip, capsys):
    forward = _forward()
    too_much = roundtrip.without_0037(forward)
    del too_much["indexes"]["idx_founder_alerts_open_dedupe"]
    too_much["columns"] = [c for c in too_much["columns"] if c["column"] != "dedupe_key"]
    assert roundtrip.check_states(forward, too_much, _forward(), ("p",) * 3) == 1
    out = capsys.readouterr().out
    assert "reverse: index idx_founder_alerts_open_dedupe changed" in out
    assert "reverse: column founder_alerts.dedupe_key changed" in out


def test_a_second_forward_that_differs_from_the_first_fails(roundtrip, capsys):
    forward = _forward()
    again = _forward()
    again["indexes"]["idx_documents_fault_keys"]["method"] = "btree"
    assert roundtrip.check_states(forward, roundtrip.without_0037(forward), again, ("p",) * 3) == 1
    assert "forward again: the state differs" in capsys.readouterr().out


def test_a_policy_or_grant_that_changed_on_the_way_fails(roundtrip, capsys):
    forward = _forward()
    assert roundtrip.check_states(forward, roundtrip.without_0037(forward), _forward(), ("p", "q", "p")) == 1
    assert "the policies or the function grants changed" in capsys.readouterr().out
