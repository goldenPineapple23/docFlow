"""
The worker's restart record against the real database (Stage 3e, part B;
BUILD-STATUS "3e detailed design", E3).

`worker_starts` is shared and has no delete path (no policy, no function), so
the counts here are relative: whatever earlier starts the window holds, each
start adds one, and three or more raise one `worker_restarting` an hour.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from docflow_core import worker_starts
from docflow_core.constants import WORKER_RESTART_ALERT_STARTS
from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import requires_stage3d_schema

pytestmark = [requires_stage3d_schema, pytest.mark.real_dispatch]


def _restart_alerts() -> list[dict]:
    with platform_session() as session:
        return [
            dict(r)
            for r in session.execute(
                text(
                    "SELECT * FROM founder_alerts WHERE type = 'worker_restarting' AND tenant_id IS NULL "
                    "AND acknowledged_at IS NULL"
                )
            ).mappings()
        ]


def _delete_restart_alerts() -> None:
    with platform_session() as session:
        outbox = [
            r[0]
            for r in session.execute(
                text(
                    "DELETE FROM founder_alerts WHERE type = 'worker_restarting' AND tenant_id IS NULL "
                    "RETURNING email_outbox_id"
                )
            )
            if r[0] is not None
        ]
        for outbox_id in outbox:
            session.execute(text("DELETE FROM email_outbox WHERE id = :id"), {"id": str(outbox_id)})


@pytest.fixture
def machine(monkeypatch):
    name = f"acme-test-machine-{uuid4().hex[:8]}"
    monkeypatch.setenv("FLY_MACHINE_ID", name)
    monkeypatch.setenv("FLY_IMAGE_REF", "registry.fly.io/acme-test:deployment-test")
    _delete_restart_alerts()
    yield name
    _delete_restart_alerts()


def test_each_start_is_recorded_and_counted_with_the_ones_before_it(machine):
    first = worker_starts.record_start()
    second = worker_starts.record_start()
    assert first is not None and second == first + 1
    # /healthz reads the count as docflow_api; the worker's own login is not
    # granted it (0036), so it is read here as the Console would.
    with platform_session() as session:
        shown = session.execute(text("SELECT public.worker_starts_last_hour()")).scalar_one()
    assert shown >= second


def test_the_third_start_in_an_hour_raises_one_high_alert_and_the_fourth_none(machine):
    for _ in range(WORKER_RESTART_ALERT_STARTS):
        starts = worker_starts.record_start()
    assert starts is not None and starts >= WORKER_RESTART_ALERT_STARTS
    alerts = _restart_alerts()
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "high"
    assert alerts[0]["payload"]["machine"] == machine
    assert alerts[0]["payload"]["alert_at_starts"] == WORKER_RESTART_ALERT_STARTS

    worker_starts.record_start()
    assert len(_restart_alerts()) == 1  # at most hourly


def test_a_start_the_database_cannot_record_is_logged_and_never_raises(monkeypatch, caplog):
    def unreachable():
        raise ConnectionError("acme test: no database")

    monkeypatch.setattr(worker_starts, "dispatcher_session", unreachable)
    assert worker_starts.record_start() is None
    assert "worker_start_not_recorded error_type=ConnectionError" in caplog.text
