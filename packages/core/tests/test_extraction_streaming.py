"""
M1 (Phase 5.5 Stage 1c, D-158 follow-up 2, D-161): the extraction call is
streamed.

Measured before this change (scripts/measure_long_order.py, D-161): a
non-streaming call is silent until the whole answer is ready, and a
connection that is idle for 60 seconds is dropped on the way. So any order
that took the model more than about a minute to write -- 80 lines did --
failed as DOC-008 after three paid attempts, well below the 16,000-token cap.
A streamed answer arrives as it is written, so the connection is never idle,
and the cap can be the model's own maximum.

What these tests hold:
  * the call is a stream, at the large ceiling, with a silence timeout;
  * a truncated answer is still DOC-020 and keeps nothing (Section 7.1);
  * an extraction that runs past its deadline is stopped and fails DOC-020,
    and the deadline ends well inside the stuck-document timeout, so the
    sweep never hands a live extraction to a second worker (H3);
  * a connection dropped mid-answer is a DOC-008 failure, never a crash.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest

from docflow_core import extraction
from docflow_core.constants import STUCK_PROCESSING_TIMEOUT_MIN
from docflow_core.extraction import build_text_content, extract_document

ANSWER = {
    "header": {
        "po_number": "TST-1",
        "order_date": None,
        "requested_delivery_date": None,
        "buyer_name": "Acme Test Stream Buyer",
        "buyer_contact_email": None,
        "ship_to_address": None,
        "payment_terms": None,
        "order_total": "10.00",
        "currency": "USD",
        "notes": None,
    },
    "header_confidence": {"po_number": 0.99, "order_total": 0.99, "currency": 0.95, "buyer_name": 0.95},
    "line_items": [
        {
            "line_number": 1,
            "sku": "TST-0001",
            "description": "Test Widget",
            "quantity": "2",
            "unit": "EA",
            "unit_price": "5.00",
            "line_total": "10.00",
            "confidence": 0.99,
        }
    ],
    "document_notes": "",
    "injection_suspected": False,
    "currency_inferred": False,
}


def _message(stop_reason: str = "end_turn", text: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(ANSWER) if text is None else text)],
        usage=SimpleNamespace(input_tokens=1200, output_tokens=340),
        stop_reason=stop_reason,
    )


class _Stream:
    def __init__(self, message, events: int, on_event=None, fail_after: int | None = None):
        self._message = message
        self._events = events
        self._on_event = on_event
        self._fail_after = fail_after
        self.closed = False
        self.iterated = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def __iter__(self):
        for i in range(self._events):
            if self._fail_after is not None and i == self._fail_after:
                raise httpx2.RemoteProtocolError("peer closed connection mid-body")
            self.iterated += 1
            if self._on_event:
                self._on_event()
            yield SimpleNamespace(type="content_block_delta")

    def get_final_message(self):
        return self._message

    @property
    def current_message_snapshot(self):
        return SimpleNamespace(
            content=[], usage=SimpleNamespace(input_tokens=1200, output_tokens=0), stop_reason=None
        )


class _Client:
    """Only a stream: a non-streaming `create` for extraction fails the test."""

    def __init__(self, stream: _Stream):
        self.stream_obj = stream
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return self.stream_obj

    def create(self, **kwargs):  # pragma: no cover - the assertion is that it is never called
        raise AssertionError("extraction must stream (M1); messages.create was called")


def _extract(client):
    return extract_document(client, build_text_content("PURCHASE ORDER TST-1"))


def test_M1_the_extraction_call_streams_at_the_large_ceiling_with_a_silence_timeout():
    client = _Client(_Stream(_message(), events=3))
    result = _extract(client)

    assert result.ok, result.error
    assert result.lines[0]["sku"] == "TST-0001"
    (call,) = client.calls
    assert call["model"] == extraction.EXTRACTION_MODEL
    assert call["max_tokens"] == extraction.EXTRACTION_MAX_TOKENS
    # The streamed ceiling is the model's own output maximum, far above the
    # non-streaming 16,000 that a dropped connection never even reached.
    assert extraction.EXTRACTION_MAX_TOKENS >= 64000
    assert call["timeout"] == extraction.EXTRACTION_STREAM_IDLE_SECONDS
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert client.stream_obj.closed


def test_M1_a_truncated_streamed_answer_is_DOC_020_and_keeps_nothing():
    cut_off = _message(stop_reason="max_tokens", text='{"header": {"po_number": "TS')
    client = _Client(_Stream(cut_off, events=2))
    result = _extract(client)

    assert not result.ok
    assert result.error_code == "DOC-020"
    assert not result.lines and not result.header
    # Paid for, so the circuit breaker must see it (Section 7.9).
    assert (result.input_tokens, result.output_tokens) == (1200, 340)


def test_M1_an_extraction_past_its_deadline_is_stopped_and_fails_DOC_020(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(extraction.time, "monotonic", lambda: clock["now"])

    def tick():
        clock["now"] += extraction.EXTRACTION_DEADLINE_SECONDS / 4

    stream = _Stream(_message(), events=100, on_event=tick)
    result = _extract(_Client(stream))

    assert not result.ok
    assert result.error_code == "DOC-020"
    assert result.raw_response["stopped"] == "deadline"
    assert not result.lines and not result.header
    # Stopped at the deadline, not read to the end; the connection is released.
    assert stream.iterated < 100
    assert stream.closed
    assert result.input_tokens == 1200


def test_M1_the_extraction_deadline_ends_well_inside_the_stuck_timeout():
    """The stuck-document sweep takes over a claim older than
    STUCK_PROCESSING_TIMEOUT_MIN (H3). A live extraction must always finish or
    stop first -- deadline plus one full silence timeout -- with room left for
    parsing and conversion before the call, or a second worker would pay for a
    second extraction of the same order."""
    worst_case = extraction.EXTRACTION_DEADLINE_SECONDS + extraction.EXTRACTION_STREAM_IDLE_SECONDS
    assert worst_case <= 0.8 * STUCK_PROCESSING_TIMEOUT_MIN * 60


def test_M1_a_connection_dropped_mid_answer_is_DOC_008_not_a_crash():
    stream = _Stream(_message(), events=10, fail_after=4)
    result = _extract(_Client(stream))

    assert not result.ok
    assert result.error_code == "DOC-008"
    assert result.raw_response["error_type"] == "RemoteProtocolError"
    assert stream.closed


@pytest.mark.parametrize("stop_reason", ["refusal"])
def test_M1_an_answer_that_is_not_a_finished_reply_is_never_kept(stop_reason):
    """Only `end_turn` is a finished answer. Anything else that isn't the
    token cap is still refused as malformed rather than half-kept."""
    client = _Client(_Stream(_message(stop_reason=stop_reason, text=""), events=1))
    result = _extract(client)
    assert not result.ok
    assert not result.lines


# ── The read budget covers the whole read, retries included (founder, 2026-09-26) ──


def test_M1_the_deadline_counts_time_spent_before_the_first_event(monkeypatch):
    """The SDK's own retries (a refused connection, a 529) happen inside
    `messages.stream(...)`, before any event. That time is part of the same
    read: the clock started before the call, so the stream is stopped at
    the first event past the deadline, not given a fresh 20 minutes."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(extraction.time, "monotonic", lambda: clock["now"])
    stream = _Stream(_message(), events=5)

    class _SlowToStart(_Client):
        def stream(self, **kwargs):
            clock["now"] += extraction.EXTRACTION_DEADLINE_SECONDS + 1  # retries ate the budget
            return super().stream(**kwargs)

    result = _extract(_SlowToStart(stream))
    assert result.error_code == "DOC-020"
    assert result.raw_response["stopped"] == "deadline"
    assert stream.iterated == 1


def test_M1_a_caller_deadline_is_the_one_used_and_bounds_the_silence_timeout(monkeypatch):
    """The worker passes the deadline it set when it claimed the document, so
    parsing, conversion and the routing call all spend the same budget."""
    clock = {"now": 5000.0}
    monkeypatch.setattr(extraction.time, "monotonic", lambda: clock["now"])
    client = _Client(_Stream(_message(), events=2))
    result = extract_document(client, build_text_content("PO TST-1"), deadline=clock["now"] + 45)
    assert result.ok
    # 45 s left: the silence timeout may not reach past the deadline.
    assert client.calls[0]["timeout"] == 45


def test_M1_the_routing_call_and_the_token_count_have_short_timeouts():
    """Neither may run on the SDK's 10-minute default inside a claim."""
    calls = []

    class _Recording:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            calls.append(("routing", kwargs.get("timeout")))
            return _message(text=json.dumps({"buyer_name": None, "buyer_contact_email": None,
                                             "buyer_confidence": 0.0, "injection_suspected": False}))

        def count_tokens(self, **kwargs):
            calls.append(("count", kwargs.get("timeout")))
            return SimpleNamespace(input_tokens=10)

    extraction.read_buyer_header(_Recording(), build_text_content("PO TST-1"))
    extraction._count_example_tokens(_Recording(), [])
    assert calls == [
        ("routing", extraction.ROUTING_TIMEOUT_SECONDS),
        ("count", extraction.COUNT_TOKENS_TIMEOUT_SECONDS),
    ]


def test_M1_the_whole_read_ends_well_inside_the_stuck_timeout():
    """From the claim: the read budget (parsing, conversion, routing, the
    streamed answer, SDK retries), one last silence timeout, then the example
    token count after the answer (three attempts). All of it must end before
    the sweep may take the claim over (H3)."""
    worst_case = (
        extraction.EXTRACTION_DEADLINE_SECONDS
        + extraction.EXTRACTION_STREAM_IDLE_SECONDS
        + 3 * extraction.COUNT_TOKENS_TIMEOUT_SECONDS
    )
    assert worst_case <= 0.8 * STUCK_PROCESSING_TIMEOUT_MIN * 60


# ── Every paid call leaves a cost record (founder, 2026-09-26) ─────────────


def test_cost_a_stream_dropped_mid_answer_keeps_the_input_it_was_billed_for():
    stream = _Stream(_message(), events=10, fail_after=4)
    result = _extract(_Client(stream))
    assert result.error_code == "DOC-008"
    # message_start had arrived: the input was billed and is recorded.
    assert result.input_tokens == 1200
    assert result.est_cost_usd is not None and result.est_cost_usd > 0
    assert result.raw_response["cost_complete"] is False


def test_cost_a_call_refused_before_it_started_records_no_tokens():
    class _Refused(_Client):
        def stream(self, **kwargs):
            raise httpx2.ConnectError("connection refused")

    result = _extract(_Refused(_Stream(_message(), events=1)))
    assert result.error_code == "DOC-008"
    assert result.input_tokens is None and result.est_cost_usd is None
