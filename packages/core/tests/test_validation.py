"""
The Section 7.7 rules, tested without a database (CLAUDE.md Section 7.7:
"warn, never auto-correct").

Every test here exercises `docflow_core.validation`'s pure layer, so the
thresholds, the null handling and the severity scheme are pinned independently
of Postgres. The database behavior -- warning rows, idempotent re-validation,
tenant isolation, and the assertion that extracted values are byte-identical
before and after -- is in apps/api/tests/test_validation.py against the real
staging database.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from docflow_core.validation import (
    CODE_CURRENCY_INFERRED,
    CODE_HEADER_TOTAL_MISMATCH,
    CODE_IMPLAUSIBLE_DATE,
    CODE_INJECTION_SUSPECTED,
    CODE_INVALID_CURRENCY,
    CODE_LINE_TOTAL_MISMATCH,
    CODE_LOW_CONFIDENCE,
    CODE_MISSING_REQUIRED_FIELD,
    CODE_NON_POSITIVE_QUANTITY,
    CODE_POSSIBLE_CHANGE_ORDER,
    CODE_POSSIBLE_DUPLICATE,
    CODE_UNPARSEABLE_DATE,
    CODE_UOM_MISMATCH,
    CONFIDENCE_THRESHOLD,
    ISO_4217_CODES,
    DocumentSnapshot,
    DocumentWarning,
    LineSnapshot,
    check_currency,
    check_date,
    check_header_total,
    check_line_total,
    check_quantity,
    evaluate_document,
    header_total_tolerance,
    line_total_tolerance,
    money_severity,
    normalize_currency,
    parse_iso_date,
    unit_price_half_step,
)

RECEIVED_ON = date(2025, 6, 1)


def _line(**overrides) -> LineSnapshot:
    values = {
        "line_id": uuid4(),
        "line_number": 1,
        "sku": "CF-1001",
        "description": "Colombian Whole Bean 5lb",
        "quantity": Decimal("10"),
        "unit": "CS",
        "unit_price": Decimal("10.00"),
        "line_total": Decimal("100.00"),
        "confidence": Decimal("0.95"),
    }
    values.update(overrides)
    return LineSnapshot(**values)


def _snapshot(**overrides) -> DocumentSnapshot:
    header = {
        "po_number": "BCH-2291",
        "order_date": "2025-05-28",
        "requested_delivery_date": "2025-06-15",
        "buyer_name": "Bella's Test Coffee House",
        "buyer_contact_email": "orders@bellas.test",
        "ship_to_address": "1 Test Way, Testville",
        "payment_terms": "Net 30",
        "order_total": Decimal("100.00"),
        "currency": "USD",
        "notes": None,
    }
    header.update(overrides.pop("header", {}))
    values = {
        "document_id": uuid4(),
        "received_on": RECEIVED_ON,
        "header": header,
        "header_confidence": {"po_number": 0.98, "order_total": 0.97},
        "lines": [_line()],
    }
    values.update(overrides)
    return DocumentSnapshot(**values)


def _codes(warnings: list[DocumentWarning]) -> list[str]:
    return [w.code for w in warnings]


# ── line_total ~= quantity x unit_price ─────────────────────────────────────


def test_a_line_that_multiplies_out_exactly_is_silent():
    assert check_line_total(Decimal("12"), Decimal("47.50"), Decimal("570.00")) is None


def test_a_line_total_that_is_plainly_wrong_fires():
    result = check_line_total(Decimal("12"), Decimal("47.50"), Decimal("470.00"))
    assert result is not None
    expected, delta = result
    assert expected == Decimal("570.00")
    assert delta == Decimal("-100.00")


def test_line_tolerance_is_the_rounding_room_of_the_printed_numbers():
    """10 x "10.00": one cent for the line total plus 10 x half a cent."""
    assert line_total_tolerance(Decimal("10"), Decimal("10.00")) == Decimal("0.060")
    assert check_line_total(Decimal("10"), Decimal("10.00"), Decimal("100.06")) is None
    assert check_line_total(Decimal("10"), Decimal("10.00"), Decimal("100.07")) is not None


def test_a_price_stored_at_four_places_is_read_as_printed_to_the_cent():
    """unit_price is numeric(14,4): "47.50" comes back as 47.5000."""
    assert unit_price_half_step(Decimal("47.5000")) == Decimal("0.005")
    assert unit_price_half_step(Decimal("50")) == Decimal("0.005")
    assert unit_price_half_step(Decimal("0.1235")) == Decimal("0.00005")


def test_a_large_line_can_no_longer_hide_a_real_error_in_a_percentage():
    """
    D-096: under D-073's 0.5% relative term, a $20,000 line could be $100
    off and stay silent. Now the allowance is 1 cent + 100 x half a cent.
    """
    assert check_line_total(Decimal("100"), Decimal("200.00"), Decimal("20100.00")) is not None
    assert check_line_total(Decimal("100"), Decimal("200.00"), Decimal("20000.51")) is None
    assert check_line_total(Decimal("100"), Decimal("200.00"), Decimal("20000.52")) is not None


def test_a_rounded_unit_price_does_not_fire_the_line_rule():
    """1,000 units of an item whose real price is 0.12345, printed as 0.1235."""
    assert check_line_total(Decimal("1000"), Decimal("0.1235"), Decimal("123.45")) is None


def test_line_rule_is_silent_on_every_null_input():
    assert check_line_total(None, Decimal("10.00"), Decimal("100.00")) is None
    assert check_line_total(Decimal("10"), None, Decimal("100.00")) is None
    assert check_line_total(Decimal("10"), Decimal("10.00"), None) is None
    assert check_line_total(None, None, None) is None


# ── header total ~= sum of line totals ──────────────────────────────────────


def test_a_header_total_that_reconciles_is_silent():
    assert check_header_total(Decimal("300.00"), [Decimal("100.00"), Decimal("200.00")]) is None


def test_a_header_total_that_does_not_reconcile_fires():
    """
    **Phase 2 exit criterion (pure half):** "totals that don't reconcile
    produce a warning and are not silently corrected."
    """
    result = check_header_total(Decimal("4300.00"), [Decimal("100.00"), Decimal("200.00")])
    assert result is not None
    expected_sum, delta = result
    assert expected_sum == Decimal("300.00")
    assert delta == Decimal("4000.00")


def test_header_tolerance_boundary_just_inside_and_just_outside():
    # One line -> one cent, whatever the size of the order.
    assert header_total_tolerance(1) == Decimal("0.01")
    assert check_header_total(Decimal("100.01"), [Decimal("100.00")]) is None
    assert check_header_total(Decimal("100.02"), [Decimal("100.00")]) is not None


def test_a_large_order_total_can_no_longer_hide_a_real_error_in_a_percentage():
    """D-096: under D-073, $100 on a $20,000 order was inside the tolerance."""
    lines = [Decimal("10000.00"), Decimal("10000.00")]
    assert check_header_total(Decimal("20100.00"), lines) is not None
    assert check_header_total(Decimal("20000.03"), lines) is not None
    assert check_header_total(Decimal("20000.02"), lines) is None


def test_a_forty_line_order_whose_lines_each_round_legitimately_stays_silent():
    """
    The failure mode this tolerance exists to avoid: a rule that fires on
    every correct 40-line purchase order teaches reviewers to click past
    warnings, which costs more than the rule ever saves.

    Forty lines of one unit at a true 0.125, each printed rounded to 0.13.
    The buyer's own system computed the header total from the unrounded
    price, so the two differ by 20 cents through nobody's mistake.
    """
    lines = [Decimal("0.13")] * 40
    assert sum(lines) == Decimal("5.20")
    assert check_header_total(Decimal("5.00"), lines) is None
    # Tolerance scales with the line count -- 40 cents, not one.
    assert header_total_tolerance(40) == Decimal("0.40")


def test_a_forty_line_order_with_a_real_error_still_fires():
    lines = [Decimal("0.13")] * 40
    result = check_header_total(Decimal("4005.20"), lines)
    assert result is not None
    _, delta = result
    assert delta == Decimal("4000.00")


def test_header_rule_is_silent_when_it_cannot_be_evaluated():
    # No order total: the required-field rule says that once, not twice.
    assert check_header_total(None, [Decimal("100.00")]) is None
    # No lines at all: nothing to reconcile against.
    assert check_header_total(Decimal("100.00"), []) is None
    # A line with no total: the sum would be built from an incomplete set.
    assert check_header_total(Decimal("300.00"), [Decimal("100.00"), None]) is None


# ── severity ────────────────────────────────────────────────────────────────


def test_a_rounding_sized_discrepancy_is_a_warning_and_a_material_one_is_high():
    assert money_severity(Decimal("0.20"), Decimal("5.20")) == "warning"
    assert money_severity(Decimal("4000.00"), Decimal("300.00")) == "high"
    # Material in absolute terms even on a huge order.
    assert money_severity(Decimal("100.00"), Decimal("500000.00")) == "high"
    # Material in relative terms even on a tiny one.
    assert money_severity(Decimal("1.00"), Decimal("10.00")) == "high"
    assert money_severity(Decimal("-4000.00"), Decimal("300.00")) == "high"


def test_money_severity_never_divides_by_zero():
    assert money_severity(Decimal("0.50"), Decimal("0")) == "warning"


# ── quantity > 0 ────────────────────────────────────────────────────────────


def test_quantity_rule():
    assert check_quantity(Decimal("0")) is True
    assert check_quantity(Decimal("-3")) is True
    assert check_quantity(Decimal("0.5")) is False
    assert check_quantity(Decimal("12")) is False
    # Null is the required-field rule's business, not this one's.
    assert check_quantity(None) is False


# ── dates parse and are plausible ───────────────────────────────────────────


def test_parse_iso_date_never_guesses_at_another_format():
    assert parse_iso_date("2025-05-28") == date(2025, 5, 28)
    assert parse_iso_date("28/05/2025") is None
    assert parse_iso_date("May 28, 2025") is None
    assert parse_iso_date("2025-06-31") is None
    assert parse_iso_date(None) is None
    assert parse_iso_date("   ") is None


def _order_date(value: str | None) -> str | None:
    return check_date(value, RECEIVED_ON, max_past_days=730, max_future_days=30)


def test_a_normal_order_date_is_silent():
    assert _order_date("2025-05-28") is None
    assert _order_date("2024-06-02") is None


def test_a_date_two_hundred_years_off_is_implausible():
    assert _order_date("1825-06-01") == "implausible"
    assert _order_date("2225-06-01") == "implausible"


def test_a_date_just_outside_the_window_is_implausible():
    assert _order_date("2023-06-02") is None  # 730 days back, the boundary
    assert _order_date("2023-06-01") == "implausible"  # 731 days back


def test_an_unreadable_date_is_reported_rather_than_reinterpreted():
    assert _order_date("28/05/2025") == "unparseable"


def test_a_missing_date_is_silence_not_a_wrong_date():
    assert _order_date(None) is None
    assert _order_date("") is None


def test_a_delivery_date_far_in_the_future_is_allowed_because_blanket_orders_exist():
    assert check_date("2027-01-01", RECEIVED_ON, max_past_days=30, max_future_days=1095) is None
    assert (
        check_date("2030-01-01", RECEIVED_ON, max_past_days=30, max_future_days=1095) == "implausible"
    )


# ── currency is a valid ISO code ────────────────────────────────────────────


def test_currency_rule():
    assert check_currency("USD") is False
    assert check_currency("usd") is False
    assert check_currency("  EUR  ") is False
    assert check_currency("US$") is True
    assert check_currency("DOLLARS") is True
    # Valid ISO codes that are not valid answers to "what money is this".
    assert check_currency("XXX") is True
    assert check_currency("XTS") is True
    # Null/blank is the required-field rule's business.
    assert check_currency(None) is False
    assert check_currency("") is False


def test_normalization_is_a_comparison_key_not_a_correction():
    assert normalize_currency("  usd ") == "USD"
    assert "CAD" in ISO_4217_CODES
    # Recently withdrawn codes stay valid: a backdated PO can carry one.
    assert "HRK" in ISO_4217_CODES
    assert "ANG" in ISO_4217_CODES


# ── the whole-document evaluation ───────────────────────────────────────────


def test_a_clean_document_produces_no_warnings_at_all():
    assert evaluate_document(_snapshot()) == []


def test_a_missing_po_number_is_raised_above_the_other_required_fields():
    warnings = evaluate_document(_snapshot(header={"po_number": None}))
    missing = [w for w in warnings if w.code == CODE_MISSING_REQUIRED_FIELD]
    assert [w.field_name for w in missing] == ["po_number"]
    assert missing[0].severity == "high"


def test_a_missing_buyer_name_is_an_ordinary_warning():
    warnings = evaluate_document(_snapshot(header={"buyer_name": "   "}))
    missing = [w for w in warnings if w.code == CODE_MISSING_REQUIRED_FIELD]
    assert [w.field_name for w in missing] == ["buyer_name"]
    assert missing[0].severity == "warning"


def test_a_line_with_neither_sku_nor_description_is_flagged_once():
    snapshot = _snapshot(lines=[_line(sku=None, description=None)])
    warnings = [w for w in evaluate_document(snapshot) if w.code == CODE_MISSING_REQUIRED_FIELD]
    assert [w.field_name for w in warnings] == ["sku_or_description"]


def test_a_line_missing_its_quantity_reports_the_missing_field_not_a_bogus_mismatch():
    snapshot = _snapshot(
        header={"order_total": Decimal("100.00")},
        lines=[_line(quantity=None)],
    )
    codes = _codes(evaluate_document(snapshot))
    assert CODE_MISSING_REQUIRED_FIELD in codes
    assert CODE_LINE_TOTAL_MISMATCH not in codes
    assert CODE_NON_POSITIVE_QUANTITY not in codes


def test_a_line_with_no_unit_price_is_not_a_warning_at_all():
    """Contract-priced lines routinely omit the unit price. Nothing is
    wrong with that document."""
    snapshot = _snapshot(lines=[_line(unit_price=None)])
    assert evaluate_document(snapshot) == []


def test_injection_suspected_becomes_a_high_severity_warning():
    warnings = evaluate_document(_snapshot(injection_suspected=True))
    injection = [w for w in warnings if w.code == CODE_INJECTION_SUSPECTED]
    assert len(injection) == 1
    assert injection[0].severity == "high"


def test_low_confidence_is_flagged_per_field_and_per_line():
    snapshot = _snapshot(
        header_confidence={"po_number": 0.55, "order_total": 0.99},
        lines=[_line(confidence=Decimal("0.42"))],
    )
    low = [w for w in evaluate_document(snapshot) if w.code == CODE_LOW_CONFIDENCE]
    assert len(low) == 2
    assert low[0].field_name == "po_number"
    assert low[1].document_line_id == snapshot.lines[0].line_id
    assert low[0].detail["threshold"] == str(CONFIDENCE_THRESHOLD)


def test_confidence_exactly_at_the_threshold_is_not_flagged():
    snapshot = _snapshot(
        header_confidence={"po_number": 0.80},
        lines=[_line(confidence=Decimal("0.80"))],
    )
    assert [w for w in evaluate_document(snapshot) if w.code == CODE_LOW_CONFIDENCE] == []


def test_a_confidence_map_with_no_numbers_in_it_never_crashes():
    snapshot = _snapshot(header_confidence={"po_number": None, "currency": "n/a"})
    assert [w for w in evaluate_document(snapshot) if w.code == CODE_LOW_CONFIDENCE] == []


def test_an_inferred_currency_gets_the_warning_section_7_1_asks_for():
    warnings = evaluate_document(_snapshot(currency_inferred=True))
    inferred = [w for w in warnings if w.code == CODE_CURRENCY_INFERRED]
    assert len(inferred) == 1
    assert inferred[0].severity == "info"


def test_a_uom_mismatch_from_the_matching_slice_becomes_a_warning():
    """DECISIONS.md D-070 deferred this to here: the boolean becomes a row."""
    snapshot = _snapshot(
        lines=[_line(uom_mismatch=True, unit="EA", matched_item_uom="CASE")]
    )
    warnings = [w for w in evaluate_document(snapshot) if w.code == CODE_UOM_MISMATCH]
    assert len(warnings) == 1
    assert warnings[0].detail["unit"] == "EA"
    assert warnings[0].detail["catalog_unit_of_measure"] == "CASE"


def test_duplicate_and_change_order_flags_become_warnings():
    earlier = uuid4()
    duplicate = evaluate_document(
        _snapshot(is_possible_duplicate=True, duplicate_of_document_id=earlier)
    )
    assert [w.code for w in duplicate if w.code == CODE_POSSIBLE_DUPLICATE] == [
        CODE_POSSIBLE_DUPLICATE
    ]

    revision = evaluate_document(
        _snapshot(is_possible_change_order=True, change_order_of_document_id=earlier)
    )
    change = [w for w in revision if w.code == CODE_POSSIBLE_CHANGE_ORDER]
    assert change[0].detail["change_order_of_document_id"] == str(earlier)
    assert change[0].severity == "high"


def test_a_flag_with_no_pointer_produces_no_warning():
    """A boolean nobody can act on is noise; the reviewer needs the link."""
    assert evaluate_document(_snapshot(is_possible_duplicate=True)) == []


def test_a_bad_currency_and_a_bad_date_are_reported_independently():
    snapshot = _snapshot(header={"currency": "US$", "order_date": "May 28"})
    codes = _codes(evaluate_document(snapshot))
    assert CODE_INVALID_CURRENCY in codes
    assert CODE_UNPARSEABLE_DATE in codes


def test_a_two_hundred_year_old_order_date_is_reported_with_the_received_date():
    snapshot = _snapshot(header={"order_date": "1825-06-01"})
    warnings = [w for w in evaluate_document(snapshot) if w.code == CODE_IMPLAUSIBLE_DATE]
    assert warnings[0].detail["received_on"] == RECEIVED_ON.isoformat()
    assert warnings[0].detail["value"] == "1825-06-01"


def test_every_number_in_a_warning_detail_is_a_string():
    """Section 7.1: numbers are strings in transport, never floats."""
    snapshot = _snapshot(
        header={"order_total": Decimal("4300.00")},
        lines=[_line(line_total=Decimal("470.00"))],
    )
    for warning in evaluate_document(snapshot):
        for key, value in warning.detail.items():
            assert value is None or isinstance(value, str), f"{warning.code}.{key} is {type(value)}"


def test_evaluation_is_deterministic_and_never_mutates_its_input():
    snapshot = _snapshot(
        header={"order_total": Decimal("4300.00"), "currency": "US$"},
        lines=[_line(line_total=Decimal("470.00"), quantity=Decimal("-1"))],
    )
    before_header = dict(snapshot.header)
    before_line = snapshot.lines[0]

    first = evaluate_document(snapshot)
    second = evaluate_document(snapshot)

    assert [w.fingerprint for w in first] == [w.fingerprint for w in second]
    # The snapshot the rules read is exactly as it was handed over. Nothing
    # in this module can "reconcile" a document by changing a number.
    assert snapshot.header == before_header
    assert snapshot.lines[0] == before_line
    assert snapshot.lines[0].line_total == Decimal("470.00")
    assert snapshot.lines[0].quantity == Decimal("-1")


# ── fingerprints ────────────────────────────────────────────────────────────


def test_the_same_warning_fingerprints_the_same_way_twice():
    line_id = uuid4()
    left = DocumentWarning(
        code=CODE_LINE_TOTAL_MISMATCH,
        severity="warning",
        field_name="line_total",
        document_line_id=line_id,
        detail={"difference": "-100.00"},
    )
    right = DocumentWarning(
        code=CODE_LINE_TOTAL_MISMATCH,
        severity="warning",
        field_name="line_total",
        document_line_id=line_id,
        detail={"difference": "-100.00"},
    )
    assert left.fingerprint == right.fingerprint


def test_changing_the_numbers_changes_the_fingerprint():
    """
    An acknowledgement of "this total is out by two cents" must never carry
    silently over to "this total is out by four thousand dollars".
    """
    line_id = uuid4()
    small = DocumentWarning(
        code=CODE_HEADER_TOTAL_MISMATCH,
        severity="warning",
        field_name="order_total",
        document_line_id=line_id,
        detail={"difference": "0.02"},
    )
    large = DocumentWarning(
        code=CODE_HEADER_TOTAL_MISMATCH,
        severity="high",
        field_name="order_total",
        document_line_id=line_id,
        detail={"difference": "4000.00"},
    )
    assert small.fingerprint != large.fingerprint


def test_the_same_code_on_two_lines_fingerprints_differently():
    detail = {"difference": "-100.00"}
    first = DocumentWarning(
        code=CODE_LINE_TOTAL_MISMATCH, severity="warning", document_line_id=uuid4(), detail=detail
    )
    second = DocumentWarning(
        code=CODE_LINE_TOTAL_MISMATCH, severity="warning", document_line_id=uuid4(), detail=detail
    )
    assert first.fingerprint != second.fingerprint


def test_severity_is_not_part_of_the_fingerprint():
    """Severity is derived from the same numbers the fingerprint already
    covers, so including it would be redundant -- and a future change to the
    severity scheme must not orphan every acknowledgement in the database."""
    detail = {"difference": "-100.00"}
    warning = DocumentWarning(code=CODE_LINE_TOTAL_MISMATCH, severity="warning", detail=detail)
    escalated = DocumentWarning(code=CODE_LINE_TOTAL_MISMATCH, severity="high", detail=detail)
    assert warning.fingerprint == escalated.fingerprint
