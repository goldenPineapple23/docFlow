"""
CLAUDE.md Section 8.3 / Section 5: "the golden fixture (Section 8.3) checked
in with its recorded model response" is a Phase 0 deliverable. The actual
extraction service (and the recorded-response CI replay + live-run test
against the real model) is Phase 1 work -- see docs/docflow-claude-code-
build-prompt-v2.docx Section 6, Phase 1 exit criteria.

This test only proves the fixture itself is present and internally
consistent, so Phase 1 has something concrete to wire the real pipeline
against. It does not call the Anthropic API and is not the "live" test
required at every checkpoint -- that lands in Phase 1.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_PO_PATH = REPO_ROOT / "docs" / "sample_po.txt"
EXPECTED_OUTPUT_PATH = Path(__file__).parent / "fixtures" / "golden" / "expected_output.json"


def test_sample_po_fixture_exists_and_is_readable():
    assert SAMPLE_PO_PATH.exists(), f"Golden fixture input missing: {SAMPLE_PO_PATH}"
    text = SAMPLE_PO_PATH.read_text(encoding="utf-8")
    assert "BCH-2291" in text
    assert "Bella's Coffee House" in text


def test_expected_output_matches_section_8_3():
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

    # injection_suspected must be false for a clean document (CLAUDE.md Section 8.3).
    assert expected["injection_suspected"] is False

    # D-011: currency is only inferable from "$" symbols in sample_po.txt, never stated
    # as an ISO code, so it must be flagged inferred (CLAUDE.md Section 7.1).
    assert expected["currency_inferred"] is True


def test_expected_output_line_totals_reconcile():
    expected = json.loads(EXPECTED_OUTPUT_PATH.read_text(encoding="utf-8"))
    line_sum = sum(Decimal(li["line_total"]) for li in expected["line_items"])
    assert line_sum == Decimal(expected["header"]["order_total"])
    for li in expected["line_items"]:
        assert Decimal(li["quantity"]) * Decimal(li["unit_price"]) == Decimal(li["line_total"])
