"""
The API made public on staging (D-235 item 2; D-237): the docs routes off,
the CORS allowlist checked before a deployed API starts, and the headers
every response carries.

`app` is built when `app.main` is imported, from the settings in force then,
so each case that needs other settings runs in a fresh Python process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from docflow_core.config import Settings, get_settings
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.main import API_SECURITY_HEADERS, _SecurityHeaders, app, cors_startup_refusal

_API_DIR = Path(__file__).resolve().parents[1]
_DOC_PATHS = ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect")
_WEB = "https://app.example.test"


def _run(code: str, extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """`code` in a fresh interpreter in apps/api, as staging config: DOCFLOW_ENV
    set explicitly, and never the parse token or a Fly app name unless given."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PARSE_SERVICE_TOKEN", "FLY_APP_NAME", "API_DOCS_ENABLED", "CORS_ALLOWED_ORIGINS")
    }
    env.update({"DOCFLOW_ENV": "staging", "PARSE_SERVICE_TOKEN": ""})
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", code], cwd=_API_DIR, env=env, capture_output=True, text=True, timeout=120
    )


_STATUSES = (
    "import json\n"
    "from fastapi.testclient import TestClient\n"
    "from app.main import app\n"
    "c = TestClient(app)\n"
    f"print(json.dumps({{p: c.get(p).status_code for p in {_DOC_PATHS!r}}}))\n"
)


def _doc_statuses(extra_env: dict[str, str]) -> dict[str, int]:
    done = _run(_STATUSES, extra_env)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


# ── /docs, /redoc, /openapi.json ─────────────────────────────────────────────


def test_under_staging_config_every_docs_route_is_a_404():
    assert _doc_statuses({}) == dict.fromkeys(_DOC_PATHS, 404)


def test_the_setting_turns_them_on_so_the_404s_above_are_the_settings_doing():
    statuses = _doc_statuses({"API_DOCS_ENABLED": "true"})
    assert statuses == dict.fromkeys(_DOC_PATHS, 200)


def test_on_fly_the_setting_is_ignored_and_startup_says_the_docs_are_off():
    fly = {"FLY_APP_NAME": "docflow-api-test", "API_DOCS_ENABLED": "true", "CORS_ALLOWED_ORIGINS": _WEB}
    assert _doc_statuses(fly) == dict.fromkeys(_DOC_PATHS, 404)

    said = _run(
        "import logging, sys\n"
        "logging.basicConfig(stream=sys.stdout, level=logging.WARNING, format='%(message)s')\n"
        "from app.main import api_docs_startup_check\n"
        "print('on' if api_docs_startup_check() else 'off')\n",
        fly,
    )
    assert said.returncode == 0, said.stderr[-2000:]
    lines = said.stdout.strip().splitlines()
    assert lines[-1] == "off"
    said_off = "api_docs_off: /docs, /redoc and /openapi.json are off on Fly"
    assert any(line.startswith(said_off) for line in lines)


def test_the_schema_still_builds_for_the_tests_that_walk_the_routes():
    assert len(app.openapi()["paths"]) > 20


# ── The CORS allowlist on Fly ────────────────────────────────────────────────


@pytest.fixture
def settings_env(monkeypatch):
    def apply(**env: str | None) -> None:
        for name, value in env.items():
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    yield apply
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("value", "named"),
    [
        ("*", "is a wildcard"),
        ("https://*.vercel.app", "is a wildcard"),
        ("http://app.example.test", "is not https"),
        ("http://localhost:3000", "is not https"),
        ("https://localhost:3000", "is a loopback address"),
        ("https://127.0.0.1", "is a loopback address"),
        ("https://[::1]:3000", "is a loopback address"),
        ("https://app.example.test/review", "has a path"),
        ("https://app.example.test?x=1", "has a path"),
        (f"{_WEB},http://localhost:3000", "is not https"),
    ],
)
def test_on_fly_a_bad_origin_is_refused_by_name(settings_env, value, named):
    settings_env(FLY_APP_NAME="docflow-api-staging", CORS_ALLOWED_ORIGINS=value)
    refusal = cors_startup_refusal()
    assert refusal is not None and named in refusal
    bad = value.split(",")[-1].strip()
    assert repr(bad) in refusal


@pytest.mark.parametrize("value", [None, "", " , "])
def test_on_fly_an_unset_allowlist_is_refused(settings_env, value):
    # Read without the .env file, so a local CORS line can't stand in for "unset".
    settings_env(FLY_APP_NAME="docflow-api-staging", CORS_ALLOWED_ORIGINS=value)
    refusal = cors_startup_refusal(Settings(_env_file=None))
    assert refusal is not None and "CORS_ALLOWED_ORIGINS is not set" in refusal


@pytest.mark.parametrize("value", [_WEB, f"{_WEB}/", f"{_WEB}, https://docflow.example.test"])
def test_on_fly_exact_https_origins_are_accepted(settings_env, value):
    settings_env(FLY_APP_NAME="docflow-api-staging", CORS_ALLOWED_ORIGINS=value)
    assert cors_startup_refusal() is None


def test_off_fly_nothing_is_checked(settings_env):
    settings_env(FLY_APP_NAME=None, CORS_ALLOWED_ORIGINS="http://localhost:3000")
    assert cors_startup_refusal() is None


def test_on_fly_the_api_will_not_import_with_a_bad_origin_and_says_which():
    done = _run("import app.main", {"FLY_APP_NAME": "docflow-api-test", "CORS_ALLOWED_ORIGINS": "*"})
    assert done.returncode == 1, done.stderr[-2000:]
    assert "DocFlow API refused to start: CORS_ALLOWED_ORIGINS holds '*', which is a wildcard" in done.stderr

    # Control: the same start with a good origin imports, so the refusal was the value.
    ok = _run("import app.main", {"FLY_APP_NAME": "docflow-api-test", "CORS_ALLOWED_ORIGINS": _WEB})
    assert ok.returncode == 0, ok.stderr[-2000:]


# ── CORS for the deployed web origin ─────────────────────────────────────────


def test_a_preflight_from_the_configured_origin_is_answered_and_any_other_is_not():
    done = _run(
        "import json\n"
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "c = TestClient(app)\n"
        "out = {}\n"
        f"for origin in ({_WEB!r}, 'https://evil.example.test'):\n"
        "    r = c.options('/review/documents', headers={'Origin': origin,\n"
        "        'Access-Control-Request-Method': 'GET',\n"
        "        'Access-Control-Request-Headers': 'authorization'})\n"
        "    out[origin] = [r.status_code, r.headers.get('access-control-allow-origin'),\n"
        "                   r.headers.get('access-control-allow-credentials')]\n"
        "print(json.dumps(out))\n",
        {"CORS_ALLOWED_ORIGINS": _WEB},
    )
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out[_WEB] == [200, _WEB, None]
    assert out["https://evil.example.test"][0] == 400
    assert out["https://evil.example.test"][1] is None


# ── The headers on every API response ────────────────────────────────────────


def _expected() -> dict[str, str]:
    return {name.decode(): value.decode() for name, value in API_SECURITY_HEADERS}


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/healthz", 200),
        ("GET", "/home", 401),
        ("GET", "/admin/tenants", 404),
        ("GET", "/admin/tenants/00000000-0000-0000-0000-000000000000/act/review/documents", 404),
        ("GET", "/no-such-route", 404),
    ],
)
def test_every_kind_of_answer_carries_the_headers(method, path, status):
    response = TestClient(app).request(method, path)
    assert response.status_code == status
    for name, value in _expected().items():
        assert response.headers.get(name) == value, name


def test_a_preflight_carries_them_too():
    origin = get_settings().cors_allowed_origins.split(",")[0].strip().rstrip("/")
    response = TestClient(app).options(
        "/home", headers={"Origin": origin, "Access-Control-Request-Method": "GET"}
    )
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["strict-transport-security"] == "max-age=63072000; includeSubDomains"


def test_a_route_that_sets_its_own_csp_keeps_it_byte_for_byte():
    own = "default-src 'none'; sandbox"

    def page(_request):
        headers = {"Content-Security-Policy": own, "Referrer-Policy": "same-origin"}
        return PlainTextResponse("x", headers=headers)

    wrapped = _SecurityHeaders(Starlette(routes=[Route("/p", page)]))
    response = TestClient(wrapped).get("/p")
    assert response.headers.get_list("content-security-policy") == [own]
    assert response.headers.get_list("referrer-policy") == ["same-origin"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_the_api_sends_no_x_frame_options_so_the_originals_frame_still_works():
    assert b"x-frame-options" not in {name for name, _ in API_SECURITY_HEADERS}
