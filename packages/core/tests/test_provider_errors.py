"""
Stage 3d, change A (founder, 2026-10-01): which model-provider answers make a
document wait and which fail it -- proven through the REAL Anthropic SDK
against a local HTTP server standing in for the provider (founder: "test
each through the local stand-in server"). Nothing here is a fake of the SDK:
the server answers on a socket, the SDK parses the answer and raises what it
raises, and `provider_errors.classify` sorts it.

Each case is classified by the status the provider answered with (or, for an
error event in the middle of a streamed answer, by the status the reference
gives that error's type). The SDK's own retries are off (max_retries=0), so
one request is one answer.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import anthropic
import pytest

from docflow_core import provider_errors
from docflow_core.extraction import build_text_content, extract_document, read_buyer_header
from docflow_core.provider_errors import (
    CAUSE_5XX,
    CAUSE_NETWORK,
    CAUSE_OUR_CONFIGURATION,
    CAUSE_OVERLOADED,
    CAUSE_RATE_LIMITED,
)

_MESSAGE_START = {
    "type": "message_start",
    "message": {
        "id": "msg_test_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5",
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 1},
    },
}


def _error_body(error_type: str, message: str, **extra: Any) -> dict[str, Any]:
    error = {"type": error_type, "message": message, **extra}
    return {"type": "error", "error": error, "request_id": "req_t"}


class _StandIn(BaseHTTPRequestHandler):
    """Answers every POST with the server's current `script`."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # keep the test output clean
        pass

    def do_POST(self) -> None:  # noqa: N802 -- http.server's name
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        script = self.server.script  # type: ignore[attr-defined]
        kind = script["kind"]
        if kind == "status":
            body = json.dumps(script["body"]).encode()
            self.send_response(script["status"])
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            for name, value in script.get("headers", {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
            return
        # A streamed answer: chunked SSE.
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        self._chunk(f"event: message_start\ndata: {json.dumps(_MESSAGE_START)}\n\n")
        if kind == "mid_stream_error":
            event = json.dumps(_error_body(script["error_type"], "stand-in"))
            self._chunk(f"event: error\ndata: {event}\n\n")
            self.wfile.write(b"0\r\n\r\n")
            return
        # kind == "drop": the connection dies in the middle of a chunk.
        self.wfile.write(b"40\r\nevent: content_block_start\n")
        self.wfile.flush()
        self.connection.shutdown(socket.SHUT_RDWR)

    def _chunk(self, text: str) -> None:
        data = text.encode()
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request: Any, client_address: Any) -> None:
        pass  # the "drop" case breaks its own connection on purpose


@pytest.fixture(scope="module")
def stand_in():
    server = _QuietServer(("127.0.0.1", 0), _StandIn)
    server.script = {}  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _client(server: ThreadingHTTPServer) -> anthropic.Anthropic:
    host, port = server.server_address[:2]
    return anthropic.Anthropic(api_key="test-key-not-real", base_url=f"http://{host}:{port}", max_retries=0)


def _extract(server: ThreadingHTTPServer, script: dict[str, Any]):
    server.script = script  # type: ignore[attr-defined]
    return extract_document(_client(server), build_text_content("PO TEST-1\n"))


CASES = [
    # (id, script, group, cause, label)
    ("529 overloaded", {"kind": "status", "status": 529, "body": _error_body("overloaded_error", "x")},
     "wait", CAUSE_OVERLOADED, "http_529"),
    ("503", {"kind": "status", "status": 503, "body": _error_body("api_error", "x")},
     "wait", CAUSE_5XX, "http_503"),
    ("500", {"kind": "status", "status": 500, "body": _error_body("api_error", "x")},
     "wait", CAUSE_5XX, "http_500"),
    ("504 timeout_error", {"kind": "status", "status": 504, "body": _error_body("timeout_error", "x")},
     "wait", CAUSE_5XX, "http_504"),
    ("429 rate limited", {"kind": "status", "status": 429, "body": _error_body("rate_limit_error", "x"),
                          "headers": {"retry-after": "7"}},
     "wait", CAUSE_RATE_LIMITED, "http_429"),
    ("429 tier spend cap", {"kind": "status", "status": 429, "body": _error_body(
        "rate_limit_error", "You have reached your API usage limits.",
        details={"error_code": "enforced_spend_limit_reached"})},
     "wait", CAUSE_OUR_CONFIGURATION, "http_429_spend_cap"),
    ("401 key", {"kind": "status", "status": 401, "body": _error_body("authentication_error", "x")},
     "wait", CAUSE_OUR_CONFIGURATION, "http_401"),
    ("402 billing", {"kind": "status", "status": 402, "body": _error_body("billing_error", "x")},
     "wait", CAUSE_OUR_CONFIGURATION, "http_402"),
    ("403 permission", {"kind": "status", "status": 403, "body": _error_body("permission_error", "x")},
     "wait", CAUSE_OUR_CONFIGURATION, "http_403"),
    ("404 model retired",
     {"kind": "status", "status": 404, "body": _error_body("not_found_error", "model: x")},
     "wait", CAUSE_OUR_CONFIGURATION, "http_404"),
    ("400 own spend limit", {"kind": "status", "status": 400, "body": _error_body(
        "invalid_request_error", "You have reached your specified API usage limits. Access resumes ...")},
     "wait", CAUSE_OUR_CONFIGURATION, "http_400_own_spend_limit"),
    ("400 bad request", {"kind": "status", "status": 400, "body": _error_body("invalid_request_error", "x")},
     "fail", None, "http_400"),
    ("413 too large", {"kind": "status", "status": 413, "body": _error_body("request_too_large", "x")},
     "fail", None, "http_413"),
    ("mid-stream overloaded", {"kind": "mid_stream_error", "error_type": "overloaded_error"},
     "wait", CAUSE_OVERLOADED, "http_529_mid_stream"),
    ("mid-stream unknown type", {"kind": "mid_stream_error", "error_type": "something_new"},
     "wait", CAUSE_5XX, "http_500_mid_stream"),
    ("dropped mid-stream", {"kind": "drop"}, "wait", CAUSE_NETWORK, None),
]


@pytest.mark.parametrize(
    ("script", "group", "cause", "label"), [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_each_provider_answer_lands_in_its_group_through_the_real_sdk(stand_in, script, group, cause, label):
    result = _extract(stand_in, script)
    error = result.provider_error
    # RUNBOOK 1.6: on a mismatch, say what the SDK raised and how it was read.
    assert error is not None, f"no provider error recorded; raw_response={result.raw_response}"
    assert (error.group, error.cause) == (group, cause), f"{error} from {result.raw_response}"
    if label is not None:
        assert error.label == label, error
    else:
        assert error.label.startswith("network:"), error
    # A failed call is still DOC-008 on the result; the worker decides wait or
    # fail from provider_error (app.tasks.parse_and_extract).
    assert result.ok is False and result.error_code == "DOC-008"


def test_a_429s_retry_after_is_kept_as_the_floor_of_the_wait(stand_in):
    result = _extract(stand_in, CASES[4][1])
    assert result.provider_error is not None and result.provider_error.retry_after_seconds == 7


def test_nothing_answering_at_all_is_a_network_wait():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    client = anthropic.Anthropic(
        api_key="test-key-not-real", base_url=f"http://127.0.0.1:{port}", max_retries=0
    )
    result = extract_document(client, build_text_content("PO TEST-1\n"))
    assert result.provider_error is not None
    assert (result.provider_error.group, result.provider_error.cause) == ("wait", CAUSE_NETWORK)


def test_the_routing_call_classifies_its_own_failure_too(stand_in):
    """routing_model_failure (founder, 2026-10-01) is raised from this."""
    stand_in.script = CASES[9][1]  # 404: the routing model retired
    routing = read_buyer_header(_client(stand_in), build_text_content("PO TEST-1\n"))
    assert routing.ok is False and routing.provider_error is not None
    assert routing.provider_error.is_our_configuration and routing.provider_error.label == "http_404"


def test_an_sdk_error_that_is_neither_a_status_nor_a_connection_fails_as_before():
    class Odd(anthropic.APIError):
        pass

    error = provider_errors.classify(Odd("odd", request=None, body=None))  # type: ignore[arg-type]
    assert (error.group, error.cause) == ("fail", None)
