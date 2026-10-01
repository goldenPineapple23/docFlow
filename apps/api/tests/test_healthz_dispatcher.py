"""
Stage 3d, gap 1 (founder): the dispatcher and its heartbeat check both run in
the worker, so a dead worker could alert nobody. /healthz -- no sign-in --
shows the heartbeat's age and whether it is stale, for the Console and an
external uptime monitor (Phase 6, RUNBOOK). It shows nothing else, and it is
always HTTP 200, so a platform check pointed at it never restarts a healthy
API because the worker is down.
"""

from __future__ import annotations

from docflow_core.constants import DISPATCHER_STALE_MIN

from tests.conftest import requires_database


def test_a_stale_heartbeat_shows_stale(client, monkeypatch):
    monkeypatch.setattr(
        "app.main.dispatcher_health",
        lambda: {"heartbeat_age_seconds": DISPATCHER_STALE_MIN * 60 + 1, "stale": True},
    )
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "dispatcher": {"heartbeat_age_seconds": DISPATCHER_STALE_MIN * 60 + 1, "stale": True},
    }


def test_a_fresh_heartbeat_shows_fresh(client, monkeypatch):
    monkeypatch.setattr("app.main.dispatcher_health", lambda: {"heartbeat_age_seconds": 12, "stale": False})
    assert client.get("/healthz").json()["dispatcher"] == {"heartbeat_age_seconds": 12, "stale": False}


def test_an_unreadable_heartbeat_reads_as_stale_never_as_an_error(client, monkeypatch):
    def broken():
        raise ConnectionError("database unreachable")

    monkeypatch.setattr("app.main.dispatcher_health", broken)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["dispatcher"] == {"heartbeat_age_seconds": None, "stale": True}


@requires_database
def test_healthz_reads_the_real_heartbeat_and_shows_only_its_age(client):
    """The real function, unauthenticated: the dispatcher part carries the
    age and the verdict and nothing else (no counts, no ids)."""
    body = client.get("/healthz").json()
    assert set(body["dispatcher"]) == {"heartbeat_age_seconds", "stale"}
    age = body["dispatcher"]["heartbeat_age_seconds"]
    assert age is None or isinstance(age, int)
    assert body["dispatcher"]["stale"] == (age is None or age > DISPATCHER_STALE_MIN * 60)
