"""
Stage 3d, gap 1 (founder): the dispatcher and its heartbeat check both run in
the worker, so a dead worker could alert nobody. /healthz -- no sign-in --
shows the heartbeat's age and whether it is stale, for the Console and an
external uptime monitor (from the first worker deploy, RUNBOOK 9.4). It shows nothing else, and it is
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
    body = response.json()
    assert body["status"] == "ok"
    assert body["dispatcher"] == {"heartbeat_age_seconds": DISPATCHER_STALE_MIN * 60 + 1, "stale": True}


def test_a_fresh_heartbeat_shows_fresh(client, monkeypatch):
    monkeypatch.setattr("app.main.dispatcher_health", lambda: {"heartbeat_age_seconds": 12, "stale": False})
    assert client.get("/healthz").json()["dispatcher"] == {"heartbeat_age_seconds": 12, "stale": False}


MONITOR_KEYWORD = b'"stale":false'


def test_the_uptime_monitors_keyword_is_in_the_body_only_when_fresh_and_is_the_one_the_runbook_gives(
    client, monkeypatch
):
    """The external monitor (RUNBOOK 9.4) matches raw bytes, not parsed JSON.
    Found 2026-10-01: the RUNBOOK said `"stale": false`, with a space, which
    the API never sends (Starlette writes compact JSON), so a monitor set up
    from it would alert for ever. These are the bytes, and the RUNBOOK must
    give exactly them."""
    from pathlib import Path

    monkeypatch.setattr("app.main.dispatcher_health", lambda: {"heartbeat_age_seconds": 12, "stale": False})
    assert MONITOR_KEYWORD in client.get("/healthz").content
    for stale in (
        lambda: {"heartbeat_age_seconds": DISPATCHER_STALE_MIN * 60 + 1, "stale": True},
        lambda: {"heartbeat_age_seconds": None, "stale": True},
    ):
        monkeypatch.setattr("app.main.dispatcher_health", stale)
        assert MONITOR_KEYWORD not in client.get("/healthz").content

    runbook = (Path(__file__).resolve().parents[3] / "RUNBOOK.md").read_text(encoding="utf-8")
    section = runbook[runbook.index("### 9.4"):runbook.index("### 9.5")]
    assert f"`{MONITOR_KEYWORD.decode()}`" in section
    assert '"stale": false' not in section


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


@requires_database
def test_healthz_shows_the_workers_starts_in_the_last_hour(client):
    """Stage 3e (part B): a worker restarting slowly keeps the heartbeat
    fresh, so /healthz also shows the starts in the last hour -- a count,
    through the API's own login (worker_starts_last_hour())."""
    body = client.get("/healthz").json()
    assert set(body) == {"status", "dispatcher", "worker_starts_last_hour"}
    assert isinstance(body["worker_starts_last_hour"], int) and body["worker_starts_last_hour"] >= 0


def test_an_unreadable_start_count_is_null_and_healthz_still_answers(client, monkeypatch):
    def broken():
        raise ConnectionError("database unreachable")

    monkeypatch.setattr("app.main.worker_starts.starts_last_hour", broken)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["worker_starts_last_hour"] is None
