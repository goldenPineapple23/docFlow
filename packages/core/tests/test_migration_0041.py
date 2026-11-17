"""
Migration 0041 and its reverse (B1's demo half; DECISIONS.md D-230;
docs/designs/b1.md section 4): what the two files say, and the checks CI's
round trip applies to them.

The round trip itself needs a database CI can drop things from
(scripts/ci/migration_0041_roundtrip.py, run by the core job). These tests
hold its comparisons to account without one, with a broken state for each
thing it must catch. What the columns then hold is proved against a real
database in the API's tests (test_email_body_intake.py).
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
MIGRATION = REPO / "supabase" / "migrations" / "0041_mail_body.sql"
REVERSE = REPO / "supabase" / "reverse" / "0041_reverse.sql"

SIX_LABELS = "'order', 'change', 'cancellation', 'quote_request', 'status_question', 'not_an_order'"


def _statements(path: Path) -> list[str]:
    """The file's SQL statements, comments removed, whitespace collapsed."""
    sql = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
    return [re.sub(r"\s+", " ", s).strip().lower() for s in sql.split(";") if s.strip()]


@pytest.fixture(scope="module")
def roundtrip():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "migration_0041_roundtrip", SCRIPTS / "migration_0041_roundtrip.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── the two files ────────────────────────────────────────────────────────────


def test_0041_touches_only_its_four_tables_and_adds_or_loosens_only():
    statements = _statements(MIGRATION)
    assert len(statements) == 12
    for statement in statements:
        assert statement.startswith(("alter table public.", "create index ", "create unique index ")), (
            statement
        )
        table = re.search(r"public\.(\w+)", statement)
        assert table and table.group(1) in ("raw_emails", "documents", "intake_rejections", "extraction_runs")
        for word in (
            "policy",
            "grant",
            "revoke",
            "update ",
            "delete ",
            "insert ",
            "truncate",
            "drop column",
            "drop table",
            "set not null",
        ):
            # A foreign key's own "on delete ..." rule is not a DELETE.
            assert word not in statement.replace(" on delete ", " "), f"{statement!r} contains {word!r}"
    # The only things dropped are the two value lists, each put back wider in
    # the next statement.
    drops = [s for s in statements if " drop " in s]
    assert drops == [
        "alter table public.raw_emails drop constraint raw_emails_outcome_check",
        "alter table public.extraction_runs alter column document_id drop not null",
        "alter table public.extraction_runs drop constraint extraction_runs_run_kind_check",
    ]
    assert (
        "alter table public.raw_emails add constraint raw_emails_outcome_check "
        "check (outcome in ('processed', 'quarantined', 'rejected', 'received'))"
    ) in statements
    assert (
        "alter table public.extraction_runs add constraint extraction_runs_run_kind_check "
        "check (run_kind in ('extraction', 'buyer_routing', 'mail_label'))"
    ) in statements


def test_0041_gives_no_new_column_a_default_but_body_state_and_makes_no_other_not_null():
    """A constant default is recorded without rewriting the table; anything
    else would write every row. `body_state` is the one column with one."""
    text = " ".join(_statements(MIGRATION))
    # "not null" otherwise appears only in `is not null` tests and in the one
    # `drop not null`.
    assert text.count("not null") == 1 + text.count("is not null") + text.count("drop not null")
    assert text.count("drop not null") == 1
    assert text.count("default") == 1
    assert "add column body_state text not null default 'none'" in text


def test_every_new_foreign_key_lets_the_tenants_hard_delete_through():
    """The hard delete removes documents before raw_emails
    (admin_data_access._PURGE_TABLES): a link that neither cascades nor
    nulls would stop it."""
    text = " ".join(_statements(MIGRATION))
    references = re.findall(r"references public\.(\w+)\(id\) on delete (set null|cascade)", text)
    assert references == [
        ("raw_emails", "set null"),
        ("raw_emails", "set null"),
        ("raw_emails", "cascade"),
    ]
    assert text.count("references ") == 3
    # Nothing on raw_emails points back at a document (founder, 2026-11-17):
    # the e-mail's body document is found through documents.raw_email_id.
    assert "references public.documents" not in text and "body_document_id" not in text


def test_at_most_one_body_document_per_email_is_a_partial_unique_index():
    """Decision 5 (founder, 2026-11-17): unique on raw_email_id among the
    body documents only, so an e-mail's attachments are unaffected and the
    insert in slice 4 can name it: ON CONFLICT (raw_email_id) WHERE
    email_part = 'body' DO NOTHING."""
    statements = _statements(MIGRATION)
    assert (
        "create unique index idx_documents_one_body_per_email on public.documents(raw_email_id) "
        "where email_part = 'body'"
    ) in statements
    assert sum(s.startswith("create unique index") for s in statements) == 1


def test_the_six_labels_are_the_same_wherever_0041_lists_them():
    text = " ".join(_statements(MIGRATION))
    assert text.count(SIX_LABELS) == 2


def test_the_reverse_removes_what_0041_adds_and_deletes_or_rewrites_no_row():
    statements = _statements(REVERSE)
    for statement in statements:
        assert statement.startswith(("alter table public.", "drop index public.")), statement
        for word in ("policy", "grant", "revoke", "update ", "delete ", "insert ", "truncate", "cascade"):
            assert word not in statement, f"{statement!r} contains {word!r}"
    text = " ".join(statements)
    assert "alter table public.extraction_runs alter column document_id set not null" in statements
    assert (
        "alter table public.raw_emails add constraint raw_emails_outcome_check "
        "check (outcome in ('processed', 'quarantined', 'rejected'))"
    ) in statements
    assert (
        "alter table public.extraction_runs add constraint extraction_runs_run_kind_check "
        "check (run_kind in ('extraction', 'buyer_routing'))"
    ) in statements
    assert "drop index public.idx_documents_raw_email" in statements
    assert "drop index public.idx_documents_one_body_per_email" in statements
    assert "drop index public.idx_extraction_runs_raw_email" in statements
    assert "'received'" not in text and "'mail_label'" not in text


def test_the_round_trip_expects_what_the_migration_declares(roundtrip):
    forward = " ".join(_statements(MIGRATION))
    reverse = " ".join(_statements(REVERSE))
    added = re.findall(r"add column (\w+)", forward)
    assert [column for _table, column in roundtrip.EXPECTED_COLUMNS] == added
    assert len(added) == 19
    for _table, column in roundtrip.EXPECTED_COLUMNS:
        assert f"drop column {column}" in reverse, column
    for name in (*roundtrip.EXPECTED_CHECKS, *roundtrip.CHANGED_CHECKS, *roundtrip.EXPECTED_FOREIGN_KEYS):
        assert f"constraint {name} " in forward, name
    assert len(re.findall(r"constraint \w+ (?:check|references)", forward)) == (
        len(roundtrip.EXPECTED_CHECKS) + len(roundtrip.CHANGED_CHECKS) + len(roundtrip.EXPECTED_FOREIGN_KEYS)
    )
    for name, (_table, _columns, _predicate, unique) in roundtrip.EXPECTED_INDEXES.items():
        create = "create unique index" if unique else "create index"
        assert f"{create} {name} on " in forward and f"drop index public.{name}" in reverse
    assert forward.count("create index") + forward.count("create unique index") == len(
        roundtrip.EXPECTED_INDEXES
    )
    assert set(roundtrip.TABLES) == {table for table, _column in roundtrip.EXPECTED_COLUMNS}


# ── the round trip's comparisons ─────────────────────────────────────────────


def _column(table, name, data_type="text", udt="text", nullable="YES", default=None):
    return {
        "table": table,
        "column": name,
        "data_type": data_type,
        "udt_name": udt,
        "is_nullable": nullable,
        "default": default,
    }


def _forward(roundtrip) -> dict:
    """A state shaped like the database's after 0041, built from what the
    round trip expects, with an older column, index, check and foreign key on
    each side so "nothing else changed" has something to hold."""
    columns = [
        _column("documents", "id", "uuid", "uuid", "NO"),
        _column("extraction_runs", "id", "uuid", "uuid", "NO"),
        _column("extraction_runs", "document_id", "uuid", "uuid", "YES"),
        _column("intake_rejections", "id", "uuid", "uuid", "NO"),
        _column("raw_emails", "id", "uuid", "uuid", "NO"),
        _column("raw_emails", "outcome", nullable="NO"),
    ]
    for (table, name), (data_type, udt, nullable, default) in roundtrip.EXPECTED_COLUMNS.items():
        columns.append(_column(table, name, data_type, udt, nullable, default))
    columns.sort(key=lambda c: c["table"])  # stable: each table's own order is kept

    indexes = {
        "documents_pkey": {
            "table": "documents",
            "unique": True,
            "method": "btree",
            "predicate": None,
            "columns": ["id"],
        },
    }
    for name, (table, index_columns, predicate, unique) in roundtrip.EXPECTED_INDEXES.items():
        indexes[name] = {
            "table": table,
            "unique": unique,
            "method": "btree",
            "predicate": f"({predicate})",
            "columns": index_columns,
        }

    checks = {
        "extraction_runs.extraction_runs_run_state_check": {
            "table": "extraction_runs",
            "definition": "CHECK ((run_state = ANY (ARRAY['started'::text, 'finished'::text])))",
        },
    }
    for name, words in roundtrip.EXPECTED_CHECKS.items():
        table = next(t for t in roundtrip.TABLES if name.startswith(t))
        checks[f"{table}.{name}"] = {"table": table, "definition": "CHECK (" + " ... ".join(words) + ")"}
    for name, (kept, added) in roundtrip.CHANGED_CHECKS.items():
        table = next(t for t in roundtrip.TABLES if name.startswith(t))
        checks[f"{table}.{name}"] = {
            "table": table,
            "definition": "CHECK (" + ", ".join((*kept, *added)) + ")",
        }

    foreign_keys = {
        "extraction_runs.extraction_runs_document_id_fkey": {
            "table": "extraction_runs",
            "definition": "FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE",
        },
    }
    for name, (table, column, target, on_delete) in roundtrip.EXPECTED_FOREIGN_KEYS.items():
        foreign_keys[f"{table}.{name}"] = {
            "table": table,
            "definition": f"FOREIGN KEY ({column}) REFERENCES {target}(id) ON DELETE {on_delete}",
        }
    return {"columns": columns, "indexes": indexes, "checks": checks, "foreign_keys": foreign_keys}


def _reversed(roundtrip, forward: dict) -> dict:
    """What a correct reverse leaves: 0041's additions gone, the two value
    lists narrow again, extraction_runs.document_id NOT NULL."""
    state = copy.deepcopy(roundtrip.without_0041(forward))
    state["columns"].append(_column("extraction_runs", "document_id", "uuid", "uuid", "NO"))
    state["columns"].sort(key=lambda c: c["table"])
    for name, (kept, _added) in roundtrip.CHANGED_CHECKS.items():
        table = next(t for t in roundtrip.TABLES if name.startswith(t))
        state["checks"][f"{table}.{name}"] = {"table": table, "definition": "CHECK (" + ", ".join(kept) + ")"}
    return state


def _again(roundtrip, forward: dict) -> dict:
    """The forward-again state as a real database leaves it: 0041's columns
    re-added at the end of each table."""
    sys.path.insert(0, str(SCRIPTS))
    from roundtrip_columns import columns_after_rerun

    state = copy.deepcopy(forward)
    state["columns"] = columns_after_rerun(forward["columns"], set(roundtrip.EXPECTED_COLUMNS))
    return state


def _run(roundtrip, forward, reversed_=None, again=None, policies=("p",) * 3) -> int:
    return roundtrip.check_states(
        forward,
        _reversed(roundtrip, forward) if reversed_ is None else reversed_,
        _again(roundtrip, forward) if again is None else again,
        policies,
    )


def test_a_correct_round_trip_passes(roundtrip, capsys):
    forward = _forward(roundtrip)
    assert _run(roundtrip, forward) == 0
    out = capsys.readouterr().out
    assert f"::notice title=0041 state (forward)::sha256={roundtrip.sha256(forward)}" in out
    assert "0 problem(s)" in out


def test_a_second_forward_that_left_the_columns_where_they_were_passes_too(roundtrip, capsys):
    forward = _forward(roundtrip)
    assert _run(roundtrip, forward, again=copy.deepcopy(forward)) == 0
    assert "0 problem(s)" in capsys.readouterr().out


def _drop_column(state, table, name):
    state["columns"] = [c for c in state["columns"] if (c["table"], c["column"]) != (table, name)]


def _set_column(state, table, name, **changes):
    next(c for c in state["columns"] if (c["table"], c["column"]) == (table, name)).update(changes)


@pytest.mark.parametrize(
    ("damage", "expected"),
    [
        (
            lambda s: _drop_column(s, "documents", "raw_email_id"),
            "forward: documents.raw_email_id is missing",
        ),
        (
            lambda s: _set_column(s, "raw_emails", "body_state", default=None),
            "forward: raw_emails.body_state is",
        ),
        (
            lambda s: _set_column(s, "raw_emails", "body_bytes", data_type="text", udt_name="text"),
            "forward: raw_emails.body_bytes is",
        ),
        (
            lambda s: s["checks"].pop("raw_emails.raw_emails_body_stored_together"),
            "forward: check raw_emails_body_stored_together is missing",
        ),
        (
            lambda s: s["checks"]["raw_emails.raw_emails_body_label_check"].update(definition="CHECK (true)"),
            "forward: check raw_emails_body_label_check reads",
        ),
        (
            lambda s: s["checks"]["raw_emails.raw_emails_outcome_check"].update(
                definition="CHECK ('processed', 'quarantined', 'rejected')"
            ),
            "forward: check raw_emails_outcome_check reads",
        ),
        (
            lambda s: s["foreign_keys"]["extraction_runs.extraction_runs_raw_email_id_fkey"].update(
                definition="FOREIGN KEY (raw_email_id) REFERENCES raw_emails(id)"
            ),
            "forward: foreign key extraction_runs_raw_email_id_fkey on extraction_runs reads",
        ),
        (
            lambda s: s["foreign_keys"].pop("documents.documents_raw_email_id_fkey"),
            "forward: foreign key documents_raw_email_id_fkey is missing",
        ),
        (
            lambda s: s["indexes"].pop("idx_documents_raw_email"),
            "forward: index idx_documents_raw_email is missing",
        ),
        (
            lambda s: s["indexes"]["idx_extraction_runs_raw_email"].update(predicate=None),
            "forward: index idx_extraction_runs_raw_email reads",
        ),
        (
            lambda s: s["indexes"]["idx_documents_one_body_per_email"].update(unique=False),
            "forward: index idx_documents_one_body_per_email reads",
        ),
        (
            lambda s: s["indexes"]["idx_documents_one_body_per_email"].update(
                predicate="(raw_email_id IS NOT NULL)"
            ),
            "forward: index idx_documents_one_body_per_email reads",
        ),
        (
            lambda s: s["indexes"]["idx_documents_raw_email"].update(unique=True),
            "forward: index idx_documents_raw_email reads",
        ),
        (
            lambda s: _set_column(s, "extraction_runs", "document_id", is_nullable="NO"),
            "forward: extraction_runs.document_id must be nullable",
        ),
    ],
    ids=[
        "column missing",
        "default missing",
        "wrong type",
        "check missing",
        "check changed",
        "value list not widened",
        "foreign key without its delete rule",
        "foreign key missing",
        "index missing",
        "index not partial",
        "body index not unique",
        "body index over every e-mail document",
        "lookup index made unique",
        "document_id still required",
    ],
)
def test_a_forward_state_that_is_not_what_0041_declares_fails(roundtrip, capsys, damage, expected):
    forward = _forward(roundtrip)
    reversed_ = _reversed(roundtrip, forward)
    damage(forward)
    assert _run(roundtrip, forward, reversed_=reversed_, again=copy.deepcopy(forward)) == 1
    out = capsys.readouterr().out
    assert expected in out and "::error title=0041 round trip:" in out


@pytest.mark.parametrize(
    ("leave", "expected"),
    [
        (
            lambda r, f: r["columns"].append(_column("raw_emails", "body_label")),
            "reverse: raw_emails.body_label is still there",
        ),
        (
            lambda r, f: r["checks"].update(
                {"documents.documents_email_part_check": f["checks"]["documents.documents_email_part_check"]}
            ),
            "reverse: check documents_email_part_check is still there",
        ),
        (
            lambda r, f: r["foreign_keys"].update(
                {
                    "documents.documents_raw_email_id_fkey": f["foreign_keys"][
                        "documents.documents_raw_email_id_fkey"
                    ]
                }
            ),
            "reverse: foreign key documents_raw_email_id_fkey is still there",
        ),
        (
            lambda r, f: r["indexes"].update(
                {"idx_documents_raw_email": f["indexes"]["idx_documents_raw_email"]}
            ),
            "reverse: index idx_documents_raw_email is still there",
        ),
        (
            lambda r, f: r["indexes"].update(
                {"idx_documents_one_body_per_email": f["indexes"]["idx_documents_one_body_per_email"]}
            ),
            "reverse: index idx_documents_one_body_per_email is still there",
        ),
        (
            lambda r, f: r["checks"].update(
                {"raw_emails.raw_emails_outcome_check": f["checks"]["raw_emails.raw_emails_outcome_check"]}
            ),
            "reverse: check raw_emails_outcome_check reads",
        ),
        (
            lambda r, f: r["checks"].pop("extraction_runs.extraction_runs_run_kind_check"),
            "reverse: check extraction_runs_run_kind_check is missing",
        ),
        (
            lambda r, f: _set_column(r, "extraction_runs", "document_id", is_nullable="YES"),
            "reverse: extraction_runs.document_id must be NOT NULL again",
        ),
    ],
    ids=[
        "column left",
        "check left",
        "foreign key left",
        "index left",
        "body index left",
        "value list still wide",
        "value list gone",
        "document_id still nullable",
    ],
)
def test_a_reverse_that_leaves_something_behind_fails(roundtrip, capsys, leave, expected):
    forward = _forward(roundtrip)
    reversed_ = _reversed(roundtrip, forward)
    leave(reversed_, forward)
    assert _run(roundtrip, forward, reversed_=reversed_) == 1
    assert expected in capsys.readouterr().out


def test_a_reverse_that_removes_more_than_0041_added_fails(roundtrip, capsys):
    forward = _forward(roundtrip)
    too_much = _reversed(roundtrip, forward)
    _drop_column(too_much, "raw_emails", "outcome")
    del too_much["indexes"]["documents_pkey"]
    del too_much["checks"]["extraction_runs.extraction_runs_run_state_check"]
    del too_much["foreign_keys"]["extraction_runs.extraction_runs_document_id_fkey"]
    assert _run(roundtrip, forward, reversed_=too_much) == 1
    out = capsys.readouterr().out
    for part in ("columns", "indexes", "checks", "foreign_keys"):
        assert f"reverse: the {part} of the four tables changed beyond 0041's" in out


def test_a_second_forward_with_another_column_moved_fails(roundtrip, capsys):
    forward = _forward(roundtrip)
    again = _again(roundtrip, forward)
    again["columns"] = [again["columns"][1], again["columns"][0], *again["columns"][2:]]
    assert _run(roundtrip, forward, again=again) == 1
    assert "forward again: the state differs" in capsys.readouterr().out


def test_a_second_forward_with_a_column_missing_fails(roundtrip, capsys):
    forward = _forward(roundtrip)
    again = _again(roundtrip, forward)
    _drop_column(again, "raw_emails", "outcome")
    assert _run(roundtrip, forward, again=again) == 1
    assert "forward again: the state differs" in capsys.readouterr().out


def test_a_second_forward_that_left_a_value_list_narrow_fails(roundtrip, capsys):
    forward = _forward(roundtrip)
    again = _again(roundtrip, forward)
    again["checks"]["raw_emails.raw_emails_outcome_check"] = {
        "table": "raw_emails",
        "definition": "CHECK (x)",
    }
    assert _run(roundtrip, forward, again=again) == 1
    assert "forward again: the state differs" in capsys.readouterr().out


def test_a_policy_or_grant_that_changed_on_the_way_fails(roundtrip, capsys):
    forward = _forward(roundtrip)
    assert _run(roundtrip, forward, policies=("p", "q", "p")) == 1
    assert "the policies or the function grants changed" in capsys.readouterr().out
