"""
The live-bucket tests' refusal (tests/bucket_guard.py): off this machine they
run only with DOCFLOW_STAGING_RUN=1 in the process's own environment. No
network: the guard only reads an endpoint and a mapping.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import bucket_guard

CORE = Path(__file__).resolve().parents[1]

# The shapes CI's local stack and a hosted project use; no real project ref.
LOCAL = (
    "http://127.0.0.1:25321/storage/v1/s3",
    "http://localhost:25321/storage/v1/s3",
    "http://[::1]:25321/storage/v1/s3",
    "HTTP://LocalHost:25321/storage/v1/s3",
)
REMOTE = (
    "https://abcdefghijklmnopqrst.storage.supabase.co/storage/v1/s3",
    "http://127.0.0.1.example.test/storage/v1/s3",
    "http://localhost@storage.example.test/storage/v1/s3",
    "http://10.0.0.5:9000/storage/v1/s3",
    "not a url",
    "",
)


@pytest.mark.parametrize("endpoint", LOCAL)
def test_storage_on_this_machine_runs_without_the_flag(endpoint):
    """CI's case: local_storage_env.py writes a loopback endpoint, so CI runs
    the live-bucket tests as it did before, with no flag."""
    assert bucket_guard.refusal(endpoint, {}) is None


@pytest.mark.parametrize("endpoint", REMOTE)
def test_storage_off_this_machine_is_refused_without_the_flag(endpoint):
    reason = bucket_guard.refusal(endpoint, {})
    assert reason is not None and reason.startswith("refused:")
    assert "DOCFLOW_STAGING_RUN=1" in reason
    # The endpoint itself is never named: a staging host carries the project's reference.
    assert "supabase.co" not in reason and "example.test" not in reason


@pytest.mark.parametrize("value", ["0", "", "true", "yes", " 1", "1 "])
def test_only_the_flag_set_to_1_opens_it(value):
    assert bucket_guard.refusal(REMOTE[0], {"DOCFLOW_STAGING_RUN": value}) is not None


def test_a_granted_staging_run_opens_it():
    assert bucket_guard.refusal(REMOTE[0], {"DOCFLOW_STAGING_RUN": "1"}) is None


def _run_live_module(endpoint: str) -> subprocess.CompletedProcess:
    """Run test_storage_bucket_live.py in a child process with Storage set to
    `endpoint`, no DOCFLOW_STAGING_RUN, and every other outside service
    pointed nowhere (a dead proxy, db.invalid), so a request that got past
    the guard would fail rather than reach anything."""
    env = {k: v for k, v in os.environ.items() if k != "DOCFLOW_STAGING_RUN"}
    blocked = "postgresql://nobody:nothing@db.invalid:5432/none"
    env.update(
        {
            "STORAGE_S3_ENDPOINT": endpoint,
            "DOCFLOW_TEST_ANON_KEY": "test-anon-key",
            "DATABASE_URL": blocked,
            "API_DATABASE_URL": blocked,
            "WORKER_DATABASE_URL": blocked,
            "ADMIN_DATABASE_URL": blocked,
            "STRIPE_DATABASE_URL": blocked,
            "SUPERUSER_DATABASE_URL": blocked,
            "HTTP_PROXY": "http://proxy.invalid:9",
            "HTTPS_PROXY": "http://proxy.invalid:9",
            "NO_PROXY": "",
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
         "tests/test_storage_bucket_live.py"],
        cwd=CORE, env=env, capture_output=True, text=True, timeout=120,
    )


def test_every_live_test_is_refused_before_its_fixtures_off_this_machine():
    """The module's own mark: with a remote endpoint and no flag, all four
    live tests are skipped with the refusal, and none of them, nor the probe
    fixture, runs. A storage call would have failed against the dead proxy."""
    done = _run_live_module(REMOTE[0])
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    assert "4 skipped" in out and "passed" not in out and "failed" not in out and "error" not in out.lower()
    assert out.count("refused: the Storage endpoint is not on this machine") == 4, out
    assert "storage_call_failed" not in out and "supabase.co" not in out


def test_the_flag_is_read_from_the_process_environment_only():
    """The module passes os.environ, never the settings, which also read `.env`."""
    src = (CORE / "tests" / "test_storage_bucket_live.py").read_text(encoding="utf-8")
    assert 'bucket_guard.refusal(get_settings().storage_s3_endpoint or "", os.environ)' in src
    guard = (CORE / "tests" / "bucket_guard.py").read_text(encoding="utf-8")
    assert "get_settings" not in guard and "dotenv" not in guard
