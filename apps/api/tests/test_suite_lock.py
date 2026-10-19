"""
tests/suite_lock.py: a second test run against the same database refuses to
start, and says which run holds it (founder, 2026-09-29). Only the suites the
parallel runner starts together may overlap, and while they do, a test that
touches the database's global state holds it (D-211).
"""

from pathlib import Path

import psycopg
import pytest
from docflow_core.config import get_settings

from tests import suite_lock
from tests.conftest import requires_database

HERE = Path(__file__).resolve().parent
# A suite key no real suite uses, so this test's second "run" never collides
# with the other suite of a parallel run.
PROBE_KEY = 5_150_292_099


def test_the_worker_copy_is_identical():
    worker_copy = HERE.parents[1] / "worker" / "tests" / "suite_lock.py"
    assert worker_copy.read_bytes() == (HERE / "suite_lock.py").read_bytes()


def test_the_transaction_pooler_becomes_its_session_mode():
    url = suite_lock.session_mode_url(
        "postgresql+psycopg://docflow_app.abc:pw@aws-0-us-east-1.pooler.supabase.com:6543/postgres"
    )
    assert url == "postgresql://docflow_app.abc:pw@aws-0-us-east-1.pooler.supabase.com:5432/postgres"


def test_any_other_database_is_used_as_it_is():
    url = suite_lock.session_mode_url("postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres")
    assert url == "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


@pytest.mark.parametrize(("value", "parallel"), [("1", True), ("", False), ("true", False), ("0", False)])
def test_only_the_runners_flag_makes_a_parallel_run(monkeypatch, value, parallel):
    monkeypatch.setenv("DOCFLOW_TEST_PARALLEL_RUN", value)
    assert suite_lock.parallel_run() is parallel


def test_every_key_is_its_own():
    keys = [suite_lock.SUITE_LOCK_KEY, *suite_lock.SUITE_OWN_KEYS.values(), suite_lock.SHARED_STATE_KEY]
    assert len(set(keys)) == len(keys) and PROBE_KEY not in keys


def _session_url() -> str:
    return suite_lock.session_mode_url(get_settings().database_url)


@requires_database
def test_this_run_holds_the_lock_and_a_second_run_is_refused_by_name(monkeypatch):
    database_url = get_settings().database_url
    # This run took its locks at session start (conftest.pytest_sessionstart).
    with psycopg.connect(_session_url(), autocommit=True) as other:
        taken = other.execute("SELECT pg_try_advisory_lock(%s)", (suite_lock.SUITE_LOCK_KEY,)).fetchone()[0]
        assert taken is False
        api_key = suite_lock.SUITE_OWN_KEYS["api"]
        own = other.execute("SELECT pg_try_advisory_lock(%s)", (api_key,)).fetchone()[0]
        assert own is False

    # A second API run: same code, but not the process that holds the lock.
    monkeypatch.setattr(suite_lock, "_held", None)
    with pytest.raises(suite_lock.SuiteLockHeld) as refused:
        suite_lock.acquire(database_url, suite="api")
    message = str(refused.value)
    assert "Another test run is using this database" in message
    assert "docflow-test-run api" in message and "pid=" in message


@requires_database
def test_another_suite_may_start_only_in_the_mode_this_run_is_in(monkeypatch):
    """A normal run has the database to itself; a run the parallel runner
    started lets the other suites it started share it."""
    database_url = get_settings().database_url
    monkeypatch.setitem(suite_lock.SUITE_OWN_KEYS, "probe", PROBE_KEY)
    monkeypatch.setattr(suite_lock, "_held", None)
    if suite_lock.parallel_run():
        suite_lock.acquire(database_url, suite="probe")
        probe = suite_lock._held
        assert probe is not None
        probe.close()  # its locks go with it
    else:
        with pytest.raises(suite_lock.SuiteLockHeld) as refused:
            suite_lock.acquire(database_url, suite="probe")
        assert "docflow-test-run api" in str(refused.value)
        # Refused on the shared key, so the probe's own key went with its connection.
        with psycopg.connect(_session_url(), autocommit=True) as other:
            assert other.execute("SELECT pg_try_advisory_lock(%s)", (PROBE_KEY,)).fetchone()[0] is True


@requires_database
def test_the_shared_state_is_held_inside_the_block_and_a_second_holder_waits_then_says_who(monkeypatch):
    with suite_lock.shared_state():
        with psycopg.connect(_session_url(), autocommit=True) as other:
            key = suite_lock.SHARED_STATE_KEY
            taken = other.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()[0]
            assert taken is False
        # Another suite's test, on its own connection, waits -- here for a
        # second only -- and then names the holder.
        with psycopg.connect(_session_url(), autocommit=True) as second:
            monkeypatch.setattr(suite_lock, "_held", second)
            monkeypatch.setattr(suite_lock, "SHARED_STATE_WAIT_SECONDS", 1)
            with pytest.raises(AssertionError) as waited:
                with suite_lock.shared_state():
                    pass
            monkeypatch.undo()
        assert "docflow-test-run api" in str(waited.value)


def test_the_shared_state_is_a_no_op_without_a_database(monkeypatch):
    monkeypatch.setattr(suite_lock, "_held", None)
    with suite_lock.shared_state():
        pass
