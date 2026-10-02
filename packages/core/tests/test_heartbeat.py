"""
The worker's external heartbeat (Stage 3e, part C; BUILD-STATUS "3e detailed
design", E4). The ping server and the unclaimed reading are fakes here: what
is proven is when a ping goes, which one, and that nothing about it can fail
a pass or leak the URL. The unclaimed reading against the real database is
in apps/worker/tests/test_dispatch_db.py; the real Healthchecks.io check is
proven at the first worker deploy (stop the worker, the email arrives).
"""

from __future__ import annotations

import logging

import httpx
import pytest

from docflow_core import heartbeat
from docflow_core.config import get_settings
from docflow_core.constants import DISPATCH_UNCLAIMED_ALERT_MIN, HEARTBEAT_PING_MIN

URL = "https://hc-ping.example/acme-test-check-0000"


@pytest.fixture
def pings(monkeypatch):
    """Every URL pinged, in order; the unclaimed age set by the test."""
    sent: list[str] = []
    state = {"age": None, "status": 200}

    def fake_get(url, timeout):
        assert timeout == heartbeat.HEARTBEAT_PING_TIMEOUT_SECONDS
        sent.append(url)
        return httpx.Response(state["status"], request=httpx.Request("GET", url))

    monkeypatch.setenv("HEARTBEAT_URL", URL)
    get_settings.cache_clear()
    monkeypatch.setattr(heartbeat.httpx, "get", fake_get)
    monkeypatch.setattr(heartbeat, "unclaimed_age_seconds", lambda: state["age"])
    heartbeat.reset()
    yield sent, state
    heartbeat.reset()
    get_settings.cache_clear()


def test_a_successful_pass_pings_and_a_failed_one_does_not(pings):
    sent, _ = pings
    assert heartbeat.after_pass(succeeded=False, now=0) is None
    assert heartbeat.after_pass(succeeded=True, now=0) == "success"
    assert sent == [URL]


def test_at_most_one_ping_per_interval(pings):
    sent, _ = pings
    interval = HEARTBEAT_PING_MIN * 60
    for second in range(0, 3 * interval, 30):  # a pass every 30 s for 15 minutes
        heartbeat.after_pass(succeeded=True, now=float(second))
    assert sent == [URL] * 3


def test_an_unclaimed_document_past_the_limit_sends_fail_at_once_and_success_resumes_when_it_clears(pings):
    sent, state = pings
    limit = DISPATCH_UNCLAIMED_ALERT_MIN * 60
    assert heartbeat.after_pass(succeeded=True, now=0) == "success"

    state["age"] = limit  # at the limit: still healthy
    assert heartbeat.after_pass(succeeded=True, now=30) is None
    state["age"] = limit + 1
    assert heartbeat.after_pass(succeeded=True, now=60) == "fail"  # not throttled: a change
    assert heartbeat.after_pass(succeeded=True, now=90) is None  # no success pings meanwhile

    state["age"] = None
    assert heartbeat.after_pass(succeeded=True, now=120) == "success"  # at once, too
    assert sent == [URL, URL + "/fail", URL]


def test_a_ping_that_fails_never_fails_the_pass_and_never_logs_the_url(pings, monkeypatch, caplog):
    sent, state = pings

    def broken(url, timeout):
        raise httpx.ConnectError(f"acme test: cannot reach {url}")

    monkeypatch.setattr(heartbeat.httpx, "get", broken)
    with caplog.at_level(logging.ERROR):
        assert heartbeat.after_pass(succeeded=True, now=0) is None
    assert "heartbeat_ping_failed kind=success error_type=ConnectError" in caplog.text
    assert URL not in caplog.text and "acme-test-check" not in caplog.text

    # A failed ping isn't counted: the next pass tries again.
    monkeypatch.setattr(
        heartbeat.httpx, "get", lambda url, timeout: httpx.Response(200, request=httpx.Request("GET", url))
    )
    assert heartbeat.after_pass(succeeded=True, now=30) == "success"


def test_an_error_answer_counts_as_a_failed_ping(pings):
    _, state = pings
    state["status"] = 429  # Healthchecks.io's rate limit
    assert heartbeat.after_pass(succeeded=True, now=0) is None


def test_no_url_means_no_ping_and_no_database_read(monkeypatch):
    monkeypatch.delenv("HEARTBEAT_URL", raising=False)
    get_settings.cache_clear()

    def must_not_run():
        raise AssertionError("read the database with no heartbeat configured")

    monkeypatch.setattr(heartbeat, "unclaimed_age_seconds", must_not_run)
    monkeypatch.setattr(heartbeat.httpx, "get", lambda *a, **k: pytest.fail("pinged with no URL"))
    heartbeat.reset()
    try:
        assert heartbeat.after_pass(succeeded=True, now=0) is None
    finally:
        get_settings.cache_clear()


def test_an_unreadable_database_sends_no_ping(pings, monkeypatch):
    sent, _ = pings

    def unreachable():
        raise ConnectionError("acme test: no database")

    monkeypatch.setattr(heartbeat, "unclaimed_age_seconds", unreachable)
    assert heartbeat.after_pass(succeeded=True, now=0) is None
    assert sent == []


def test_only_the_dispatch_task_pings():
    """The pass at the end of a document task must never ping: a busy
    documents worker would hide a stopped dispatch process."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    callers = sorted(
        path.relative_to(repo).as_posix()
        for root in (repo / "apps", repo / "packages" / "core" / "docflow_core")
        for path in root.rglob("*.py")
        if "tests" not in path.parts
        and ".venv" not in path.parts
        and "heartbeat.after_pass" in path.read_text(encoding="utf-8")
    )
    assert callers == ["apps/worker/app/tasks/dispatch.py"]


def test_the_unclaimed_limit_stays_below_the_stuck_sweeps_timeout_and_above_the_longest_other_task():
    """Founder, Q9 (2026-10-02). Below: the stuck sweep returns a document
    unclaimed for STUCK_PROCESSING_TIMEOUT_MIN to waiting, and its next
    dispatch restarts the age (test_dispatch_db.py proves the restart), so a
    limit at or above it could never fire. Above: every non-document task
    shares the documents worker's slots, so a healthy worker can hold a
    dispatched document for the longest of their hard limits."""
    from docflow_core import constants

    others = [
        constants.EXPORT_TASK_TIME_LIMIT_SECONDS,
        constants.IMPORT_TASK_TIME_LIMIT_SECONDS,
        constants.ROLLUP_TASK_TIME_LIMIT_SECONDS,
        constants.SCHEDULED_JOBS_TASK_TIME_LIMIT_SECONDS,
        constants.LIFECYCLE_SWEEP_TASK_TIME_LIMIT_SECONDS,
        constants.STUCK_SWEEP_TASK_TIME_LIMIT_SECONDS,
    ]
    assert max(others) < DISPATCH_UNCLAIMED_ALERT_MIN * 60
    assert DISPATCH_UNCLAIMED_ALERT_MIN < constants.STUCK_PROCESSING_TIMEOUT_MIN
