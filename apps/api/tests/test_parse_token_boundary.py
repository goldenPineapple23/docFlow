"""
The API never holds the parse service's token (founder, 2026-10-01).

Only the worker talks to the parse service (Stage 3c). The API is the
public-facing process, so a token in its settings would let anything that
reaches its memory or environment send files to the service. On Fly the token
is a secret on the worker's app and the parse service's app, never the API's.

In CI the API job's test step has no PARSE_SERVICE_TOKEN. The token reaches
only the harness that runs worker code (`as_the_worker` in tests/conftest.py),
under WORKER_HARNESS_PARSE_TOKEN. The first test also reads the root .env, so
a token put there for the worker would fail it: in dev the API and the worker
share that file.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from docflow_core import parse_client
from docflow_core.config import Settings, get_settings

from app.main import parse_token_startup_refusal
from tests.conftest import WORKER_HARNESS_PARSE_TOKEN, as_the_worker

_TABLE = b"sku,description\nTEST-1001,Acme Test Widget\n"
_API_DIR = Path(__file__).resolve().parents[1]


def test_the_api_settings_carry_no_parse_token():
    assert "PARSE_SERVICE_TOKEN" not in os.environ
    get_settings.cache_clear()
    assert get_settings().parse_service_token == ""
    assert Settings().parse_service_token == ""


@pytest.mark.skipif(
    not os.environ.get(WORKER_HARNESS_PARSE_TOKEN),
    reason="needs the real parse service with a token (CI's API job); the dev service has none",
)
def test_a_request_carrying_the_api_settings_is_refused():
    get_settings.cache_clear()
    with pytest.raises(parse_client.ParseUnavailable) as refused:
        parse_client.parse_table(_TABLE, "catalog.csv")
    assert refused.value.reason == "unauthorized"

    # Control: the same request with the worker's token gets in, so the
    # refusal above was the missing token, not a service that was down.
    with as_the_worker():
        answer = parse_client.parse_table(_TABLE, "catalog.csv")
    assert answer.outcome == "ok"
    assert answer.columns == ["sku", "description"]


# ── Q12: on Fly, the API refuses to start holding the token ─────────────────


@pytest.mark.parametrize(
    ("fly_app_name", "token", "refused"),
    [
        ("docflow-api-staging", "a-token", True),
        ("docflow-api-staging", "", False),
        ("", "a-token", False),  # dev: not deployed; the .env test above covers it
        ("", "", False),
    ],
)
def test_the_startup_rule(monkeypatch, fly_app_name, token, refused):
    monkeypatch.setenv("FLY_APP_NAME", fly_app_name)
    monkeypatch.setenv("PARSE_SERVICE_TOKEN", token)
    get_settings.cache_clear()
    try:
        assert (parse_token_startup_refusal() is not None) is refused
    finally:
        monkeypatch.delenv("PARSE_SERVICE_TOKEN")
        monkeypatch.delenv("FLY_APP_NAME")
        get_settings.cache_clear()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _accepts(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _start_api(extra_env: dict[str, str]) -> tuple[subprocess.Popen, int]:
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k not in ("PARSE_SERVICE_TOKEN", "FLY_APP_NAME")}
    env.update(extra_env)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=_API_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc, port


def test_on_fly_the_api_exits_before_opening_its_port_when_it_holds_the_token():
    proc, port = _start_api({"FLY_APP_NAME": "docflow-api-test", "PARSE_SERVICE_TOKEN": "a-token"})
    opened = False
    deadline = time.monotonic() + 60
    while proc.poll() is None and time.monotonic() < deadline:
        opened = opened or _accepts(port)
        time.sleep(0.1)
    if proc.poll() is None:
        proc.kill()
    _, stderr = proc.communicate(timeout=10)
    assert proc.returncode not in (None, 0), stderr.decode(errors="replace")[-2000:]
    assert not opened
    assert "DocFlow API refused to start: PARSE_SERVICE_TOKEN is set" in stderr.decode(errors="replace")

    # Control: the same start on Fly without the token opens its port, so the
    # refusal above was the token, not a server that couldn't start at all.
    proc, port = _start_api({"FLY_APP_NAME": "docflow-api-test"})
    try:
        deadline = time.monotonic() + 60
        while proc.poll() is None and not _accepts(port) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert _accepts(port), proc.poll()
    finally:
        proc.terminate()
        proc.communicate(timeout=20)
