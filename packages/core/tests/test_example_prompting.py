"""
Approved-example prompting, the parts that need no database (Section 7.13;
slice 5.10, D-141). The database-backed rules -- tenant isolation, the
approved-only and threshold gates, most-recent-first -- are in
apps/api/tests/test_example_prompting_db.py against the real schema.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

import anthropic
import pytest

from docflow_core import example_prompting as ep
from docflow_core.constants import EXAMPLE_TEXT_CHAR_BUDGET, ROUTING_TEXT_CHAR_BUDGET
from docflow_core.extraction import ROUTING_MODEL, PromptExample, RoutingResult, read_buyer_header

# ── the example's shape ─────────────────────────────────────────────────────


def test_an_example_shows_printed_values_only_never_catalog_matches_or_ids():
    snapshot = {
        "document_id": "d1",
        "header": {"po_number": "BCH-2240", "order_total": "800.50", "currency": "USD"},
        "lines": [
            {
                "line_number": 1, "sku": "CF-1001", "description": "Colombian", "quantity": "9",
                "unit": "CS", "unit_price": "46.00", "line_total": "414.00",
                "matched_item_id": "item-uuid", "matched_uom": "CS", "catalog_sku": "INT-99",
            }
        ],
    }
    shaped = ep.example_extraction(json.dumps(snapshot))

    assert set(shaped) == {"header", "line_items"}
    assert shaped["header"]["po_number"] == "BCH-2240"
    assert shaped["header"]["notes"] is None  # every schema field present, like the model's own output
    assert "document_id" not in json.dumps(shaped)
    line = shaped["line_items"][0]
    assert set(line) == {"line_number", "sku", "description", "quantity", "unit", "unit_price", "line_total"}
    assert "INT-99" not in json.dumps(shaped) and "item-uuid" not in json.dumps(shaped)


def test_example_text_is_cut_to_the_budget_and_says_so():
    long_text = "x" * (EXAMPLE_TEXT_CHAR_BUDGET + 500)
    cut = ep._truncate(long_text, EXAMPLE_TEXT_CHAR_BUDGET)
    assert cut.startswith("x" * EXAMPLE_TEXT_CHAR_BUDGET)
    assert len(cut) < EXAMPLE_TEXT_CHAR_BUDGET + 50 and cut.endswith("truncated ...]")
    assert ep._truncate("short", EXAMPLE_TEXT_CHAR_BUDGET) == "short"


def test_routing_reads_only_the_top_of_a_text_document_inside_the_document_fence():
    content = ep.routing_content([{"type": "text", "text": "HEADER " + "y" * (ROUTING_TEXT_CHAR_BUDGET * 3)}])
    assert len(content) == 1
    body = content[0]["text"]
    assert body.startswith("<document>") and body.rstrip().endswith("</document>")
    assert len(body) < ROUTING_TEXT_CHAR_BUDGET + 40


def test_routing_keeps_a_page_read_visually_whole():
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}}
    content = ep.routing_content([image])
    assert content[0]["text"] == "<document>" and content[1] is image and content[-1]["text"] == "</document>"


# ── the routing call ────────────────────────────────────────────────────────


@dataclass
class _Block:
    type: str
    text: str


@dataclass
class _Usage:
    input_tokens: int
    output_tokens: int


@dataclass
class _Message:
    content: list
    usage: _Usage


class _RoutingClient:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return _Message([_Block("text", json.dumps(self.payload))], _Usage(1000, 40))


def test_the_routing_call_uses_the_cheap_model_and_prices_it_as_such():
    client = _RoutingClient(
        {"buyer_name": "Bella's Coffee House", "buyer_contact_email": None,
         "buyer_confidence": 0.93, "injection_suspected": False}
    )
    result = read_buyer_header(client, [{"type": "text", "text": "<document>x</document>"}])

    assert client.calls[0]["model"] == ROUTING_MODEL
    assert "json_schema" in json.dumps(client.calls[0]["output_config"])
    assert result.ok and result.buyer_name == "Bella's Coffee House"
    # $1/M in + $5/M out
    assert result.est_cost_usd == Decimal("0.0012")


def test_a_routing_api_failure_is_a_failed_run_not_an_exception():
    # Any APIError subclass will do; build one without a real HTTP request.
    error = anthropic.APIConnectionError.__new__(anthropic.APIConnectionError)
    client = _RoutingClient(error=error)
    result = read_buyer_header(client, [])
    assert result.ok is False and result.error_code == "DOC-008"


# ── the whole decision, with the database replaced ─────────────────────────


class _NullSession:
    pass


@contextlib.contextmanager
def _factory(tenant_id):
    yield _NullSession()


def _routing(**overrides) -> RoutingResult:
    base = dict(
        ok=True, model_id=ROUTING_MODEL, prompt_hash="h", schema_version="r", raw_response={},
        buyer_name="Bella's Coffee House", buyer_confidence=0.95,
    )
    base.update(overrides)
    return RoutingResult(**base)


@pytest.fixture
def world(monkeypatch):
    """Every database question the planner asks, answered from a dict."""
    state = {
        "enabled": True,
        "sender": None,
        "any_qualifies": True,
        "routing_buyer": None,
        "approved": 10,
        "examples": [PromptExample("e1", "text", {"header": {}, "line_items": []})],
        "routing_calls": 0,
    }

    monkeypatch.setattr(ep, "is_enabled", lambda s, t: state["enabled"])
    monkeypatch.setattr(ep, "buyer_from_sender", lambda s, t, e: state["sender"])
    monkeypatch.setattr(ep, "any_buyer_qualifies", lambda s, t: state["any_qualifies"])
    monkeypatch.setattr(ep, "buyer_from_routing", lambda s, t, r: state["routing_buyer"])
    monkeypatch.setattr(ep, "approved_count", lambda s, t, b: state["approved"])
    monkeypatch.setattr(ep, "select_examples", lambda *a, **k: state["examples"])

    def fake_read(client, content):
        state["routing_calls"] += 1
        return _routing()

    monkeypatch.setattr(ep, "read_buyer_header", fake_read)
    return state


def _plan():
    return ep.plan(object(), uuid4(), uuid4(), sender_email="a@b.example", parts=[], session_factory=_factory)


def test_flag_off_means_nothing_runs_at_all(world):
    world["enabled"] = False
    plan = _plan()
    assert plan.examples == [] and plan.outcome == "flag_off" and world["routing_calls"] == 0


def test_a_sender_match_skips_the_routing_call(world):
    buyer = uuid4()
    world["sender"] = (buyer, "sender_email")
    plan = _plan()
    assert plan.buyer_id == buyer and plan.identified_by == "sender_email"
    assert world["routing_calls"] == 0 and plan.routing is None
    assert plan.outcome == "examples_used" and len(plan.examples) == 1


def test_no_qualifying_buyer_anywhere_means_no_routing_call_and_no_cost(world):
    world["any_qualifies"] = False
    plan = _plan()
    assert world["routing_calls"] == 0 and plan.outcome == "no_buyer"


def test_the_header_read_identifies_the_buyer_when_the_sender_cannot(world):
    buyer = uuid4()
    world["routing_buyer"] = buyer
    plan = _plan()
    assert world["routing_calls"] == 1 and plan.routing is not None
    assert plan.buyer_id == buyer and plan.identified_by == "header_read"
    assert plan.outcome == "examples_used"


def test_the_header_read_is_recorded_even_when_it_finds_no_buyer(world):
    plan = _plan()
    assert plan.routing is not None and plan.examples == [] and plan.outcome == "no_buyer"


def test_below_ten_approved_orders_no_example_is_ever_used(world):
    world["sender"] = (uuid4(), "sender_email")
    world["approved"] = 9
    plan = _plan()
    assert plan.examples == [] and plan.outcome == "below_threshold"


# ── buyer_from_routing's own gates ──────────────────────────────────────────


def test_a_routing_answer_below_threshold_or_suspecting_injection_identifies_nobody(monkeypatch):
    monkeypatch.setattr(ep.buyers, "find_buyer_by_email", lambda *a: uuid4())
    monkeypatch.setattr(ep.buyers, "find_buyer_by_normalized_name", lambda *a: uuid4())
    session = _NullSession()
    tenant = uuid4()

    assert ep.buyer_from_routing(session, tenant, _routing(buyer_confidence=0.79)) is None
    assert ep.buyer_from_routing(session, tenant, _routing(injection_suspected=True)) is None
    assert ep.buyer_from_routing(session, tenant, _routing(ok=False)) is None
    assert ep.buyer_from_routing(session, tenant, _routing(buyer_confidence=0.80)) is not None


def test_a_free_mail_domain_never_identifies_a_buyer(monkeypatch):
    monkeypatch.setattr(ep.buyers, "find_buyer_by_email", lambda *a: None)

    class _Explodes:
        def execute(self, *a, **k):  # the domain query must not even run
            raise AssertionError("queried buyers by a free-mail domain")

    assert ep.buyer_from_sender(_Explodes(), uuid4(), "someone@gmail.com") is None
    assert ep.buyer_from_sender(_Explodes(), uuid4(), None) is None
