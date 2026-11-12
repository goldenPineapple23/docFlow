"""
The API answers on IPv4 and on IPv6 from one socket (app/serve.py; D-227).

Fly's edge dials a machine on IPv4 and `fly proxy` on its private IPv6 address.
On 2026-11-01 the first request through a public address read 503, because
`uvicorn --host ::` listens on IPv6 alone. The listening tests start real
processes, so the fault can't hide behind a fake; ::1 stands for the private
address and 127.0.0.1 for the edge's.
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

_API_DIR = Path(__file__).resolve().parents[1]
# On Fly the API also refuses to start without a good CORS allowlist (D-237),
# so every Fly-mode start here carries one: the only fault is the one tested.
_FLY_CORS = {"CORS_ALLOWED_ORIGINS": "https://app.example.test"}
_START_SECONDS = 60
_HANG_SECONDS = 300
_LOGIN_URLS = ("DATABASE_URL", "API_DATABASE_URL", "ADMIN_DATABASE_URL", "STRIPE_DATABASE_URL")
_NO_DATABASE = "postgresql://nobody:nothing@db.invalid:5432/none"
# db.verify_logins is the one startup call that would reach a database. Forcing
# it here leaves the real check, the real lifespan and real uvicorn to do the
# rest, so the result can't depend on whether a database is up.
_WRONG_LOGIN = """
from app import main, serve

def wrong_login(logins):
    raise main.db.WrongLoginError("forced by the test")

main.db.verify_logins = wrong_login
serve.main(["0"])
"""


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _accepts(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex((host, port)) == 0


def _env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("PARSE_SERVICE_TOKEN", "FLY_APP_NAME")}
    env.update(extra_env or {})
    return env


def _start(command: list[str], extra_env: dict[str, str] | None = None) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", *command],
        cwd=_API_DIR,
        env=_env(extra_env),
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
        ["app.serve", str(port)],
        {"FLY_APP_NAME": "docflow-api-test", "PARSE_SERVICE_TOKEN": "a-token", **_FLY_CORS},
    )
    opened = False
    deadline = time.monotonic() + _START_SECONDS
    while proc.poll() is None and time.monotonic() < deadline:
        opened = opened or _accepts("::1", port) or _accepts("127.0.0.1", port)
        time.sleep(0.1)
    if proc.poll() is None:
        proc.kill()
    _, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 1, stderr.decode(errors="replace")[-2000:]
    assert not opened
    assert "DocFlow API refused to start: PARSE_SERVICE_TOKEN is set" in stderr.decode(errors="replace")


def test_a_wrong_login_at_startup_exits_3_through_real_uvicorn():
    # A failed startup must not read as a clean stop. uvicorn 0.53.0 exits 3
    # itself and the launcher adds no check, so this guards a uvicorn change.
    # Nothing is timed: there is no port to watch (port 0), the process ends by
    # itself, and the limit only stops a hang.
    result = subprocess.run(
        [sys.executable, "-c", _WRONG_LOGIN],
        cwd=_API_DIR,
        env=_env(dict.fromkeys(_LOGIN_URLS, _NO_DATABASE)),
        capture_output=True,
        timeout=_HANG_SECONDS,
    )
    stderr = result.stderr.decode(errors="replace")
    assert result.returncode == 3, stderr[-2000:]
    assert "DocFlow API refused to start: forced by the test" in stderr


def test_the_image_starts_the_launcher():
    dockerfile = (_API_DIR / "Dockerfile").read_text(encoding="utf-8")
    cmd = [line for line in dockerfile.splitlines() if line.startswith("CMD ")]
    assert len(cmd) == 1
    assert json.loads(cmd[0][len("CMD ") :]) == ["python", "-m", "app.serve"]
