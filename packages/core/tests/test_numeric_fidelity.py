"""
C1, M2, M3 as pure functions: how a number is read from the model's answer,
written back out, and bounded (DECISIONS.md D-149, D-154).

The database half of C1 -- that nothing between the model and the export
rounds -- is `apps/worker/tests/test_numeric_fidelity_db.py`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from docflow_core import extraction
from docflow_core.numbers import decimal_places, parse_document_number, plain
from docflow_core.review import _as_text


def _payload(**line_overrides):
    line = {
        "line_number": 1,
        "sku": "WT-001",
        "description": "Test widget",
        "quantity": "12",
        "unit": "EA",
        "unit_price": "47.50",
        "line_total": "570.00",
        "confidence": 0.9,
    }
    line.update(line_overrides)
    header = {
        name: None
        for name in (
            "po_number", "order_date", "requested_delivery_date", "buyer_name",
            "buyer_contact_email", "ship_to_address", "payment_terms", "order_total",
            "currency", "notes",
        )
    }
    return {
        "header": header,
        "header_confidence": {name: 0.9 for name in header},
        "line_items": [line],
        "document_notes": "",
        "injection_suspected": False,
        "currency_inferred": False,
    }


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (Decimal("0.0000001"), "0.0000001"),
        (Decimal("1E-7"), "0.0000001"),
        (Decimal("1E+3"), "1000"),
        (Decimal("47.50"), "47.50"),
        (Decimal("2"), "2"),
        (Decimal("123456789012345678901234.0000000001"), "123456789012345678901234.0000000001"),
    ],
)
def test_C1_plain_never_writes_an_exponent_and_keeps_the_scale(value, text):
    assert plain(value) == text


def test_C1_review_text_of_a_tiny_number_has_no_exponent():
    # What Postgres hands back for 0.0000001 is Decimal('1E-7'); the snapshot,
    # the audit trail and every export are written from this.
    assert _as_text(Decimal("0.0000001")) == "0.0000001"


@pytest.mark.parametrize(
    "text", ["1,356.00", "47.50 USD", "$47.50", "NaN", "Infinity", "1E+3", " 12", "-5.00", "", "."]
)
def test_M2_a_non_conforming_number_is_not_parsed(text):
    assert parse_document_number(text) is None


@pytest.mark.parametrize("text", ["0", "12", "47.50", "0.00345", "123456789012345678901234.5"])
def test_M2_a_conforming_number_parses_exactly(text):
    assert plain(parse_document_number(text)) == text


def test_M2_extraction_drops_a_non_conforming_number_rather_than_guess():
    parsed = extraction._parse_response_payload(_payload(line_total="1,356.00", quantity="NaN"))
    line = parsed["lines"][0]
    assert line["line_total"] is None
    assert line["quantity"] is None
    assert line["unit_price"] == Decimal("47.50")


def test_C1_extraction_keeps_every_printed_digit():
    parsed = extraction._parse_response_payload(_payload(unit_price="0.00345", line_total="0.0414"))
    assert plain(parsed["lines"][0]["unit_price"]) == "0.00345"
    assert plain(parsed["lines"][0]["line_total"]) == "0.0414"


@pytest.mark.parametrize("bad", [95, -0.2, 1.5, "0.9", None, float("nan")])
def test_M3_an_out_of_range_confidence_is_treated_as_no_confidence(bad):
    payload = _payload(confidence=bad)
    payload["header_confidence"]["po_number"] = bad
    parsed = extraction._parse_response_payload(payload)
    assert parsed["lines"][0]["confidence"] == 0
    assert parsed["header_confidence"]["po_number"] == 0


def test_M3_an_in_range_confidence_is_untouched():
    parsed = extraction._parse_response_payload(_payload(confidence=0.73))
    assert parsed["lines"][0]["confidence"] == 0.73


@pytest.mark.parametrize(("text", "places"), [("47.50", 2), ("2", 0), ("0.0000001", 7), ("1000", 0)])
def test_decimal_places_counts_what_was_printed(text, places):
    assert decimal_places(Decimal(text)) == places


# ── Validation: warn, never round (C1, M2) ──────────────────────────────────


def _snapshot(**kwargs):
    from datetime import date
    from uuid import uuid4

    from docflow_core.validation import DocumentSnapshot, LineSnapshot

    line = LineSnapshot(
        line_id=uuid4(),
        line_number=1,
        sku="WT-001",
        quantity=kwargs.pop("quantity", Decimal("1")),
        unit_price=kwargs.pop("unit_price", Decimal("10.00")),
        line_total=kwargs.pop("line_total", Decimal("10.00")),
        confidence=Decimal("0.99"),
    )
    return DocumentSnapshot(
        document_id=uuid4(),
        received_on=date(2026, 3, 14),
        header={"po_number": "WT-1", "order_total": kwargs.pop("order_total", Decimal("10.00")),
                "order_date": "2026-03-14", "currency": "USD"},
        lines=[line],
        **kwargs,
    )


def test_C1_more_than_six_decimal_places_warns_and_changes_nothing():
    from docflow_core.validation import evaluate_document

    price = Decimal("0.1234567")
    snapshot = _snapshot(unit_price=price, line_total=Decimal("0.1234567"))
    codes = [(w.code, w.field_name) for w in evaluate_document(snapshot)]
    assert ("VAL-015", "unit_price") in codes
    assert ("VAL-015", "line_total") in codes
    assert snapshot.lines[0].unit_price == price  # nothing was touched


def test_C1_six_decimal_places_is_not_unusual():
    from docflow_core.validation import evaluate_document

    snapshot = _snapshot(unit_price=Decimal("0.123456"), line_total=Decimal("0.123456"))
    assert "VAL-015" not in [w.code for w in evaluate_document(snapshot)]


def test_M2_an_unreadable_printed_number_warns_with_what_was_printed():
    from docflow_core.validation import evaluate_document

    snapshot = _snapshot(line_total=None, unreadable_numbers=(("line_total", 1, "1,356.00"),))
    found = [w for w in evaluate_document(snapshot) if w.code == "VAL-014"]
    assert len(found) == 1
    assert found[0].detail["printed"] == "1,356.00"
    assert found[0].field_name == "line_total"


def test_C1_line_total_check_is_exact_for_long_numbers():
    """Python's default 28-digit precision rounds both sides of this to the
    same value and computes a delta of -0.01 -- inside the tolerance, so a
    real 0.87 discrepancy went unreported."""
    from docflow_core.validation import check_line_total

    result = check_line_total(
        Decimal("1"),
        Decimal("12345678901234567890123456789.12"),
        Decimal("12345678901234567890123456789.99"),
    )
    assert result is not None
    assert result[1] == Decimal("0.87")


def test_C1_header_total_check_is_exact_for_long_numbers():
    from docflow_core.validation import check_header_total

    result = check_header_total(
        Decimal("12345678901234567890123456789.99"),
        [Decimal("12345678901234567890123456789.12")],
    )
    assert result is not None
    assert result[1] == Decimal("0.87")


def test_C1_review_rejects_a_typed_number_that_is_not_one():
    from uuid import uuid4

    from docflow_core.review import EditRequest, ReviewError, validate_editable

    with pytest.raises(ReviewError) as caught:
        validate_editable(EditRequest(lines={uuid4(): {"quantity": "4,750"}}))
    assert caught.value.code == "REV-007"
    with pytest.raises(ReviewError):
        validate_editable(EditRequest(header={"order_total": "$47.50"}))
    # A plain number, and an emptied field, are both fine.
    validate_editable(EditRequest(header={"order_total": "0.00345"}))
    validate_editable(EditRequest(lines={uuid4(): {"unit_price": ""}}))


# ── Exports: exact digits, and a warning where QuickBooks may not keep them ──


def _approved(order_total, **line):
    base = {"line_number": "1", "catalog_sku": None, "sku": "WT-001", "description": "Widget",
            "quantity": "2", "unit": "EA", "unit_price": "0.25", "line_total": "0.50"}
    base.update(line)
    header = {"po_number": "WT-1", "order_date": "2026-03-14", "requested_delivery_date": None,
              "buyer_name": "Acme Test Distributor", "buyer_contact_email": None,
              "ship_to_address": None, "payment_terms": None, "currency": "USD",
              "order_total": order_total, "notes": None}
    return {"document_id": "00000000-0000-0000-0000-000000000001", "header": header, "lines": [base]}


def test_C1_iif_warns_when_a_number_carries_more_places_than_quickbooks_keeps():
    from docflow_core.exports import format_warnings

    snapshot = _approved("0.00345", quantity="2.123456", unit_price="0.001725", line_total="0.00345")
    found = {(w["field"], w["line_number"]) for w in format_warnings(snapshot, "iif")}
    assert found == {("order_total", None), ("line_total", 1), ("quantity", 1), ("unit_price", 1)}
    assert {w["code"] for w in format_warnings(snapshot, "iif")} == {"EXP-008"}


def test_C1_iif_within_quickbooks_limits_has_no_warning_and_other_formats_never_warn():
    from docflow_core.exports import format_warnings

    assert format_warnings(_approved("0.50", unit_price="0.25"), "iif") == []
    busy = _approved("0.00345", unit_price="0.0000001")
    for fmt in ("csv", "xlsx", "json"):
        assert format_warnings(busy, fmt) == []


@pytest.mark.parametrize("fmt", ["csv", "xlsx", "json", "iif"])
def test_C1_every_format_writes_the_exact_digits_even_when_it_warns(fmt):
    from docflow_core.exports import PARSERS, build_export

    snapshot = _approved("0.00345", quantity="3", unit_price="0.00115", line_total="0.00345")
    built = build_export(snapshot, "hash", fmt)
    parsed = PARSERS[fmt](built.content)
    assert parsed["header"]["order_total"] == "0.00345"
    assert parsed["lines"][0]["unit_price"] == "0.00115"
