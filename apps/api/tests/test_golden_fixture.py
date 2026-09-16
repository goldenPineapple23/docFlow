"""
CLAUDE.md Section 8.3 golden fixture, wired to the real extraction pipeline
(Phase 1). Runs two ways, per Section 5's "golden fixture runs two ways" rule:

  - `test_recorded_response_matches_section_8_3` replays a recorded API
    response (`fixtures/golden/recorded_response.json`) through
    `docflow_core.extraction.extract_document` with the Anthropic client
    mocked out. No network call, deterministic, runs in the default CI suite.
    This is the one that must never be skipped or marked flaky.
  - `test_live_extraction_matches_section_8_3` makes one real call to the
    pinned model and asserts the same values. Marked `live_api` and excluded
    from the default test run (see apps/api/pyproject.toml addopts) --
    required to pass before shipping any prompt/schema/model-ID change, and
    at every phase checkpoint, per CLAUDE.md Section 7.1 and Section 5.

The recorded response was captured from one real call to claude-sonnet-5
against docs/sample_po.txt (see DECISIONS.md) -- it is not hand-built.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from docflow_core.extraction import build_text_content, extract_document

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_PO_PATH = REPO_ROOT / "docs" / "sample_po.txt"
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "golden"
EXPECTED_OUTPUT_PATH = FIXTURES_DIR / "expected_output.json"
RECORDED_RESPONSE_PATH = FIXTURES_DIR / "recorded_response.json"


@dataclass
class _FakeTextBlock:
    type: str
    text: str


@dataclass
class _FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _FakeMessage:
    content: list[_FakeTextBlock]
    usage: _FakeUsage


class _FakeMessagesResource:
    def __init__(self, response_payload: dict[str, Any]):
        self._response_payload = response_payload

    def create(self, **kwargs) -> _FakeMessage:
        return _FakeMessage(
            content=[_FakeTextBlock(type="text", text=json.dumps(self._response_payload))],
            usage=_FakeUsage(input_tokens=2617, output_tokens=813),
        )


class _FakeAnthropicClient:
    def __init__(self, response_payload: dict[str, Any]):
        self.messages = _FakeMessagesResource(response_payload)


def _assert_matches_section_8_3(result) -> None:
    assert result.ok, result.error
    header = result.header
    assert header["po_number"] == "BCH-2291"
    assert header["order_date"] == "2026-03-14"
    assert header["requested_delivery_date"] == "2026-03-21"
    assert header["buyer_name"] == "Bella's Coffee House"
    assert header["buyer_contact_email"] == "orders@bellascoffee.com"
    assert header["ship_to_address"] == "Bella's Coffee House, 1442 Oak Street, Portland, OR 97204"
    assert header["payment_terms"] == "Net 30"
    assert header["order_total"] == Decimal("1356.00")
    assert header["currency"] == "USD"
    assert header["notes"] == "Please deliver before 10am. Back door access only."

    assert result.injection_suspected is False
    # D-011: currency is only inferable from "$" symbols in sample_po.txt,
    # never stated as an ISO code (CLAUDE.md Section 7.1).
    assert result.currency_inferred is True
    assert result.header_confidence["currency"] <= 0.6

    expected_lines = {
        "CF-1001": ("Colombian Whole Bean 5lb", "12", "CS", "47.50", "570.00"),
        "CF-2210": ("Ethiopian Yirgacheffe 5lb", "6", "CS", "62.00", "372.00"),
        "SY-0045": ("Vanilla Syrup 750ml", "24", "EA", "8.25", "198.00"),
        "CUP-12": ("12oz Paper Cups (1000ct)", "4", "BOX", "54.00", "216.00"),
    }
    assert len(result.lines) == 4
    for line in result.lines:
        description, quantity, unit, unit_price, line_total = expected_lines[line["sku"]]
        assert line["description"] == description
        assert line["quantity"] == Decimal(quantity)
        assert line["unit"] == unit
        assert line["unit_price"] == Decimal(unit_price)
        assert line["line_total"] == Decimal(line_total)

    # Line totals reconcile and header total equals the sum of lines
    # (CLAUDE.md Section 7.7 / Section 8.3).
    line_sum = sum((line["line_total"] for line in result.lines), Decimal("0"))
    assert line_sum == header["order_total"]
    for line in result.lines:
        assert line["quantity"] * line["unit_price"] == line["line_total"]


def test_sample_po_fixture_exists_and_is_readable():
    assert SAMPLE_PO_PATH.exists(), f"Golden fixture input missing: {SAMPLE_PO_PATH}"
    text = SAMPLE_PO_PATH.read_text(encoding="utf-8")
    assert "BCH-2291" in text
    assert "Bella's Coffee House" in text


def test_expected_output_matches_section_8_3():
    """Hand-derived reference values (see D-011); a human-readable cross-check."""
    expected = json.loads(EXPECTED_OUTPUT_PATH.read_text(encoding="utf-8"))
    header = expected["header"]

    assert header["po_number"] == "BCH-2291"
    assert header["order_date"] == "2026-03-14"
    assert header["requested_delivery_date"] == "2026-03-21"
    assert header["buyer_name"] == "Bella's Coffee House"
    assert header["buyer_contact_email"] == "orders@bellascoffee.com"
    assert header["payment_terms"] == "Net 30"
    assert header["order_total"] == "1356.00"
    assert header["currency"] == "USD"

    assert len(expected["line_items"]) == 4
    assert {li["sku"] for li in expected["line_items"]} == {"CF-1001", "CF-2210", "SY-0045", "CUP-12"}
    assert expected["injection_suspected"] is False
    assert expected["currency_inferred"] is True


def test_expected_output_line_totals_reconcile():
    expected = json.loads(EXPECTED_OUTPUT_PATH.read_text(encoding="utf-8"))
    line_sum = sum(Decimal(li["line_total"]) for li in expected["line_items"])
    assert line_sum == Decimal(expected["header"]["order_total"])
    for li in expected["line_items"]:
        assert Decimal(li["quantity"]) * Decimal(li["unit_price"]) == Decimal(li["line_total"])


def test_recorded_response_matches_section_8_3():
    """
    CI-safe replay: no network call. This is the non-negotiable regression
    anchor -- it must never be skipped or marked flaky (CLAUDE.md Section 10).
    """
    assert RECORDED_RESPONSE_PATH.exists(), (
        f"Recorded golden response missing: {RECORDED_RESPONSE_PATH}. "
        "This must be a real captured API response, not hand-built -- see DECISIONS.md."
    )
    recorded_payload = json.loads(RECORDED_RESPONSE_PATH.read_text(encoding="utf-8"))
    fake_client = _FakeAnthropicClient(recorded_payload)

    sample_text = SAMPLE_PO_PATH.read_text(encoding="utf-8")
    content = build_text_content(sample_text)
    result = extract_document(fake_client, content)

    _assert_matches_section_8_3(result)


@pytest.mark.live_api
def test_live_extraction_matches_section_8_3():
    """
    Makes one real call to the pinned extraction model. Excluded from the
    default test run (see apps/api/pyproject.toml addopts); run explicitly
    with `pytest -m live_api` before shipping any prompt/schema/model-ID
    change and at every phase checkpoint (CLAUDE.md Section 5, Section 7.1).
    """
    import anthropic
    from docflow_core.config import get_settings

    settings = get_settings()
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY not set -- cannot make a live call.")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    sample_text = SAMPLE_PO_PATH.read_text(encoding="utf-8")
    content = build_text_content(sample_text)
    result = extract_document(client, content)

    _assert_matches_section_8_3(result)
