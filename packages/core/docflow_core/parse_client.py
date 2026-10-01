"""
The worker's side of the parse service (Stage 3c, item 6). HTTP only: this
module opens no file, and importing it pulls in no parsing library, so the
dependency-graph tests allow it anywhere.

What each answer means for the caller:
- `ParseAnswer` with outcome ok / rejected / stopped / crashed -- the
  service answered (a "crashed" job died some other way than our limits);
- `ParseUnavailable` -- **never got in**: no connection, the service busy
  (503) or refusing (401: a token mismatch), after waiting up to
  PARSE_CONNECT_PATIENCE_SECONDS (a stopped Fly machine is started by the
  first request). The document waits; no try is used;
- `ParseLost` -- **got in, never came out**: the file was sent and no answer
  came (the connection dropped, the read timed out, the proxy said 502/504).
  A timeout-class try (item 6a);
- `ParseAnswerInvalid` -- an answer that fails the checks below. Treated as
  a fault, never parsed further.

Every answer is checked before it is used (Section 7.11: a converter's
output is still bytes DocFlow didn't write): only the part types the model
call takes, media types from a fixed list, base64 that decodes, sizes
within caps, catalog codes that exist. Nothing here decodes an image or a
PDF -- that would be parsing again.

Only the file's extension is sent; the sender's filename never leaves the
worker.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import httpx

from docflow_core.config import get_settings
from docflow_core.errors import CATALOG
from docflow_core.file_types import FileTypeName

logger = logging.getLogger(__name__)

# The service's own wall clock per job (apps/parse/parse_service/config.py,
# PARSE_JOB_WALL_SECONDS) plus a margin: the service always answers by then.
PARSE_JOB_WALL_SECONDS = 180
READ_TIMEOUT_SECONDS = PARSE_JOB_WALL_SECONDS + 30
CONNECT_TIMEOUT_SECONDS = 5
# How long "never got in" is retried before the caller waits it out.
PARSE_CONNECT_PATIENCE_SECONDS = 45

MAX_PARTS = 500
MAX_TEXT_CHARS = 5_000_000
MAX_DECODED_BYTES = 40 * 1024 * 1024
MAX_PREVIEW_BYTES = 8 * 1024 * 1024
PART_MEDIA_TYPES = {
    "image": {"image/png", "image/jpeg", "image/gif", "image/webp"},
    "document": {"application/pdf"},
}
PREVIEW_MEDIA_TYPES = {"image/png", "image/jpeg"}
STOP_CAUSES = {"memory", "cpu", "wall_clock", "output_too_large"}
_CODE = re.compile(r"^(DOC|IMP)-\d{3}$")
_EXTENSION = re.compile(r"^\.[a-z0-9]{1,8}$")


class ParseUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # a fixed label, never response text


class ParseLost(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ParseAnswerInvalid(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class ParseAnswer:
    outcome: str  # ok | rejected | stopped | crashed
    code: str | None = None
    cause: str | None = None
    file_type: str | None = None
    parts: list[dict] = field(default_factory=list)
    image_preview: dict | None = None  # {"media_type", "kind", "content": bytes}
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    header_row_number: int | None = None


def extension_of(filename: str | None) -> str:
    suffix = PurePosixPath((filename or "").replace("\\", "/")).suffix.lower()
    return suffix if _EXTENSION.match(suffix) else ".bin"


def _b64(value: object, cap: int, what: str) -> bytes:
    if not isinstance(value, str):
        raise ParseAnswerInvalid(f"{what}_not_text")
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ParseAnswerInvalid(f"{what}_bad_base64") from exc
    if len(data) > cap:
        raise ParseAnswerInvalid(f"{what}_too_large")
    return data


def _check_document(body: dict) -> ParseAnswer:
    outcome = body.get("outcome")
    if outcome == "rejected":
        code = body.get("code")
        if not isinstance(code, str) or not _CODE.match(code) or code not in CATALOG:
            raise ParseAnswerInvalid("unknown_code")
        return ParseAnswer("rejected", code=code)
    if outcome == "stopped":
        if body.get("cause") not in STOP_CAUSES:
            raise ParseAnswerInvalid("unknown_stop_cause")
        return ParseAnswer("stopped", cause=body["cause"])
    if outcome == "crashed":
        cause = body.get("cause")
        named = isinstance(cause, str) and len(cause) < 40
        return ParseAnswer("crashed", cause=cause if named else "unknown")
    if outcome != "ok":
        raise ParseAnswerInvalid("unknown_outcome")
    file_type = body.get("file_type")
    if file_type not in {t.value for t in FileTypeName}:
        raise ParseAnswerInvalid("unknown_file_type")
    parts = body.get("parts")
    if not isinstance(parts, list) or not parts or len(parts) > MAX_PARTS:
        raise ParseAnswerInvalid("bad_parts")
    total = 0
    checked: list[dict] = []
    for part in parts:
        if not isinstance(part, dict):
            raise ParseAnswerInvalid("bad_part")
        kind = part.get("type")
        if kind == "text":
            text_value = part.get("text")
            exact_keys = set(part) == {"type", "text"}
            if not isinstance(text_value, str) or len(text_value) > MAX_TEXT_CHARS or not exact_keys:
                raise ParseAnswerInvalid("bad_text_part")
            total += len(text_value)
            checked.append({"type": "text", "text": text_value})
        elif kind in PART_MEDIA_TYPES:
            source = part.get("source")
            if not isinstance(source, dict) or source.get("type") != "base64":
                raise ParseAnswerInvalid("bad_source")
            media_type = source.get("media_type")
            if media_type not in PART_MEDIA_TYPES[kind]:
                raise ParseAnswerInvalid("bad_media_type")
            total += len(_b64(source.get("data"), MAX_DECODED_BYTES, "part"))
            checked.append(
                {"type": kind, "source": {"type": "base64", "media_type": media_type, "data": source["data"]}}
            )
        else:
            raise ParseAnswerInvalid("bad_part_type")
        if total > MAX_DECODED_BYTES:
            raise ParseAnswerInvalid("answer_too_large")
    preview = body.get("image_preview")
    checked_preview = None
    if preview is not None:
        if not isinstance(preview, dict) or preview.get("media_type") not in PREVIEW_MEDIA_TYPES:
            raise ParseAnswerInvalid("bad_preview")
        checked_preview = {
            "media_type": preview["media_type"],
            "kind": "converted_image",
            "content": _b64(preview.get("data"), MAX_PREVIEW_BYTES, "preview"),
        }
    return ParseAnswer("ok", file_type=file_type, parts=checked, image_preview=checked_preview)


def _check_table(body: dict) -> ParseAnswer:
    outcome = body.get("outcome")
    if outcome in ("rejected", "stopped", "crashed"):
        return _check_document(body)
    if outcome != "ok":
        raise ParseAnswerInvalid("unknown_outcome")
    columns, rows, header = body.get("columns"), body.get("rows"), body.get("header_row_number")
    if (
        not isinstance(columns, list)
        or not all(isinstance(c, str) for c in columns)
        or not isinstance(rows, list)
        or not all(isinstance(r, list) and all(isinstance(c, str) for c in r) for r in rows)
        or not isinstance(header, int)
    ):
        raise ParseAnswerInvalid("bad_table")
    return ParseAnswer("ok", columns=columns, rows=rows, header_row_number=header)


def _post(kind: str, content: bytes, filename: str | None) -> dict:
    settings = get_settings()
    url = f"{settings.parse_service_url.rstrip('/')}/v1/{kind}"
    headers = {"Content-Type": "application/octet-stream", "X-File-Extension": extension_of(filename)}
    if settings.parse_service_token:
        headers["Authorization"] = f"Bearer {settings.parse_service_token}"
    timeout = httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
    deadline = time.monotonic() + PARSE_CONNECT_PATIENCE_SECONDS
    delay = 1.0
    while True:
        reason: str
        try:
            response = httpx.post(url, content=content, headers=headers, timeout=timeout)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            reason = "no_connection"
        except (httpx.ReadTimeout, httpx.ReadError, httpx.WriteError, httpx.WriteTimeout,
                httpx.RemoteProtocolError) as exc:
            raise ParseLost(type(exc).__name__) from exc
        else:
            if response.status_code == 200:
                try:
                    body = response.json()
                except ValueError as exc:
                    raise ParseAnswerInvalid("not_json") from exc
                if not isinstance(body, dict):
                    raise ParseAnswerInvalid("not_an_object")
                return body
            if response.status_code in (502, 504):
                raise ParseLost(f"http_{response.status_code}")
            if response.status_code == 503:
                reason = "busy_or_isolation_failed"
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    delay = max(delay, float(retry_after))
            elif response.status_code == 401:
                raise ParseUnavailable("unauthorized")  # a token mismatch: no point retrying
            else:
                # 400/404/411/413: the request itself; our bug, never the file's.
                raise ParseUnavailable(f"http_{response.status_code}")
        if time.monotonic() + delay > deadline:
            raise ParseUnavailable(reason)
        time.sleep(delay)
        delay = min(delay * 2, 10.0)


def parse_document(content: bytes, filename: str | None) -> ParseAnswer:
    return _check_document(_post("document", content, filename))


def parse_table(content: bytes, filename: str | None) -> ParseAnswer:
    return _check_table(_post("table", content, filename))
