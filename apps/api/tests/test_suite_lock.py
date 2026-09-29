"""
tests/suite_lock.py: a second test run against the same database refuses to
start, and says which run holds it (founder, 2026-09-29).
"""

from pathlib import Path

import psycopg
import pytest
from docflow_core.config import get_settings

from tests import suite_lock
from tests.conftest import requires_database

HERE = Path(__file__).resolve().parent


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


@requires_database
def test_this_run_holds_the_lock_and_a_second_run_is_refused_by_name(monkeypatch):
    database_url = get_settings().database_url
    # This run took the lock at session start (conftest.pytest_sessionstart).
    with psycopg.connect(suite_lock.session_mode_url(database_url), autocommit=True) as other:
        taken = other.execute("SELECT pg_try_advisory_lock(%s)", (suite_lock.SUITE_LOCK_KEY,)).fetchone()[0]
        assert taken is False

    # A second run: same code, but not the process that holds the lock.
    monkeypatch.setattr(suite_lock, "_held", None)
    with pytest.raises(suite_lock.SuiteLockHeld) as refused:
        suite_lock.acquire(database_url, suite="api")
    message = str(refused.value)
    assert "Another test run is using this database" in message
    assert "docflow-test-run api pid=" in message
