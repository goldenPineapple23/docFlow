"""
How the parse tests reach the parser.

- Default (this machine, CI's unit job): in-process, the parsing code
  called directly. Logic only -- never proof of isolation.
- With `PARSE_SERVICE_URL` set (CI's image job, Fly): over HTTP to the
  running service, so every answer comes out of a real sandboxed job.
  `PARSE_SERVICE_TOKEN` must match the service's.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SERVICE_URL = os.environ.get("PARSE_SERVICE_URL", "").rstrip("/")
SERVICE_TOKEN = os.environ.get("PARSE_SERVICE_TOKEN", "")


def via_http() -> bool:
    return bool(SERVICE_URL)


def post(
    kind: str, content: bytes, extension: str, *, token: str | None = None, timeout: float = 300
) -> tuple[int, dict]:
    headers = {"Content-Type": "application/octet-stream", "X-File-Extension": extension}
    token = SERVICE_TOKEN if token is None else token
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{SERVICE_URL}/v1/{kind}", data=content, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def answer(kind: str, path: Path) -> dict:
    """The parser's answer for one fixture file, by whichever route is set."""
    content = path.read_bytes()
    if via_http():
        status, body = post(kind, content, path.suffix.lower())
        assert status == 200, f"{path.name}: HTTP {status} {body}"
        return body
    if kind == "document":
        from parse_service.parsing import documents

        return documents.parse(content, path.name)
    from parse_service.parsing import tables

    return tables.parse(content, path.name)


def decoded(part: dict) -> bytes:
    return base64.standard_b64decode(part["source"]["data"])


@pytest.fixture(scope="session")
def baseline() -> dict:
    return json.loads((FIXTURES / "baseline.json").read_text(encoding="utf-8"))
