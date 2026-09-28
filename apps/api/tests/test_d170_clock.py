# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
D-170: a timestamp the database will later compare is written by the database.

Items #4, #5 and #7 of D-170's table (#1 and #2 are in test_lifecycle_api.py,
beside the lifecycle they guard). Each test runs one module's app clock off
the database's (tests/app_clock.py): code that follows the database's clock
is unaffected, code that decides by its own goes wrong by exactly the skew.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from docflow_core import lifecycle, metrics, onboarding
from docflow_core.constants import FIRST_WEEK_CHECKIN_DAYS, REMINDER_DAYS
from docflow_core.db import rollup_session, tenant_session
from sqlalchemy import text

from tests.app_clock import skew_app_clock
from tests.test_console_api import _Console, _environment, _scalar, stripe  # noqa: F401
from tests.test_lifecycle_api import _activate, _backdate_cancellation, requires_lifecycle_schema

AHEAD, BEHIND = timedelta(hours=2), -timedelta(hours=2)
SKEWS = pytest.mark.parametrize("skew", [AHEAD, BEHIND], ids=["app-clock-ahead", "app-clock-behind"])


# ── #4 scheduled_jobs.run_at ─────────────────────────────────────────────────


@SKEWS
def test_the_first_week_checkin_is_scheduled_on_the_database_clock(
    client, stripe, _environment, monkeypatch, skew
):
    """The job sweep runs a job when run_at <= now(), so run_at is the
    database's: exactly FIRST_WEEK_CHECKIN_DAYS after the row's own created_at."""
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]

        skew_app_clock(monkeypatch, onboarding, by=skew)
        with tenant_session(tenant_id) as session:
            onboarding.schedule_first_week_checkin(session, tenant_id)

        gap = _scalar(
            "SELECT run_at - created_at FROM scheduled_jobs "
            "WHERE tenant_id = :t AND job_type = 'first_week_checkin'",
            t=tenant_id,
        )
        assert gap == timedelta(days=FIRST_WEEK_CHECKIN_DAYS)


@requires_lifecycle_schema
@SKEWS
def test_the_deletion_reminders_are_scheduled_on_the_database_clock(
    client, stripe, _environment, monkeypatch, skew
):
    """Each reminder runs REMINDER_DAYS after the export window began, by the
    database's clock; the date in its dedupe key is its own run_at's."""
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        _activate(tenant_id)
        with tenant_session(tenant_id) as session:
            lifecycle.cancel(
                session, tenant_id, reason="for_cause",
                note="a reason at least twenty characters long", actor_user_id=console.user_id,
            )
        _backdate_cancellation(tenant_id, minutes=180)

        skew_app_clock(monkeypatch, lifecycle, by=skew)
        with tenant_session(tenant_id) as session:
            assert lifecycle.claim_for_suspend(session, tenant_id) is not None

        offsets = _scalar(
            "SELECT array_agg(j.run_at - t.status_changed_at ORDER BY j.run_at) "
            "FROM scheduled_jobs j JOIN tenants t ON t.id = j.tenant_id "
            "WHERE j.tenant_id = :t AND j.job_type = 'pending_deletion_reminder'",
            t=tenant_id,
        )
        assert list(offsets) == [timedelta(days=day) for day in REMINDER_DAYS]
        mismatched = _scalar(
            "SELECT count(*) FROM scheduled_jobs WHERE tenant_id = :t "
            "AND job_type = 'pending_deletion_reminder' "
            "AND dedupe_key NOT LIKE '%:' || (run_at AT TIME ZONE 'UTC')::date::text",
            t=tenant_id,
        )
        assert mismatched == 0


# ── #5 rollup staleness ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "skew,finished_ago,stale",
    [
        # The app thinks 37 h have passed; the database says 35 h -- fresh.
        (AHEAD, "35 hours", False),
        # The database says 37 h; the app thinks 35 h -- stale all the same.
        (BEHIND, "37 hours", True),
    ],
    ids=["app-clock-ahead", "app-clock-behind"],
)
def test_the_rollups_staleness_is_judged_on_the_database_clock(
    client, stripe, _environment, monkeypatch, skew, finished_ago, stale
):
    """The database stamps finished_at, so its clock says how long ago that
    was, against ROLLUP_STALE_HOURS (36). Read through the dashboard, as the
    founder sees it; the run is this test's own and is removed afterwards."""
    with rollup_session() as session:
        run_id = session.execute(
            text(
                "INSERT INTO rollup_runs (trigger, finished_at, ok) "
                "VALUES ('manual', now() - CAST(:ago AS interval), true) RETURNING id"
            ),
            {"ago": finished_ago},
        ).scalar_one()
    try:
        skew_app_clock(monkeypatch, metrics, by=skew)
        with _Console() as console:
            body = client.get("/admin/dashboard", headers=console.headers()).json()
        assert body["rollup"]["id"] == str(run_id), "another run is newer -- the test is not reading its own"
        assert body["rollup_is_stale"] is stale
    finally:
        with rollup_session() as session:
            session.execute(text("DELETE FROM rollup_runs WHERE id = :id"), {"id": str(run_id)})
