"""
M10 (D-239): the API says which models it calls at start, and a model that
Settings refuses stops it in one line, before the routers (which import the
extraction module) can raise a traceback.

Each case runs in a fresh Python process, because `app.main` is imported
once per process with the settings in force then.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_API_DIR = Path(__file__).resolve().parents[1]
_DROPPED = (
    "PARSE_SERVICE_TOKEN",
    "FLY_APP_NAME",
    "API_DOCS_ENABLED",
    "CORS_ALLOWED_ORIGINS",
    "DOCFLOW_EXTRACTION_MODEL",
    "DOCFLOW_ROUTING_MODEL",
)


def _run(code: str, extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in _DROPPED}
    env.update({"DOCFLOW_ENV": "staging", "PARSE_SERVICE_TOKEN": ""})
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", code], cwd=_API_DIR, env=env, capture_output=True, text=True, timeout=120
    )


def test_the_api_prints_both_model_ids_at_start():
    said = _run(
        "import logging, sys\n"
        "logging.basicConfig(stream=sys.stdout, level=logging.WARNING, format='%(message)s')\n"
        "from app.main import model_ids_startup_line\n"
        "model_ids_startup_line()\n",
        {},
    )
    assert said.returncode == 0, said.stderr[-2000:]
    last = said.stdout.strip().splitlines()[-1]
    assert last == "model_ids: extraction=claude-sonnet-5 routing=claude-haiku-4-5"


def test_the_line_runs_at_application_startup():
    import app.main as main

    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "model_ids_startup_line()" in source.split("async def _lifespan")[1]


@pytest.mark.parametrize(
    ("name", "model", "says"),
    [
        ("DOCFLOW_ROUTING_MODEL", "claude-haiku-4-5-20251001", "is not in MODEL_PRICES"),
        ("DOCFLOW_EXTRACTION_MODEL", "claude-haiku-4-5", "may not fill the extraction role"),
        ("DOCFLOW_ROUTING_MODEL", "claude-sonnet-5", "may not fill the routing role"),
    ],
)
def test_a_refused_model_stops_the_api_in_one_line(name, model, says):
    said = _run("import app.main\n", {name: model})
    assert said.returncode == 1
    assert said.stderr.strip().splitlines() == [said.stderr.strip()]  # one line, no traceback
    assert said.stderr.startswith(f"DocFlow API refused to start: {name}={model!r} {says}")
