"""
`docflow_core.faults` (Stage 4, PR 1a; DECISIONS.md D-203): where one of our
own steps failed, said with the exception's type and our code location and
never with its message (CLAUDE.md Section 7.10) -- and the key an alert is
raised under once per state.

No database: the one statement `state_key` runs is answered by a stand-in.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from docflow_core import faults

TENANT = UUID("00000000-0000-4000-8000-00000000f001")
# Unmistakably a value: if it shows in a log line, a customer's would too.
BOUND_VALUE = "Acme Test Buyer 47.50 SECRET-BOUND-VALUE"


class _Session:
    """Answers `state_key`'s one read (the database's UTC date) and records
    every statement."""

    def __init__(self, day: str = "2026-10-13"):
        self.day = day
        self.statements: list[tuple[str, dict]] = []

    def execute(self, statement, params=None):
        self.statements.append((str(statement), params or {}))
        return self

    def scalar_one(self):
        return self.day


def _library_raises():
    json.loads("{")  # the library raises, from its own frames


def _database_error():
    """A database error as SQLAlchemy raises it: its text carries the
    statement and the values bound to it."""
    raise IntegrityError(
        "INSERT INTO buyers (name) VALUES (%(name)s)",
        {"name": BOUND_VALUE},
        Exception(f"duplicate key value violates unique constraint: Key (name)=({BOUND_VALUE})"),
    )


def _caught(function) -> BaseException:
    try:
        function()
    except Exception as exc:  # noqa: BLE001 -- the test wants whatever it raises
        return exc
    raise AssertionError("nothing was raised")


# ── describe: the type and our own innermost frame ───────────────────────────


def test_the_location_is_the_innermost_frame_of_our_own_code_not_the_librarys():
    exc = _caught(_library_raises)
    fault = faults.describe(exc, own=("tests",))
    assert fault.exception_type == "JSONDecodeError"
    assert (fault.module, fault.function) == (__name__, "_library_raises")
    assert fault.location == f"{__name__}:_library_raises:{fault.line}" and fault.line > 0


def test_with_the_real_package_list_a_frame_in_core_is_ours():
    class Broken:
        def execute(self, *args, **kwargs):
            raise RuntimeError("the database went away")

    connection_fault = faults.Fault("OperationalError", "docflow_core.x", "f", 1)
    exc = _caught(
        lambda: faults.state_key(
            Broken(), alert_type="document_failed", tenant_id=TENANT, step="saving", fault=connection_fault
        )
    )
    fault = faults.describe(exc)
    assert (fault.exception_type, fault.module, fault.function) == (
        "RuntimeError",
        "docflow_core.faults",
        "state_key",
    )


def test_with_no_frame_of_ours_the_location_says_so_instead_of_guessing():
    fault = faults.describe(_caught(_library_raises), own=("no_such_package",))
    assert fault.location == "unknown:unknown:0" and fault.exception_type == "JSONDecodeError"


# ── for_log: the frames, and never the message ───────────────────────────────


def test_the_log_text_has_the_type_the_location_and_the_frames():
    exc = _caught(_library_raises)
    text = faults.for_log(exc)
    first, _, rest = text.partition("\n")
    assert first.startswith("error_type=JSONDecodeError location=")
    assert 'File "' in rest and "in _library_raises" in rest and "json.loads" in rest


def test_a_database_errors_bound_values_are_not_in_the_log_text():
    """Founder, 2026-10-13: the stack stays, the message goes. The error's own
    text carries the bound value (first assertion), so a `logger.exception`
    would have printed it; this prints the type, the location and the frames."""
    exc = _caught(_database_error)
    assert BOUND_VALUE in str(exc), "the stand-in must carry a bound value, or this proves nothing"
    text = faults.for_log(exc)
    assert BOUND_VALUE not in text and "SECRET" not in text and "INSERT INTO" not in text
    assert "error_type=IntegrityError" in text and "in _database_error" in text


def test_a_messages_runtime_value_is_not_in_the_log_text_either():
    """A frame shows its line of source code, which is ours and holds no
    data; the value that line put into the message at run time is not there."""
    value = "-".join(["SECRET", "RUNTIME", "VALUE"])

    def raises():
        raise ValueError(f"po_number {value}")

    exc = _caught(raises)
    assert value in str(exc)
    text = faults.for_log(exc)
    assert value not in text and "error_type=ValueError" in text
    assert 'raise ValueError(f"po_number {value}")' in text, "the source line itself is shown"


def test_a_chained_exception_is_not_printed():
    def raises():
        try:
            _database_error()
        except IntegrityError as exc:
            raise RuntimeError("wrapped") from exc

    text = faults.for_log(_caught(raises))
    assert BOUND_VALUE not in text and "direct cause" not in text and "error_type=RuntimeError" in text


# ── release ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("registry.fly.io/docflow-worker-staging:deployment-01ABCDEF", "deployment-01ABCDEF"),
        ("registry.fly.io/docflow-worker@sha256:0123abcd", "0123abcd"),
        ("", faults.UNVERSIONED),
        (None, faults.UNVERSIONED),
    ],
)
def test_the_release_is_the_image_flys_machine_started(monkeypatch, ref, expected):
    if ref is None:
        monkeypatch.delenv("FLY_IMAGE_REF", raising=False)
    else:
        monkeypatch.setenv("FLY_IMAGE_REF", ref)
    assert faults.release() == expected


# ── state_key ───────────────────────────────────────────────────────────────

FAULT = faults.Fault("TypeError", "docflow_core.matching", "resolve_line", 412)


def _key(session=None, **changes) -> str:
    args = {"alert_type": "pipeline_step_failed", "tenant_id": TENANT, "step": "matching", "fault": FAULT}
    args.update(changes)
    return faults.state_key(session or _Session(), **args)


def test_the_key_names_the_type_the_tenant_the_step_the_fault_and_the_release(monkeypatch):
    monkeypatch.setenv("FLY_IMAGE_REF", "registry.fly.io/app:deployment-01XYZ")
    session = _Session()
    assert _key(session) == (
        f"fault:pipeline_step_failed:{TENANT}:matching:TypeError:docflow_core.matching:resolve_line:412"
        ":deployment-01XYZ"
    )
    assert session.statements == [], "a fault that isn't a connection error reads nothing"


@pytest.mark.parametrize(
    "changes",
    [
        {"alert_type": "document_failed"},
        {"tenant_id": UUID("00000000-0000-4000-8000-00000000f002")},
        {"step": "duplicate_detection"},
        {"fault": faults.Fault("KeyError", "docflow_core.matching", "resolve_line", 412)},
        {"fault": faults.Fault("TypeError", "docflow_core.buyers", "resolve_line", 412)},
        {"fault": faults.Fault("TypeError", "docflow_core.matching", "load_catalog", 412)},
        {"fault": faults.Fault("TypeError", "docflow_core.matching", "resolve_line", 413)},
    ],
    ids=["alert type", "tenant", "step", "exception type", "module", "function", "line"],
)
def test_a_change_in_any_part_is_a_different_state(changes):
    assert _key(**changes) != _key()


def test_a_new_release_is_a_different_state(monkeypatch):
    monkeypatch.setenv("FLY_IMAGE_REF", "registry.fly.io/app:deployment-01")
    before = _key()
    monkeypatch.setenv("FLY_IMAGE_REF", "registry.fly.io/app:deployment-02")
    assert _key() != before


def test_a_parse_fault_is_keyed_by_the_parse_apps_release_not_this_ones(monkeypatch):
    """D-203, PR 1b-i: a parse job's fault happened in the parse app's
    release, which the worker is given in the answer."""
    monkeypatch.setenv("FLY_IMAGE_REF", "registry.fly.io/docflow-worker-staging:deployment-W1")
    key = _key(release_tag="deployment-P7")
    assert key.endswith(":deployment-P7") and "deployment-W1" not in key
    assert _key(release_tag="deployment-P8") != key


def test_the_same_state_is_the_same_key_on_any_day():
    assert _key(_Session("2026-10-13")) == _key(_Session("2026-12-25"))


@pytest.mark.parametrize("exception_type", sorted(faults.CONNECTION_ERRORS))
def test_a_connection_error_also_carries_the_databases_utc_date(exception_type):
    """Founder, 2026-10-13: until the database retry exists (0038), a second
    outage in the same release must be raised again."""
    fault = faults.Fault(exception_type, "docflow_core.matching", "load_catalog", 88)
    monday, tuesday = _Session("2026-10-12"), _Session("2026-10-13")
    first, second = _key(monday, fault=fault), _key(tuesday, fault=fault)
    assert first.endswith(":2026-10-12") and second.endswith(":2026-10-13") and first != second
    assert _key(_Session("2026-10-12"), fault=fault) == first
    assert len(monday.statements) == 1 and "now()" in monday.statements[0][0]


def test_the_connection_errors_are_the_two_the_database_retry_will_take():
    assert faults.CONNECTION_ERRORS == {"OperationalError", "InterfaceError"}
    assert OperationalError.__name__ in faults.CONNECTION_ERRORS


# ── note_on_document ────────────────────────────────────────────────────────


def test_the_key_is_added_to_the_document_once_and_never_as_a_list():
    """One UPDATE that appends only when the key isn't there. The key travels
    as text: this project's driver sends a Python list as jsonb, which a
    text[] column would refuse."""
    session = _Session()
    document_id = UUID("00000000-0000-4000-8000-00000000d001")
    faults.note_on_document(session, document_id, "fault:x")
    ((sql, params),) = session.statements
    assert "UPDATE documents" in sql and "array_append" in sql and "NOT (" in sql and "@>" in sql
    assert params == {"id": str(document_id), "key": "fault:x"}
    assert all(not isinstance(value, list | tuple) for value in params.values())
