"""
The worker's side of the parse service (Stage 3c, item 6; test F1's client
rows): every way the service can answer, or fail to, lands on the right side
of "never got in" (wait, no try used) against "got in, never came out" (a
timeout-class try), and no answer is used before it is checked.

A small HTTP server on this machine plays the service: it can refuse, be
busy, drop the connection mid-request, or answer something malformed. The
real service's behaviour (sandbox, limits) is tested in apps/parse; the
same client runs against the real image in CI's worker job.
"""

from __future__ import annotations

import base64
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from docflow_core import parse_client
from docflow_core.config import get_settings


class _Stub:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.requests: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                stub.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                stub.behaviour(self)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def _json(status: int, body: object, headers: dict | None = None):
    def respond(handler):
        data = json.dumps(body).encode()
        handler.send_response(status)
        for name, value in (headers or {}).items():
            handler.send_header(name, value)
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    return respond


@pytest.fixture
def service(monkeypatch):
    stubs: list[_Stub] = []

    def start(behaviour, *, token="tok"):
        stub = _Stub(behaviour)
        stubs.append(stub)
        monkeypatch.setenv("PARSE_SERVICE_URL", stub.url)
        monkeypatch.setenv("PARSE_SERVICE_TOKEN", token)
        get_settings.cache_clear()
        return stub

    monkeypatch.setattr(parse_client, "PARSE_CONNECT_PATIENCE_SECONDS", 3)
    yield start
    for stub in stubs:
        stub.close()
    get_settings.cache_clear()


class _FakeClock:
    """parse_client's clock for the two busy-retry tests: time moves only when
    the client sleeps, so how long a request takes on a busy machine can't
    decide whether it retries (D-212 item 8)."""

    # _post reads the clock at most twice between sleeps (to set the deadline,
    # then to check it). A loop that reads it more without sleeping would never
    # see fake time move, so it fails the test instead of hanging. 10 is well
    # above 2, and still trips within a few turns of such a loop.
    MAX_READS_WITHOUT_SLEEP = 10

    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []
        self._reads = 0

    def monotonic(self) -> float:
        self._reads += 1
        if self._reads > self.MAX_READS_WITHOUT_SLEEP:
            pytest.fail(f"parse_client read the clock {self._reads} times without sleeping")
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        self._reads = 0


@pytest.fixture
def fake_clock(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(parse_client, "time", clock)
    return clock


OK_ANSWER = {
    "outcome": "ok",
    "file_type": "docx",
    "parts": [{"type": "text", "text": "PO BCH-2291"}],
    "image_preview": None,
}


def test_an_answer_comes_back_checked(service):
    stub = service(_json(200, OK_ANSWER))
    answer = parse_client.parse_document(b"bytes", "Order from Acme's Test Coffee House.docx")
    assert answer.outcome == "ok" and answer.parts == [{"type": "text", "text": "PO BCH-2291"}]
    request = stub.requests[0]
    assert request["path"] == "/v1/document"
    assert request["headers"]["Authorization"] == "Bearer tok"
    # Only the extension leaves the worker, never the sender's filename.
    assert request["headers"]["X-File-Extension"] == ".docx"
    assert b"Acme" not in json.dumps(request["headers"]).encode()


@pytest.mark.parametrize(
    "filename, extension",
    [("po.PDF", ".pdf"), ("../../etc/passwd", ".bin"), ("no-extension", ".bin"), (None, ".bin"),
     ("x.reallylongext", ".bin")],
)
def test_the_extension_sent_is_always_a_plain_extension(filename, extension):
    assert parse_client.extension_of(filename) == extension


# ── Never got in: wait, no try used ────────────────────────────────────────


def test_nothing_listening_is_never_got_in(monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("PARSE_SERVICE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(parse_client, "PARSE_CONNECT_PATIENCE_SECONDS", 2)
    get_settings.cache_clear()
    try:
        with pytest.raises(parse_client.ParseUnavailable) as excinfo:
            parse_client.parse_document(b"x", "po.pdf")
        assert excinfo.value.reason == "no_connection"
    finally:
        get_settings.cache_clear()


def test_busy_is_retried_then_never_got_in(service, fake_clock):
    seen_at: list[float] = []
    busy = _json(503, {"error": "busy"}, {"Retry-After": "1"})
    service(lambda handler: (seen_at.append(fake_clock.now), busy(handler)))
    with pytest.raises(parse_client.ParseUnavailable) as excinfo:
        parse_client.parse_document(b"x", "po.pdf")
    assert excinfo.value.reason == "http_503"
    # Within the 3 s patience: Retry-After's 1 s, then doubled to 2 s; the
    # next wait, 4 s, would pass the deadline.
    assert seen_at == [0.0, 1.0, 3.0]
    assert fake_clock.sleeps == [1.0, 2.0]


def test_busy_then_free_parses(service, fake_clock):
    seen_at: list[float] = []
    answers = [_json(503, {"error": "busy"}, {"Retry-After": "1"}), _json(200, OK_ANSWER)]
    service(lambda handler: (seen_at.append(fake_clock.now), answers.pop(0)(handler)))
    assert parse_client.parse_document(b"x", "po.docx").outcome == "ok"
    assert seen_at == [0.0, 1.0]
    assert fake_clock.sleeps == [1.0]


def test_a_slow_busy_answer_uses_up_the_patience(service, fake_clock):
    busy = _json(503, {"error": "busy"}, {"Retry-After": "1"})

    def slow_busy(handler):
        fake_clock.now += 2.5  # the request itself took 2.5 s of the 3 s patience
        busy(handler)

    stub = service(slow_busy)
    with pytest.raises(parse_client.ParseUnavailable) as excinfo:
        parse_client.parse_document(b"x", "po.pdf")
    assert excinfo.value.reason == "http_503"
    # 2.5 s gone plus the 1 s wait would pass the deadline: no retry.
    assert len(stub.requests) == 1 and fake_clock.sleeps == []


def test_a_token_mismatch_is_never_got_in_and_not_retried(service):
    stub = service(_json(401, {"error": "unauthorized"}))
    with pytest.raises(parse_client.ParseUnavailable) as excinfo:
        parse_client.parse_document(b"x", "po.pdf")
    assert excinfo.value.reason == "unauthorized"
    assert len(stub.requests) == 1


# ── Got in, never came out: a timeout-class try ────────────────────────────


def test_a_connection_dropped_after_the_file_was_sent_is_lost(service):
    def drop(handler):
        handler.connection.shutdown(socket.SHUT_RDWR)
        handler.connection.close()

    service(drop)
    with pytest.raises(parse_client.ParseLost):
        parse_client.parse_document(b"x", "po.pdf")


@pytest.mark.parametrize("status", [502, 504])
def test_a_proxy_error_after_sending_is_lost(service, status):
    service(_json(status, {}))
    with pytest.raises(parse_client.ParseLost):
        parse_client.parse_document(b"x", "po.pdf")


# ── Answers that are checked, and refused ──────────────────────────────────


def _b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode()


@pytest.mark.parametrize(
    "body, reason",
    [
        ({"outcome": "maybe"}, "unknown_outcome"),
        ({"outcome": "rejected", "code": "DOC-999"}, "unknown_code"),
        ({"outcome": "rejected", "code": "<script>"}, "unknown_code"),
        ({"outcome": "stopped", "cause": "boredom"}, "unknown_stop_cause"),
        ({**OK_ANSWER, "file_type": "exe"}, "unknown_file_type"),
        ({**OK_ANSWER, "parts": []}, "bad_parts"),
        ({**OK_ANSWER, "parts": [{"type": "html", "text": "<b>x</b>"}]}, "bad_part_type"),
        ({**OK_ANSWER, "parts": [{"type": "text", "text": "x", "extra": 1}]}, "bad_text_part"),
        (
            {
                **OK_ANSWER,
                "parts": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/svg+xml", "data": _b64(b"<svg/>")}}
                ],
            },
            "bad_media_type",
        ),
        (
            {**OK_ANSWER, "parts": [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                  "data": "not base64!!"}}]},
            "part_bad_base64",
        ),
        ({**OK_ANSWER, "image_preview": {"media_type": "text/html", "data": ""}}, "bad_preview"),
        # `error` (D-203, PR 1b-i): a class name and `module:function:line`, nothing else.
        ({"outcome": "error", "location": "parse_service.job:_parse:7"}, "bad_error_type"),
        (
            {"outcome": "error", "error_type": "<b>x</b>", "location": "parse_service.job:_parse:7"},
            "bad_error_type",
        ),
        ({"outcome": "error", "error_type": "TypeError"}, "bad_error_location"),
        (
            {"outcome": "error", "error_type": "TypeError", "location": "the po_number was 4471"},
            "bad_error_location",
        ),
    ],
)
def test_a_malformed_answer_is_refused_not_used(service, body, reason):
    service(_json(200, body))
    with pytest.raises(parse_client.ParseAnswerInvalid) as excinfo:
        parse_client.parse_document(b"x", "po.pdf")
    assert excinfo.value.reason == reason


def test_an_answer_that_is_not_json_is_refused(service):
    def garbage(handler):
        handler.send_response(200)
        handler.send_header("Content-Length", "5")
        handler.end_headers()
        handler.wfile.write(b"oops!")

    service(garbage)
    with pytest.raises(parse_client.ParseAnswerInvalid) as excinfo:
        parse_client.parse_document(b"x", "po.pdf")
    assert excinfo.value.reason == "not_json"


@pytest.mark.parametrize(
    "body, outcome, field, value",
    [
        ({"outcome": "rejected", "code": "DOC-017"}, "rejected", "code", "DOC-017"),
        ({"outcome": "stopped", "cause": "memory"}, "stopped", "cause", "memory"),
        ({"outcome": "crashed", "cause": "signal_11"}, "crashed", "cause", "signal_11"),
        (
            {"outcome": "crashed", "cause": "signal_11", "release": "deployment-P7"},
            "crashed",
            "release",
            "deployment-P7",
        ),
        ({"outcome": "crashed", "cause": "signal_11"}, "crashed", "release", "unknown"),
        ({"outcome": "crashed", "cause": "exit_1", "release": "no spaces!"}, "crashed", "release", "unknown"),
    ],
)
def test_the_service_s_verdicts_come_through(service, body, outcome, field, value):
    service(_json(200, body))
    answer = parse_client.parse_document(b"x", "po.pdf")
    assert answer.outcome == outcome and getattr(answer, field) == value


def test_an_image_preview_is_decoded(service):
    service(_json(200, {**OK_ANSWER, "file_type": "tiff",
                        "image_preview": {"media_type": "image/png", "kind": "converted_image",
                                          "data": _b64(b"\x89PNG fake")}}))
    answer = parse_client.parse_document(b"x", "po.tif")
    assert answer.image_preview == {"media_type": "image/png", "kind": "converted_image",
                                    "content": b"\x89PNG fake"}


def test_a_table_answer_is_checked(service):
    service(_json(200, {"outcome": "ok", "columns": ["SKU"], "rows": [["A"], [1]], "header_row_number": 1}))
    with pytest.raises(parse_client.ParseAnswerInvalid):
        parse_client.parse_table(b"x", "catalog.csv")

def test_an_error_answer_comes_through_with_its_type_location_and_release(service):
    """D-203, PR 1b-i: ours, not the file's; the worker fails it with DOC-030."""
    body = {
        "outcome": "error",
        "error_type": "TypeError",
        "location": "parse_service.parsing.conversion:convert_xls:281",
        "release": "deployment-P7",
    }
    service(_json(200, body))
    answer = parse_client.parse_document(b"x", "po.xls")
    assert (answer.outcome, answer.error_type, answer.location, answer.release) == (
        "error", "TypeError", "parse_service.parsing.conversion:convert_xls:281", "deployment-P7"
    )
    service(_json(200, body))
    assert parse_client.parse_table(b"x", "catalog.xls").outcome == "error"


# ── What an email unwrap couldn't read (D-207, PR 1b-ii) ───────────────────


def test_what_an_unwrap_lost_comes_through_checked(service):
    lost = [{"code": "VAL-017", "filename": "old_orders.zip"}, {"code": "VAL-018", "filename": None}]
    service(_json(200, {**OK_ANSWER, "file_type": "eml", "lost": lost}))
    assert parse_client.parse_document(b"x", "po.eml").lost == lost


def test_an_answer_from_before_1b_ii_has_lost_nothing(service):
    service(_json(200, OK_ANSWER))
    assert parse_client.parse_document(b"x", "po.docx").lost == []


@pytest.mark.parametrize(
    "lost, reason",
    [
        ("VAL-017", "bad_lost"),
        ([{"code": "VAL-017", "filename": "a.zip"}] * (parse_client.MAX_LOST + 1), "bad_lost"),
        (["VAL-017"], "bad_lost_item"),
        ([{"code": "VAL-017"}], "bad_lost_item"),
        ([{"code": "VAL-017", "filename": "a.zip", "text": "PO 4471"}], "bad_lost_item"),
        ([{"code": "VAL-001", "filename": "a.zip"}], "bad_lost_item"),
        ([{"code": "DOC-030", "filename": None}], "bad_lost_item"),
        # A label is the parse service's sanitized one: its alphabet, at most 80.
        ([{"code": "VAL-017", "filename": "Purchase Order.pdf"}], "bad_lost_item"),
        ([{"code": "VAL-017", "filename": "<b>po</b>.pdf"}], "bad_lost_item"),
        ([{"code": "VAL-017", "filename": "x" * 81}], "bad_lost_item"),
        ([{"code": "VAL-017", "filename": ""}], "bad_lost_item"),
        ([{"code": "VAL-017", "filename": None}], "bad_lost_item"),
        ([{"code": "VAL-018", "filename": "body.txt"}], "bad_lost_item"),
    ],
)
def test_a_malformed_lost_list_is_refused_not_used(service, lost, reason):
    service(_json(200, {**OK_ANSWER, "lost": lost}))
    with pytest.raises(parse_client.ParseAnswerInvalid) as excinfo:
        parse_client.parse_document(b"x", "po.eml")
    assert excinfo.value.reason == reason


def test_an_80_character_label_is_accepted():
    body = {**OK_ANSWER, "lost": [{"code": "VAL-017", "filename": "x" * 76 + ".zip"}]}
    assert parse_client._check_document(body).lost[0]["filename"] == "x" * 76 + ".zip"
