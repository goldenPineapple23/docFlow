"""
The supervisor's own rules: the token (test N4), the request checks, a busy
service, and the startup refusals in production mode (E2, E5).

Against `PARSE_SERVICE_URL` when set (CI's image job, Fly); otherwise a
dev-mode service started here, which runs the same supervisor code.
"""

from __future__ import annotations

import os
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

from parse_service import config
from tests import conftest
from tests.conftest import FIXTURES

TOKEN = "test-token-for-the-dev-service-only"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def service(monkeypatch_module=None):
    """(url, token) of a running service: the one under test, or a dev one."""
    if conftest.via_http():
        yield conftest.SERVICE_URL, conftest.SERVICE_TOKEN
        return
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("FLY_")}
    env.update(PARSE_ISOLATION="off", PARSE_SERVICE_TOKEN=TOKEN, PORT=str(port), DOCFLOW_ENV="development")
    proc = subprocess.Popen([sys.executable, "-m", "parse_service.server"], env=env)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.1)
    yield url, TOKEN
    proc.terminate()
    proc.wait(timeout=10)


def _post(url: str, kind: str, body: bytes, extension: str, token: str | None):
    saved = conftest.SERVICE_URL, conftest.SERVICE_TOKEN
    conftest.SERVICE_URL = url
    try:
        return conftest.post(kind, body, extension, token=token or "")
    finally:
        conftest.SERVICE_URL, conftest.SERVICE_TOKEN = saved


# ── N4: the token ──────────────────────────────────────────────────────────


def test_the_right_token_parses(service):
    url, token = service
    status, body = _post(url, "document", (FIXTURES / "positive/po.txt").read_bytes(), ".txt", token)
    assert status == 200 and body["outcome"] == "ok"


def test_no_token_is_refused_before_the_body_is_read(service):
    url, _ = service
    status, body = _post(url, "document", (FIXTURES / "positive/po.txt").read_bytes(), ".txt", None)
    assert (status, body) == (401, {"error": "unauthorized"})


def test_a_wrong_token_is_refused(service):
    url, token = service
    status, body = _post(url, "document", b"PO", ".txt", token + "x")
    assert (status, body) == (401, {"error": "unauthorized"})


# ── The request checks ─────────────────────────────────────────────────────


def test_an_extension_that_is_not_an_extension_is_refused(service):
    url, token = service
    for bad in ("", "txt", "../../etc/passwd", ".TOOLONGEXT"):
        status, body = _post(url, "document", b"PO", bad, token)
        assert (status, body) == (400, {"error": "bad_extension"}), bad


def test_an_unknown_path_is_a_404(service):
    url, token = service
    request = urllib.request.Request(
        f"{url}/v1/run", data=b"x", method="POST", headers={"Authorization": f"Bearer {token}"}
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=10)
    assert excinfo.value.code == 404


def test_health_answers_once_the_service_is_up(service):
    url, _ = service
    with urllib.request.urlopen(f"{url}/health", timeout=10) as response:
        assert response.status == 200


# ── A busy service: never got in, the worker waits ─────────────────────────


def test_no_free_slot_is_a_503_with_retry_after():
    """The slot rule, in-process: both slots taken, the next request gets 503."""
    from http.server import ThreadingHTTPServer

    from parse_service import server as srv

    settings = config.Settings(
        production=False, isolation=False, token=TOKEN, port=0, libreoffice_path="", test_only=()
    )
    service = srv.Service(settings, None)
    service.slots = queue.SimpleQueue()  # every slot taken
    handler = type("H", (srv.Handler,), {"service": service})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        saved = conftest.SERVICE_URL
        conftest.SERVICE_URL = url
        try:
            status, body = conftest.post("document", b"PO", ".txt", token=TOKEN)
        finally:
            conftest.SERVICE_URL = saved
        assert (status, body) == (503, {"error": "busy"})
    finally:
        httpd.shutdown()


# ── E2, E5: production mode refuses to start weakened ──────────────────────


@pytest.mark.parametrize(
    "env, refused",
    [
        ({"FLY_APP_NAME": "x", "PARSE_ISOLATION": "off", "PARSE_SERVICE_TOKEN": "t"}, "PARSE_ISOLATION=off"),
        ({"DOCFLOW_ENV": "production", "PARSE_ISOLATION": "off", "PARSE_SERVICE_TOKEN": "t"}, "PARSE_ISOLATION=off"),
        ({"FLY_APP_NAME": "x", "PARSE_SERVICE_TOKEN": ""}, "PARSE_SERVICE_TOKEN"),
        (
            {"FLY_APP_NAME": "x", "PARSE_SERVICE_TOKEN": "t", "PARSE_TEST_ONLY_BREAK_MEMORY_CAP": "1"},
            "PARSE_TEST_ONLY_BREAK_MEMORY_CAP",
        ),
    ],
)
def test_production_mode_refuses_a_weakened_start(env, refused):
    with pytest.raises(config.StartupRefused) as excinfo:
        config.load(env)
    assert refused in str(excinfo.value)


def test_outside_production_the_dev_settings_are_accepted():
    """E2/E5's control: the same settings start without FLY_APP_NAME."""
    settings = config.load({"PARSE_ISOLATION": "off", "PARSE_TEST_ONLY_BREAK_MEMORY_CAP": "1"})
    assert settings.isolation is False
    assert settings.test_only == ("PARSE_TEST_ONLY_BREAK_MEMORY_CAP",)


@pytest.mark.parametrize(
    "extra",
    [
        {"PARSE_ISOLATION": "off", "PARSE_SERVICE_TOKEN": "t"},
        {"PARSE_SERVICE_TOKEN": "t", "PARSE_TEST_ONLY_ANYTHING": "1"},
    ],
)
def test_the_real_process_exits_before_opening_its_port_on_fly(extra):
    """E2/E5 as a process: with FLY_APP_NAME set, the service exits with 2."""
    env = {k: v for k, v in os.environ.items() if k not in ("PARSE_ISOLATION", "PARSE_SERVICE_TOKEN")}
    env.update(FLY_APP_NAME="docflow-parse-test", PORT=str(_free_port()), **extra)
    done = subprocess.run(
        [sys.executable, "-m", "parse_service.server"], env=env, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 2
    assert "refused to start" in done.stderr
