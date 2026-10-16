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

import psycopg
import pytest
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError

from docflow_core import faults

TENANT = UUID("00000000-0000-4000-8000-00000000f001")
# Unmistakably a value: if it shows in a log line, a customer's would too.
BOUND_VALUE = "Acme Test Buyer 47.50 SECRET-BOUND-VALUE"


class _Session:
    """Records every statement, and would answer a read with `day`: the
    tests use it to show `state_key` reads nothing (it read the database's
    UTC date for connection errors until Q14, D-206)."""

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

    exc = _caught(lambda: faults.note_on_document(Broken(), TENANT, "a-key"))
    fault = faults.describe(exc)
    assert (fault.exception_type, fault.module, fault.function) == (
        "RuntimeError",
        "docflow_core.faults",
        "note_on_document",
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


def test_a_database_error_carries_its_sqlstate_so_a_deadlock_and_a_timeout_are_two_states():
    """Q14 (founder, 2026-10-16; D-206)."""
    deadlock = faults.Fault("OperationalError", "docflow_core.matching", "load_catalog", 88, sqlstate="40P01")
    timeout = faults.Fault("OperationalError", "docflow_core.matching", "load_catalog", 88, sqlstate="57014")
    session = _Session("2026-10-13")
    first, second = _key(session, fault=deadlock), _key(session, fault=timeout)
    assert first.endswith(":40P01") and second.endswith(":57014") and first != second
    assert session.statements == [], "the key reads nothing from the database"


def test_no_key_carries_the_date_any_more():
    """The UTC date connection errors carried until the database retry
    existed (founder, 2026-10-13) is gone: an outage now waits instead."""
    fault = faults.Fault("OperationalError", "docflow_core.matching", "load_catalog", 88)
    assert _key(_Session("2026-10-12"), fault=fault) == _key(_Session("2026-10-13"), fault=fault)
    assert not hasattr(faults, "CONNECTION_ERRORS")


# ── database_unavailable: exactly the outage classes (D-206) ─────────────────


def _wrapped(driver: BaseException, *, invalidated: bool = False) -> BaseException:
    """As SQLAlchemy raises it on the real path."""
    return OperationalError("SELECT 1", {}, driver, connection_invalidated=invalidated)


@pytest.mark.parametrize(
    "exc",
    [
        _wrapped(psycopg.errors.ConnectionException()),  # 08000
        _wrapped(psycopg.errors.ConnectionFailure()),  # 08006
        _wrapped(psycopg.errors.SqlclientUnableToEstablishSqlconnection()),  # 08001
        _wrapped(psycopg.errors.ProtocolViolation()),  # 08P01
        _wrapped(psycopg.errors.AdminShutdown()),  # 57P01
        _wrapped(psycopg.errors.CrashShutdown()),  # 57P02
        _wrapped(psycopg.errors.CannotConnectNow()),  # 57P03
        _wrapped(psycopg.errors.TooManyConnections()),  # 53300
        _wrapped(psycopg.errors.ConnectionTimeout()),
        _wrapped(psycopg.OperationalError("connection failed")),  # before any server answered
        _wrapped(psycopg.errors.DeadlockDetected(), invalidated=True),  # the connection is gone
        InterfaceError("SELECT 1", {}, psycopg.InterfaceError(), connection_invalidated=True),
        psycopg.errors.AdminShutdown(),  # unwrapped, as the driver raises it
    ],
    ids=[
        "08000",
        "08006",
        "08001",
        "08P01",
        "57P01",
        "57P02",
        "57P03",
        "53300",
        "connect timeout",
        "connect failed, no sqlstate",
        "connection invalidated",
        "InterfaceError on a closed connection",
        "unwrapped 57P01",
    ],
)
def test_an_outage_is_database_unavailable(exc):
    assert faults.database_unavailable(exc)


@pytest.mark.parametrize(
    "exc",
    [
        _wrapped(psycopg.errors.DeadlockDetected()),  # 40P01
        _wrapped(psycopg.errors.SerializationFailure()),  # 40001
        _wrapped(psycopg.errors.QueryCanceled()),  # 57014, a statement timeout
        _wrapped(psycopg.errors.LockNotAvailable()),  # 55P03, a lock timeout
        _wrapped(psycopg.errors.InvalidPassword()),  # 28P01, our login
        _wrapped(psycopg.errors.DiskFull()),  # 53100
        _wrapped(psycopg.errors.OutOfMemory()),  # 53200
        _wrapped(psycopg.errors.PipelineAborted()),  # no sqlstate, but a subclass
        _wrapped(psycopg.errors.CancellationTimeout()),  # no sqlstate, but a subclass
        IntegrityError("INSERT", {}, psycopg.errors.UniqueViolation()),  # 23505
        InterfaceError("SELECT 1", {}, psycopg.InterfaceError()),  # not on a closed connection
        RuntimeError("a bug of ours"),
    ],
    ids=[
        "40P01",
        "40001",
        "57014",
        "55P03",
        "28P01",
        "53100",
        "53200",
        "PipelineAborted",
        "CancellationTimeout",
        "23505",
        "InterfaceError, connection open",
        "not a database error",
    ],
)
def test_anything_else_is_not_an_outage(exc):
    assert not faults.database_unavailable(exc)


def test_the_sqlstate_is_read_from_the_drivers_original_exception():
    exc = _caught(lambda: (_ for _ in ()).throw(_wrapped(psycopg.errors.DeadlockDetected())))
    assert faults.sqlstate(exc) == "40P01"
    assert faults.describe(exc).sqlstate == "40P01"
    assert faults.sqlstate(RuntimeError("x")) is None
    assert faults.sqlstate(_wrapped(psycopg.OperationalError("connection failed"))) is None


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
