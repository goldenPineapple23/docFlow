"""
The worker's restart record against the real database (Stage 3e, part B;
BUILD-STATUS "3e detailed design", E3).

`worker_starts` has no delete path (no policy, no function), so a start a
test commits stays. Until 2026-10-05 these tests committed six a run, and
each counted toward `worker_restarting` for an hour on a database a real
worker shares (found at RUNBOOK 10.2 step 5). So every start here is recorded
inside one transaction on the worker's own login that is rolled back
(`rolled_back_worker_starts`, conftest.py; founder, 2026-10-05). The real
function, the real `dispatcher_raise` policy and the real unique index all
run; nothing is left, and no alert that was already there is deleted.

Inside that transaction only docflow_worker sees what it wrote, and 0036 lets
it read neither `worker_starts` nor `founder_alerts`. So what is checked is
what the worker itself is told: the count `record_worker_start()` returns,
and `raise_alert`'s answer from the real INSERT. The counts are relative:
whatever earlier starts the window holds, each start adds one.

That a COMMITTED start appears in the count /healthz reads needs a commit.
One test does it, and only on CI's own database, which is thrown away with
the job (`requires_throwaway_ci_database`; it is checked again at the moment
of committing). On docflow-staging it skips (.github/approved-skips.txt,
`worker-staging`; D-189), and the same thing is confirmed by hand at the
first worker deploy (RUNBOOK 9.2).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from docflow_core import db, founder_alerts, worker_starts
from docflow_core.constants import WORKER_RESTART_ALERT_STARTS
from docflow_core.db import platform_session
from sqlalchemy import text

import tests.conftest as suite
from tests.conftest import (
    WorkerStartsCheck,
    is_throwaway_ci_database,
    requires_stage3d_schema,
    requires_throwaway_ci_database,
    worker_starts_committed_on_ci,
    worker_starts_last_hour,
    worker_starts_rolled_back,
)

pytestmark = [requires_stage3d_schema, pytest.mark.real_dispatch]


def _a_restart_alert_is_already_open_this_hour() -> bool:
    """A real one, raised by a real worker: it is left alone, and this hour's
    key is taken, so the test's own alert is refused as the second of the hour."""
    with platform_session() as session:
        return bool(
            session.execute(
                text(
                    "SELECT count(*) FROM founder_alerts WHERE acknowledged_at IS NULL AND dedupe_key = "
                    "'worker_restarting:' || to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24')"
                )
            ).scalar_one()
        )


@pytest.fixture
def machine(monkeypatch, rolled_back_worker_starts):
    name = f"acme-test-machine-{uuid4().hex[:8]}"
    monkeypatch.setenv("FLY_MACHINE_ID", name)
    monkeypatch.setenv("FLY_IMAGE_REF", "registry.fly.io/acme-test:deployment-test")
    return name


@pytest.fixture
def alerts(monkeypatch):
    """Each alert record_start() asks for, with the real raise_alert's answer."""
    asked: list[dict] = []
    real = founder_alerts.raise_alert

    def recorded(session, **kwargs):
        raised = real(session, **kwargs)
        asked.append({**kwargs, "raised": raised})
        return raised

    monkeypatch.setattr(founder_alerts, "raise_alert", recorded)
    return asked


def test_each_start_is_recorded_and_counted_with_the_ones_before_it(machine):
    seen_by_others = worker_starts_last_hour()
    first = worker_starts.record_start()
    second = worker_starts.record_start()
    assert first is not None and second == first + 1
    # Not committed: the Console's count has not moved (it only falls, by ageing).
    assert worker_starts_last_hour() <= seen_by_others


def test_the_third_start_in_an_hour_raises_one_high_alert_and_the_fourth_none(machine, alerts, caplog):
    already_open = _a_restart_alert_is_already_open_this_hour()
    for _ in range(WORKER_RESTART_ALERT_STARTS):
        starts = worker_starts.record_start()
    assert starts is not None and starts >= WORKER_RESTART_ALERT_STARTS
    assert alerts, "the third start in the window asks for the alert"
    for asked in alerts:
        assert asked["alert_type"] == "worker_restarting" and asked["severity"] == "high"
        assert asked["tenant_id"] is None
        assert asked["dedupe_key"] == "worker_restarting" and asked["dedupe_per_utc_hour"] is True
        assert asked["payload"]["machine"] == machine
        assert asked["payload"]["alert_at_starts"] == WORKER_RESTART_ALERT_STARTS
    # The database wrote exactly one: the first asked for, unless a real one holds this hour.
    written = [asked for asked in alerts if asked["raised"]]
    assert len(written) == (0 if already_open else 1)
    assert caplog.text.count("worker_restarting starts_last_hour=") == len(written)

    asked_before = len(alerts)
    assert worker_starts.record_start() == starts + 1
    assert len(alerts) == asked_before + 1 and alerts[-1]["raised"] is False  # at most hourly


def test_a_start_the_database_cannot_record_is_logged_and_never_raises(monkeypatch, caplog):
    def unreachable():
        raise ConnectionError("acme test: no database")

    monkeypatch.setattr(worker_starts, "dispatcher_session", unreachable)
    assert worker_starts.record_start() is None
    assert "worker_start_not_recorded error_type=ConnectionError" in caplog.text


# ── committed, on CI's own database only ────────────────────────────────────


@requires_throwaway_ci_database
def test_a_committed_start_appears_in_the_count_healthz_reads(monkeypatch):
    """/healthz reads `worker_starts_last_hour()` as docflow_api; so does this."""
    monkeypatch.setenv("FLY_MACHINE_ID", f"acme-test-machine-{uuid4().hex[:8]}")

    def as_healthz_reads() -> int:
        with db.engine_for("api").connect() as conn:
            return int(conn.execute(text("SELECT public.worker_starts_last_hour()")).scalar_one())

    before = as_healthz_reads()
    with worker_starts_committed_on_ci():
        starts = worker_starts.record_start()
    assert starts is not None
    after = as_healthz_reads()
    assert after == before + 1
    assert starts == after  # the worker was told the same count, this start included


def test_only_ci_with_its_own_database_may_commit_a_start(monkeypatch):
    ci_database = "postgresql://docflow_worker:acme-test@127.0.0.1:54322/postgres"
    hosted = "postgresql://docflow_worker.acmetestref:acme-test@pooler.acme-test.example:6543/postgres"
    assert is_throwaway_ci_database("true", ci_database) is True
    assert is_throwaway_ci_database("true", hosted) is False  # CI's flag alone is not enough
    assert is_throwaway_ci_database(None, ci_database) is False  # nor a local database alone
    assert is_throwaway_ci_database("true", "") is False
    assert is_throwaway_ci_database("true", "not a url") is False

    # Anywhere else, the block refuses before anything is recorded.
    monkeypatch.setattr(suite, "throwaway_ci_database", lambda: False)
    recorded: list[str] = []
    with pytest.raises(AssertionError, match="not CI's own database"):
        with worker_starts_committed_on_ci():
            recorded.append("a start")
    assert recorded == [] and suite.WORKER_STARTS_CHECK.committing is False


# ── the rollback itself, and the session's own check ────────────────────────


def test_a_start_recorded_in_the_rollback_is_gone_when_it_ends(monkeypatch):
    monkeypatch.setenv("FLY_MACHINE_ID", f"acme-test-machine-{uuid4().hex[:8]}")
    with worker_starts_rolled_back():
        first = worker_starts.record_start()
        assert first is not None and worker_starts.record_start() == first + 1
    with worker_starts_rolled_back():
        # Had the two above stayed, this would be first + 2 or more.
        again = worker_starts.record_start()
    assert again is not None and again <= first


def test_the_sessions_check_names_a_start_kept_and_a_count_that_rose():
    check = WorkerStartsCheck()
    assert check.watch(lambda: 4)() == 4  # recorded for real, outside a rollback
    assert check.watch(lambda: None)() is None  # the database was not reached: nothing recorded
    check.rolled_back = True
    assert check.watch(lambda: 5)() == 5  # inside a rollback: not kept
    check.rolled_back, check.committing = False, True
    assert check.watch(lambda: 6)() == 6  # committed on purpose, on CI's own database
    assert len(check.kept) == 1 and check.committed == 1
    (problem,) = check.problems()
    assert "outside rolled_back_worker_starts" in problem

    check = WorkerStartsCheck()
    check.before, check.after = 2, 2
    assert check.problems() == []
    check.after = 1  # one aged out of the hour
    assert check.problems() == []
    check.after = 3
    (problem,) = check.problems()
    assert "went from 2 to 3" in problem
    check.committed = 1  # the one committed on purpose accounts for it
    assert check.problems() == []
    assert check.summary() == (
        "worker_starts in the last 60 minutes: before 2, after 3; starts kept by tests: 0; "
        "committed on CI's own database: 1"
    )
