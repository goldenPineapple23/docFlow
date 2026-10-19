"""
The core suite's conftest runs tests/database_guard.py before anything
else (D-211): with the database logins off this machine, the suite refuses to
start without DOCFLOW_STAGING_RUN=1 and starts with it. The guard's own rules
are tested in apps/api/tests/test_database_guard.py.
"""

import os
import subprocess
import sys
from pathlib import Path

from tests import database_guard

SUITE = Path(__file__).resolve().parents[1]
# Not on this machine, and can never resolve.
OFF_MACHINE = "postgresql://nobody:nothing@staging-stand-in.invalid:5432/none"


def child_env(flag: bool) -> dict[str, str]:
    """Five logins off this machine. DATABASE_URL is empty (no database), so
    a start that passes the guard opens no connection and needs no lock."""
    env = {**os.environ, **dict.fromkeys(database_guard.URL_SETTINGS, OFF_MACHINE), "DATABASE_URL": ""}
    env.pop("DOCFLOW_STAGING_RUN", None)
    if flag:
        env["DOCFLOW_STAGING_RUN"] = "1"
    # Fail closed: were the empty value lost on the way, the child would read
    # the root .env's DATABASE_URL instead. Prove it arrives as "".
    seen = subprocess.run(
        [sys.executable, "-c", "import os; print(repr(os.environ.get('DATABASE_URL')))"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert seen.stdout.strip() == "''", seen.stdout + seen.stderr
    return env


def collect_this_suite(flag: bool) -> subprocess.CompletedProcess:
    """A real pytest start of this suite (collect only: no test runs)."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "tests/test_database_guard_conftest.py"],
        cwd=SUITE,
        env=child_env(flag),
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_the_core_suite_aborts_on_an_off_machine_host_without_the_flag():
    run = collect_this_suite(flag=False)
    out = run.stdout + run.stderr
    assert run.returncode != 0
    assert "StagingDatabaseRefused" in out
    assert "ADMIN_DATABASE_URL (from the environment) points at staging-stand-in.invalid" in out


def test_the_core_suite_starts_with_the_flag():
    run = collect_this_suite(flag=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "StagingDatabaseRefused" not in run.stdout + run.stderr
