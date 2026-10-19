"""
One test run at a time against a shared database (founder, 2026-09-29), or
the three suites together through the parallel runner (D-211).

Two full API runs against docflow-staging once overlapped by 76 seconds. Test
cleanup hard-deletes its own test tenants, so each run deleted a tenant while
the other was counting them, and both failed the same test for a reason that
had nothing to do with the code under test. This makes a second run refuse to
start instead, naming the run that holds the lock.

The locks are Postgres session-level advisory locks, held on a connection of
their own for the whole run and released when that connection closes -- also
when the process is killed, so a crashed run never leaves one stuck. The
application's DATABASE_URL is Supabase's transaction-mode pooler (port 6543,
D-016), which may hand each transaction a different server connection, so a
session lock taken through it would be held by whichever connection it landed
on. The lock therefore connects through the same pooler's session mode
(port 5432), which keeps one server connection for as long as the client
stays connected. Any other URL (CI's local Postgres) is used as it is.

Each run takes two locks (D-211):
  * its suite's own key, always exclusive: two runs of one suite never overlap;
  * the key every suite shares: exclusive in a normal run, so a normal run
    still has the database to itself, exactly as before; shared in a run the
    parallel runner started (DOCFLOW_TEST_PARALLEL_RUN=1), so the API and
    worker suites it started together may overlap, and nothing else may.

A third key guards the database's global state while the suites overlap
(`shared_state`): the model provider's one row, the tenant-less provider and
dispatcher alerts, and the open dispatch passes that touch every tenant's
waiting documents. A test that writes or asserts on that state holds it.

apps/worker/tests/suite_lock.py is a copy of this file (each app's tests are
their own package); test_suite_lock.py fails if the two differ.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg
import pytest
from sqlalchemy.engine import make_url

# Fixed bigints. The shared key is the one every DocFlow test suite that uses
# a database has always taken (D-180).
SUITE_LOCK_KEY = 5_150_292_026
SUITE_OWN_KEYS = {"api": 5_150_292_027, "worker": 5_150_292_028}
SHARED_STATE_KEY = 5_150_292_029
PARALLEL_RUN_FLAG = "DOCFLOW_TEST_PARALLEL_RUN"
# Long enough for the other suite's longest test that holds the state.
SHARED_STATE_WAIT_SECONDS = 15 * 60
_APP_NAME_PREFIX = "docflow-test-run"

_held: psycopg.Connection | None = None


class SuiteLockHeld(Exception):
    """Another test run holds the lock."""


def parallel_run() -> bool:
    """True only in a run the parallel runner started."""
    return os.environ.get(PARALLEL_RUN_FLAG) == "1"


def session_mode_url(database_url: str) -> str:
    """A libpq URL for the lock's own connection: the Supabase pooler's
    session mode when DATABASE_URL is its transaction mode, else unchanged."""
    url = make_url(database_url).set(drivername="postgresql")
    if url.host and url.host.endswith(".pooler.supabase.com") and url.port == 6543:
        url = url.set(port=5432)
    return url.render_as_string(hide_password=False)


def _holder(conn: psycopg.Connection, key: int) -> str:
    rows = conn.execute(
        """
        SELECT a.application_name
          FROM pg_locks l
          JOIN pg_stat_activity a ON a.pid = l.pid
         WHERE l.locktype = 'advisory' AND l.granted
           AND ((l.classid::bigint << 32) | l.objid::bigint) = %s
         ORDER BY a.application_name
        """,
        (key,),
    ).fetchall()
    if not rows:
        return "another test run (its details aren't visible to this login)"
    # The name carries the run's own start time; backend_start would be the
    # pooler's server connection, which can be older than the run.
    return "; ".join(row[0] or "an unnamed connection" for row in rows)


def acquire(database_url: str, suite: str) -> None:
    """Take this run's locks, or raise SuiteLockHeld naming the holder."""
    global _held
    if _held is not None:
        return
    parallel = parallel_run()
    started = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    mode = " parallel" if parallel else ""
    app_name = (
        f"{_APP_NAME_PREFIX} {suite}{mode} pid={os.getpid()} host={socket.gethostname()} started={started}"
    )
    conn = psycopg.connect(session_mode_url(database_url), autocommit=True)
    # Named with SET, not the connect parameter: Supabase's pooler reports every
    # connection as "Supavisor". Session mode keeps the setting for this client.
    conn.execute("SELECT set_config('application_name', %s, false)", (app_name[:63],))
    own = conn.execute("SELECT pg_try_advisory_lock(%s)", (SUITE_OWN_KEYS[suite],)).fetchone()[0]
    if not own:
        holder = _holder(conn, SUITE_OWN_KEYS[suite])
        conn.close()
        raise SuiteLockHeld(
            f"Another test run is using this database: {holder}.\n"
            f"Two {suite} runs at once delete each other's test data and fail for reasons that "
            "aren't in the code. Wait for it to finish (or stop it), then start this run again."
        )
    take = "pg_try_advisory_lock_shared" if parallel else "pg_try_advisory_lock"
    shared = conn.execute(f"SELECT {take}(%s)", (SUITE_LOCK_KEY,)).fetchone()[0]
    if not shared:
        holder = _holder(conn, SUITE_LOCK_KEY)
        conn.close()  # the own key goes with the connection
        raise SuiteLockHeld(
            f"Another test run is using this database: {holder}.\n"
            "Two runs at once delete each other's test data and fail for reasons that aren't in "
            "the code. Only the suites the parallel runner starts together may overlap. Wait for "
            "it to finish (or stop it), then start this run again."
        )
    _held = conn


def release() -> None:
    global _held
    if _held is not None:
        try:
            _held.execute("SELECT pg_advisory_unlock_all()")
        except psycopg.Error:
            pass  # the connection is already gone, and the locks went with it
        finally:
            _held.close()
            _held = None


@contextmanager
def shared_state() -> Iterator[None]:
    """Hold the database's global state for one test (D-211): waits while a
    test of another suite holds it. On the run lock's own connection, so it
    costs no connection of its own. A no-op in a run with no database."""
    conn = _held
    if conn is None:
        yield
        return
    deadline = time.monotonic() + SHARED_STATE_WAIT_SECONDS
    while not conn.execute("SELECT pg_try_advisory_lock(%s)", (SHARED_STATE_KEY,)).fetchone()[0]:
        if time.monotonic() > deadline:
            raise AssertionError(
                f"waited {SHARED_STATE_WAIT_SECONDS} s for the shared-state lock, held by "
                f"{_holder(conn, SHARED_STATE_KEY)}"
            )
        time.sleep(0.25)
    try:
        yield
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (SHARED_STATE_KEY,))


def _annotate(level: str, message: str) -> None:
    """A GitHub Actions annotation, so CI shows what happened (RUNBOOK 1.6:
    CI job logs can't be read here, annotations can). Silent elsewhere."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        body = message.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::{level} title=suite lock::{body}", flush=True)
        sys.stdout.flush()


def session_start(database_url: str, suite: str) -> None:
    """pytest_sessionstart: take the locks, or stop the run naming the holder.
    Any other failure stops the run too, with its type and message -- never
    the URL, which carries the password."""
    try:
        acquire(database_url, suite)
    except SuiteLockHeld as held:
        _annotate("error", str(held))
        pytest.exit(str(held), returncode=3)
    except Exception as exc:  # noqa: BLE001 -- reported, then the run stops
        message = f"Couldn't take the test-run lock: {type(exc).__name__}: {exc}"
        _annotate("error", message)
        pytest.exit(message, returncode=3)
    mode = " in parallel mode" if parallel_run() else ""
    _annotate("notice", f"taken by {suite} (pid {os.getpid()}){mode}")


def session_finish() -> None:
    """pytest_sessionfinish: release the locks; a failure is reported, never raised."""
    try:
        release()
    except Exception as exc:  # noqa: BLE001 -- the lock goes with the process anyway
        _annotate("warning", f"Couldn't release the test-run lock: {type(exc).__name__}: {exc}")
