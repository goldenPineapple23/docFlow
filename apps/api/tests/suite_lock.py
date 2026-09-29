"""
One test run at a time against a shared database (founder, 2026-09-29).

Two full API runs against docflow-staging once overlapped by 76 seconds. Test
cleanup hard-deletes its own test tenants, so each run deleted a tenant while
the other was counting them, and both failed the same test for a reason that
had nothing to do with the code under test. This makes a second run refuse to
start instead, naming the run that holds the lock.

The lock is a Postgres session-level advisory lock, held on a connection of
its own for the whole run and released when that connection closes -- also
when the process is killed, so a crashed run never leaves it stuck. The
application's DATABASE_URL is Supabase's transaction-mode pooler (port 6543,
D-016), which may hand each transaction a different server connection, so a
session lock taken through it would be held by whichever connection it landed
on. The lock therefore connects through the same pooler's session mode
(port 5432), which keeps one server connection for as long as the client
stays connected. Any other URL (CI's local Postgres) is used as it is.

The API and worker suites share one key: both write to the same database.

apps/worker/tests/suite_lock.py is a copy of this file (each app's tests are
their own package); test_suite_lock.py fails if the two differ.
"""

from __future__ import annotations

import os
import socket
from datetime import datetime, timezone

import psycopg
from sqlalchemy.engine import make_url

# Any fixed bigint; shared by every DocFlow test suite that uses a database.
SUITE_LOCK_KEY = 5_150_292_026
_APP_NAME_PREFIX = "docflow-test-run"

_held: psycopg.Connection | None = None


class SuiteLockHeld(Exception):
    """Another test run holds the lock."""


def session_mode_url(database_url: str) -> str:
    """A libpq URL for the lock's own connection: the Supabase pooler's
    session mode when DATABASE_URL is its transaction mode, else unchanged."""
    url = make_url(database_url).set(drivername="postgresql")
    if url.host and url.host.endswith(".pooler.supabase.com") and url.port == 6543:
        url = url.set(port=5432)
    return url.render_as_string(hide_password=False)


def _holder(conn: psycopg.Connection) -> str:
    row = conn.execute(
        """
        SELECT a.application_name
          FROM pg_locks l
          JOIN pg_stat_activity a ON a.pid = l.pid
         WHERE l.locktype = 'advisory' AND l.granted
           AND ((l.classid::bigint << 32) | l.objid::bigint) = %s
        """,
        (SUITE_LOCK_KEY,),
    ).fetchone()
    if row is None:
        return "another test run (its details aren't visible to this login)"
    # The name carries the run's own start time; backend_start would be the
    # pooler's server connection, which can be older than the run.
    return row[0] or "an unnamed connection"


def acquire(database_url: str, suite: str) -> None:
    """Take the lock for this process, or raise SuiteLockHeld naming the holder."""
    global _held
    if _held is not None:
        return
    started = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    app_name = f"{_APP_NAME_PREFIX} {suite} pid={os.getpid()} host={socket.gethostname()} started={started}"
    conn = psycopg.connect(session_mode_url(database_url), autocommit=True)
    # Named with SET, not the connect parameter: Supabase's pooler reports every
    # connection as "Supavisor". Session mode keeps the setting for this client.
    conn.execute("SELECT set_config('application_name', %s, false)", (app_name[:63],))
    got = conn.execute("SELECT pg_try_advisory_lock(%s)", (SUITE_LOCK_KEY,)).fetchone()[0]
    if not got:
        holder = _holder(conn)
        conn.close()
        raise SuiteLockHeld(
            f"Another test run is using this database: {holder}.\n"
            "Two runs at once delete each other's test data and fail for reasons that aren't in "
            "the code. Wait for it to finish (or stop it), then start this run again."
        )
    _held = conn


def release() -> None:
    global _held
    if _held is not None:
        try:
            _held.execute("SELECT pg_advisory_unlock(%s)", (SUITE_LOCK_KEY,))
        except psycopg.Error:
            pass  # the connection is already gone, and the lock went with it
        finally:
            _held.close()
            _held = None
