"""
Test F5 of the 3c test table, against the real parse service image: the
container is killed while it is reading a file, and the worker's client must
call that "got in, never came out" (a timeout-class try, item 6a), not "never
got in". After a restart -- which runs the canary again -- the same file
parses.

Needs the container CI's worker job starts (PARSE_CONTAINER); it cannot run
against the dev service, which has no container to kill.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import urllib.request

import pytest
from docflow_core import parse_client

from tests.conftest import fixture_bytes

CONTAINER = os.environ.get("PARSE_CONTAINER", "")

pytestmark = pytest.mark.skipif(
    not CONTAINER, reason="needs the real parse service container (CI's worker job sets PARSE_CONTAINER)"
)


def _wait_healthy(seconds: int = 180) -> None:
    url = os.environ["PARSE_SERVICE_URL"].rstrip("/") + "/health"
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise AssertionError("the parse service did not come back after the restart")


def _wait_for_a_running_job(seconds: float) -> bool:
    """True as soon as `docker top` shows a parse job process in the
    container; False if none appears in time."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        listing = subprocess.run(["docker", "top", CONTAINER, "-eo", "args"], capture_output=True, text=True)
        if "parse_service.job" in listing.stdout:
            return True
    return False


def test_F5_a_parse_killed_mid_request_is_lost_and_the_next_try_after_a_restart_succeeds():
    content = fixture_bytes("positive/po.doc")  # LibreOffice: seconds, a window to kill in
    outcome: dict = {}

    def call() -> None:
        started = time.monotonic()
        try:
            outcome["answer"] = parse_client.parse_document(content, "po.doc")
        except Exception as exc:  # noqa: BLE001 -- the test inspects it
            outcome["error"] = exc
        outcome["seconds"] = time.monotonic() - started

    thread = threading.Thread(target=call)
    thread.start()
    # Kill the moment the service is working on it: when a parse job process
    # exists in the container (2026-10-01: a fixed 1 s wait stopped working
    # once po.doc parsed in under a second).
    in_job = _wait_for_a_running_job(seconds=30)
    subprocess.run(["docker", "kill", CONTAINER], check=True, capture_output=True)
    try:
        thread.join(timeout=60)
        assert in_job, f"no parse job was seen running before the kill ({outcome})"
        assert "answer" not in outcome, (
            f"the parse finished before the kill ({outcome}); the window is too short"
        )
        assert isinstance(outcome.get("error"), parse_client.ParseLost), outcome
    finally:
        # Always, pass or fail: every later test in this job reads through
        # this container. On 2026-10-01 an assertion above failed before the
        # restart, and 18 database tests after it found no service.
        subprocess.run(["docker", "start", CONTAINER], check=True, capture_output=True)
        _wait_healthy()

    answer = parse_client.parse_document(content, "po.doc")
    assert answer.outcome == "ok" and "BCH-2291" in answer.parts[0]["text"]
