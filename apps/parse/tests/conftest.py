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
    kind: str,
    content: bytes,
    extension: str,
    *,
    token: str | None = None,
    timeout: float = 300,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    headers = {"Content-Type": "application/octet-stream", "X-File-Extension": extension, **(extra_headers or {})}
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
    return answer_for(kind, path.read_bytes(), path.name)


def answer_for(kind: str, content: bytes, filename: str, *, first_page: int = 0) -> dict:
    """The same, for bytes built at test time. `first_page` is for pages only."""
    if via_http():
        extra = {"X-First-Page": str(first_page)} if kind == "pages" else None
        status, body = post(kind, content, Path(filename).suffix.lower(), extra_headers=extra)
        assert status == 200, f"{filename}: HTTP {status} {body}"
        return body
    if kind == "document":
        from parse_service.parsing import documents

        return documents.parse(content, filename)
    if kind == "pages":
        from parse_service.parsing import pages

        return pages.render(content, filename, first_page)
    from parse_service.parsing import tables

    return tables.parse(content, filename)


def decoded(part: dict) -> bytes:
    return base64.standard_b64decode(part["source"]["data"])


@pytest.fixture(scope="session")
def baseline() -> dict:
    return json.loads((FIXTURES / "baseline.json").read_text(encoding="utf-8"))
