"""
The API answers on IPv4 and on IPv6 from one socket (app/serve.py; D-227).

Fly's edge dials a machine on IPv4 and `fly proxy` on its private IPv6 address.
On 2026-11-01 the first request through a public address read 503, because
`uvicorn --host ::` listens on IPv6 alone. These start real processes, so the
fault can't hide behind a fake; ::1 stands for the private address and
127.0.0.1 for the edge's.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import serve

_API_DIR = Path(__file__).resolve().parents[1]
_START_SECONDS = 60


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _accepts(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex((host, port)) == 0


def _start(command: list[str], extra_env: dict[str, str] | None = None) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if k not in ("PARSE_SERVICE_TOKEN", "FLY_APP_NAME")}
    env.update(extra_env or {})
    return subprocess.Popen(
        [sys.executable, "-m", *command],
        cwd=_API_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _wait_for(proc: subprocess.Popen, host: str, port: int) -> None:
    deadline = time.monotonic() + _START_SECONDS
    while proc.poll() is None and not _accepts(host, port) and time.monotonic() < deadline:
        time.sleep(0.1)
    if proc.poll() is not None:
        raise AssertionError(proc.stderr.read().decode(errors="replace")[-2000:])  # type: ignore[union-attr]
    assert _accepts(host, port), f"nothing listening on {host}:{port} after {_START_SECONDS} s"


def _stop(proc: subprocess.Popen) -> None:
    proc.kill()
    proc.communicate(timeout=10)


def _healthz_status(host: str, port: int) -> int:
    connection = http.client.HTTPConnection(host, port, timeout=30)
    try:
        connection.request("GET", "/healthz")
        return connection.getresponse().status
    finally:
        connection.close()


def test_the_launcher_answers_on_ipv4_and_on_ipv6():
    port = _free_port()
    proc = _start(["app.serve", str(port)])
    try:
        _wait_for(proc, "::1", port)
        assert _healthz_status("::1", port) == 200
        assert _healthz_status("127.0.0.1", port) == 200
    finally:
        _stop(proc)


def test_control_uvicorn_on_colons_is_refused_on_ipv4():
    # The command that failed on Fly. If this ever reached IPv4, the test above
    # would prove nothing.
    port = _free_port()
    proc = _start(["uvicorn", "app.main:app", "--host", "::", "--port", str(port)])
    try:
        _wait_for(proc, "::1", port)
        assert _healthz_status("::1", port) == 200
        assert not _accepts("127.0.0.1", port)
    finally:
        _stop(proc)


def test_on_fly_the_launcher_exits_before_opening_its_port_when_it_holds_the_token():
    port = _free_port()
    proc = _start(
        ["app.serve", str(port)], {"FLY_APP_NAME": "docflow-api-test", "PARSE_SERVICE_TOKEN": "a-token"}
    )
    opened = False
    deadline = time.monotonic() + _START_SECONDS
    while proc.poll() is None and time.monotonic() < deadline:
        opened = opened or _accepts("::1", port) or _accepts("127.0.0.1", port)
        time.sleep(0.1)
    if proc.poll() is None:
        proc.kill()
    _, stderr = proc.communicate(timeout=10)
    assert proc.returncode not in (None, 0), stderr.decode(errors="replace")[-2000:]
    assert not opened
    assert "DocFlow API refused to start: PARSE_SERVICE_TOKEN is set" in stderr.decode(errors="replace")


@pytest.mark.parametrize(("started", "exits"), [(False, True), (True, False)])
def test_a_failed_startup_exits_non_zero_and_a_clean_stop_does_not(monkeypatch, started, exits):
    # Fly restarts on failure only, and uvicorn's Server.run simply returns
    # when the app's startup fails.
    class FakeServer:
        def __init__(self, config):
            self.started = started

        def run(self, sockets):
            for sock in sockets:
                sock.close()

    monkeypatch.setattr(serve.uvicorn, "Server", FakeServer)
    if exits:
        with pytest.raises(SystemExit) as stopped:
            serve.main([str(_free_port())])
        assert stopped.value.code == serve.STARTUP_FAILURE
    else:
        serve.main([str(_free_port())])


def test_the_image_starts_the_launcher():
    dockerfile = (_API_DIR / "Dockerfile").read_text(encoding="utf-8")
    cmd = [line for line in dockerfile.splitlines() if line.startswith("CMD ")]
    assert len(cmd) == 1
    assert json.loads(cmd[0][len("CMD ") :]) == ["python", "-m", "app.serve"]
