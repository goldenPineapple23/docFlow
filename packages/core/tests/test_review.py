"""
The pure half of human review (CLAUDE.md Section 7.3) -- the field allowlist,
the {field, before, after} diff, the acknowledgement payload, and the
canonical snapshot hashing.

Everything here runs without a database. The database half -- approval,
snapshot freezing, the reopen-on-edit path and tenant isolation -- is in
apps/api/tests/test_review.py, where it can be asserted against real rows and
real RLS.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from docflow_core.errors import CATALOG
from docflow_core.matching import PROVENANCE_HUMAN as MATCHING_PROVENANCE_HUMAN
from docflow_core.review import (
    CODE_FIELD_NOT_EDITABLE,
    EDITABLE_HEADER_FIELDS,
    EDITABLE_LINE_FIELDS,
    PROVENANCE_HUMAN,
    EditRequest,
    FieldChange,
    ReviewError,
    WarningAcknowledgement,
    canonical_snapshot_json,
    diff_fields,
    snapshot_sha256,
    validate_editable,
)

# ── The allowlist is the security boundary ──────────────────────────────────


def test_a_reviewer_can_edit_what_the_order_says():
    request = EditRequest(
        header={"po_number": "BCH-2291", "order_total": "570.00"},
        lines={uuid4(): {"quantity": "12", "unit_price": "47.50"}},
    )
    validate_editable(request)  # does not raise


@pytest.mark.parametrize(
    "forbidden",
    [
        "status",
        "raw_json",
        "approved_json",
        "model_id",
        "content_sha256",
        "header_confidence",
        "tenant_id",
        "id",
    ],
)
def test_a_reviewer_can_never_edit_the_record_of_what_the_machine_did(forbidden):
    """
    The reviewer corrects what the ORDER says. Nothing they do changes what
    DocFlow read, when, or with which model -- otherwise a review screen
    could rewrite history to make a document look like it was always right.
    """
    with pytest.raises(ReviewError) as excinfo:
        validate_editable(EditRequest(header={forbidden: "anything"}))
    assert excinfo.value.code == CODE_FIELD_NOT_EDITABLE


def test_a_forbidden_line_field_is_rejected_with_the_line_identified():
    line_id = uuid4()
    with pytest.raises(ReviewError) as excinfo:
        validate_editable(EditRequest(lines={line_id: {"matched_item_id": str(uuid4())}}))
    assert excinfo.value.code == CODE_FIELD_NOT_EDITABLE
    assert excinfo.value.detail["line_id"] == str(line_id)


def test_the_two_allowlists_do_not_overlap_with_machine_columns():
    machine_columns = {
        "confidence",
        "match_method",
        "match_score",
        "match_candidates",
        "matched_item_id",
        "matched_uom",
        "uom_mismatch",
        "field_provenance",
        "header_confidence",
        "currency_inferred",
        "buyer_id",
    }
    assert not (EDITABLE_HEADER_FIELDS & machine_columns)
    assert not (EDITABLE_LINE_FIELDS & machine_columns)


def test_every_review_error_code_is_in_the_catalog():
    """Section 7.16.5: no user-facing failure exists outside the catalog."""
    from docflow_core import review

    codes = [
        getattr(review, name) for name in dir(review) if name.startswith("CODE_")
    ]
    assert codes
    for code in codes:
        assert code in CATALOG, code
        assert CATALOG[code].action.strip(), code


def test_the_human_edit_provenance_prefix_agrees_with_matching():
    """
    `matching._human_edited` skips any field whose provenance starts with this
    prefix -- that is the mechanism behind Section 10's "never overwrite a
    human correction with a machine value". If the two modules ever disagree
    about the string, re-matching silently starts clobbering human edits.
    """
    assert PROVENANCE_HUMAN == MATCHING_PROVENANCE_HUMAN


# ── The diff is what the audit trail records ────────────────────────────────


def test_a_changed_field_produces_a_before_and_after():
    changes = diff_fields({"po_number": "BCH-2291"}, {"po_number": "BCH-2292"})
    assert changes == [FieldChange(field="po_number", before="BCH-2291", after="BCH-2292")]


def test_retyping_the_same_value_is_not_an_edit():
    """
    7.15.3's zero-edit-approvals KPI is "the single best proxy for extraction
    quality per tenant". If opening a field and retyping the same value
    counted, the KPI would measure typing rather than extraction.
    """
    assert diff_fields({"po_number": "BCH-2291"}, {"po_number": "BCH-2291"}) == []


def test_a_decimal_becomes_a_string_and_never_a_float():
    """Section 7.1: no float ever touches money -- including in the audit
    trail, which exists to prove what a value was."""
    changes = diff_fields({"order_total": Decimal("570.00")}, {"order_total": "570.50"})
    assert len(changes) == 1
    assert changes[0].before == "570.00"
    assert isinstance(changes[0].before, str)
    assert changes[0].after == "570.50"


def test_a_decimals_trailing_zeros_are_preserved():
    """"47.50" and "47.5" are the same number and different strings. The
    audit trail must show what was stored, to the scale it was stored at."""
    changes = diff_fields({"unit_price": Decimal("47.50")}, {"unit_price": "47.51"})
    assert changes[0].before == "47.50"


def test_clearing_a_field_is_an_edit_and_records_the_null():
    changes = diff_fields({"payment_terms": "Net 30"}, {"payment_terms": None})
    assert changes == [FieldChange(field="payment_terms", before="Net 30", after=None)]


def test_filling_in_a_field_the_model_left_null_is_an_edit():
    """Section 7.1 has the model return null rather than guess, so the
    reviewer filling it in is the common case, not the edge case."""
    changes = diff_fields({"payment_terms": None}, {"payment_terms": "Net 30"})
    assert changes == [FieldChange(field="payment_terms", before=None, after="Net 30")]


def test_a_line_change_carries_its_line_number():
    changes = diff_fields({"quantity": Decimal("10")}, {"quantity": "12"}, line_number=3)
    assert changes[0].line_number == 3
    assert changes[0].as_json()["line_number"] == 3


def test_a_header_change_carries_no_line_number_in_its_payload():
    entry = FieldChange(field="po_number", before="A", after="B").as_json()
    assert set(entry) == {"field", "before", "after"}


# ── Acknowledgements copy the text, they do not point at it ─────────────────


def test_an_acknowledgement_stores_the_warning_text_not_a_reference():
    """
    Section 7.3: the acknowledgement is recorded "with the warning text". If
    it stored only an ID, changing the catalog wording next year would
    silently rewrite what the human agreed to.
    """
    warning_id = uuid4()
    ack = WarningAcknowledgement(
        warning_id=warning_id,
        code="VAL-002",
        text="The order total doesn't equal the sum of the line totals.",
        note="Freight isn't itemized on this buyer's POs.",
    )
    payload = ack.as_json()
    assert payload["warning_id"] == str(warning_id)
    assert payload["code"] == "VAL-002"
    assert "doesn't equal the sum" in payload["text"]
    assert payload["note"] == "Freight isn't itemized on this buyer's POs."


# ── Snapshot hashing must be deterministic ──────────────────────────────────


def test_the_same_snapshot_always_hashes_the_same_way():
    """
    Section 7.4: "Exports are deterministic: same snapshot -> byte-identical
    file", and every `exports` row records "the snapshot hash it was built
    from". Neither means anything if the hash depends on dict ordering.
    """
    a = {"header": {"po_number": "BCH-2291", "order_total": "570.00"}, "lines": []}
    b = {"lines": [], "header": {"order_total": "570.00", "po_number": "BCH-2291"}}
    assert snapshot_sha256(a) == snapshot_sha256(b)


def test_a_different_snapshot_hashes_differently():
    a = {"header": {"order_total": "570.00"}}
    b = {"header": {"order_total": "570.01"}}
    assert snapshot_sha256(a) != snapshot_sha256(b)


def test_the_canonical_rendering_carries_no_incidental_whitespace():
    rendered = canonical_snapshot_json({"b": "2", "a": "1"})
    assert rendered == '{"a":"1","b":"2"}'


def test_non_ascii_survives_the_canonical_rendering():
    """A buyer name with an accent must hash as itself, not as an escape."""
    rendered = canonical_snapshot_json({"buyer_name": "Café Test Roasters"})
    assert "Café" in rendered
    assert snapshot_sha256({"buyer_name": "Café Test Roasters"}) == snapshot_sha256(
        {"buyer_name": "Café Test Roasters"}
    )
