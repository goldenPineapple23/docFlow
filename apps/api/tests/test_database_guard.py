"""
tests/database_guard.py: a test run refuses any database off this machine
unless the environment carries DOCFLOW_STAGING_RUN=1 (founder, 2026-10-19;
D-211). The worker and core suites run the same check of their own conftest
(test_database_guard_conftest.py in each).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import database_guard

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

# Staging's pooler host has this shape; the password is never in a message.
STAGING = (
    "postgresql://docflow_admin.abcdefghij:not-a-real-password@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
)
POOLER = "aws-0-us-east-1.pooler.supabase.com"
LOCAL = "postgresql://docflow_api:ci-local-only@127.0.0.1:54322/postgres"
BLOCKED = "postgresql://nobody:nothing@db.invalid:5432/none"


def _all(url: str) -> dict[str, str]:
    return dict.fromkeys(database_guard.URL_SETTINGS, url)


def test_the_worker_and_core_copies_are_identical():
    ours = (HERE / "database_guard.py").read_bytes()
    assert (REPO / "apps" / "worker" / "tests" / "database_guard.py").read_bytes() == ours
    assert (REPO / "packages" / "core" / "tests" / "database_guard.py").read_bytes() == ours


def test_a_staging_host_without_the_flag_is_refused_by_name_and_host():
    urls = {**_all(LOCAL), "ADMIN_DATABASE_URL": STAGING}
    found = database_guard.refusals(urls, {"ADMIN_DATABASE_URL": STAGING})
    assert found == [f"ADMIN_DATABASE_URL (from the environment) points at {POOLER}"]
    assert "not-a-real-password" not in "".join(found)


def test_a_staging_host_with_the_flag_passes():
    assert database_guard.refusals(_all(STAGING), {"DOCFLOW_STAGING_RUN": "1"}) == []


@pytest.mark.parametrize("value", ["", "0", "true", "yes", " 1"])
def test_only_the_value_1_is_the_flag(value):
    assert len(database_guard.refusals(_all(STAGING), {"DOCFLOW_STAGING_RUN": value})) == 6


@pytest.mark.parametrize(
    "url",
    [
        LOCAL,
        "postgresql+psycopg://u:p@localhost:5432/postgres",
        "postgresql://u:p@LOCALHOST/postgres",
        BLOCKED,
        "",
    ],
)
def test_this_machine_the_blocked_host_and_no_database_pass(url):
    assert database_guard.refusals(_all(url), {}) == []


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@db.example.test:5432/postgres",
        "postgresql://u:p@127.0.0.2:5432/postgres",  # not one of the three
        "postgresql://u:p@/postgres?host=/var/run/postgresql",  # no host to show
        "not a url at all",
    ],
)
def test_anything_else_is_refused(url):
    assert len(database_guard.refusals({"DATABASE_URL": url}, {})) == 1


def test_a_url_missing_from_the_environment_is_read_from_the_env_file(tmp_path, monkeypatch):
    """The incident's shape: the shell's variable was gone, so `.env` was read."""
    env_file = tmp_path / ".env"
    env_file.write_text(f"DATABASE_URL={STAGING}\n", encoding="utf-8")
    for name in database_guard.URL_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    urls = database_guard.resolved_urls(str(env_file))
    assert urls["DATABASE_URL"] == STAGING
    found = database_guard.refusals(urls, {})
    assert found == [f"DATABASE_URL (from the root .env) points at {POOLER}"]


def test_the_environment_wins_over_the_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(f"DATABASE_URL={STAGING}\n", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", BLOCKED)
    assert database_guard.resolved_urls(str(env_file))["DATABASE_URL"] == BLOCKED


def test_check_raises_without_the_flag_and_never_shows_the_password(monkeypatch):
    monkeypatch.delenv("DOCFLOW_STAGING_RUN", raising=False)
    for name in database_guard.URL_SETTINGS:
        monkeypatch.setenv(name, STAGING)
    with pytest.raises(database_guard.StagingDatabaseRefused) as refused:
        database_guard.check()
    assert "DOCFLOW_STAGING_RUN=1" in str(refused.value)
    assert "not-a-real-password" not in str(refused.value)


# A host that is not on this machine and can never resolve.
OFF_MACHINE = "postgresql://nobody:nothing@staging-stand-in.invalid:5432/none"


def child_env(flag: bool) -> dict[str, str]:
    """Five logins off this machine. DATABASE_URL is empty (no database), so
    a start that passes the guard opens no connection and needs no lock: an
    unresolvable host takes 11 s to fail on Windows, once per schema check."""
    env = {**os.environ, **_all(OFF_MACHINE), "DATABASE_URL": ""}
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


def collect_this_suite(suite_dir: Path, test_file: str, flag: bool) -> subprocess.CompletedProcess:
    """A real pytest start of one suite (collect only: no test runs)."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", test_file],
        cwd=suite_dir,
        env=child_env(flag),
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_the_api_suite_aborts_on_an_off_machine_host_without_the_flag():
    run = collect_this_suite(HERE.parent, "tests/test_database_guard.py", flag=False)
    out = run.stdout + run.stderr
    assert run.returncode != 0
    assert "StagingDatabaseRefused" in out
    assert "ADMIN_DATABASE_URL (from the environment) points at staging-stand-in.invalid" in out


def test_the_api_suite_starts_with_the_flag():
    run = collect_this_suite(HERE.parent, "tests/test_database_guard.py", flag=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "StagingDatabaseRefused" not in run.stdout + run.stderr
