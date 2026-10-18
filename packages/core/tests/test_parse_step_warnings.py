"""
The parse step's warnings, as validation shapes and shows them (D-207, PR
1b-ii). No database: the writes are proven in the worker's and the API's
tests against the real one.
"""

from __future__ import annotations

from docflow_core import validation
from docflow_core.errors import CATALOG
from docflow_core.review import acknowledgement_text
from docflow_core.validation import parse_step_warnings, warning_entry

LOST = [
    {"code": "VAL-017", "filename": "orders.zip"},
    {"code": "VAL-018", "filename": None},
    {"code": "VAL-017", "filename": "orders.zip"},
]


def test_each_loss_is_a_high_warning_and_two_same_named_attachments_are_two():
    warnings = parse_step_warnings(LOST)
    assert [w.code for w in warnings] == ["VAL-017", "VAL-018", "VAL-017"]
    assert {w.severity for w in warnings} == {"high"}
    assert warnings[0].detail == {"filename": "orders.zip", "position": "1"}
    assert warnings[1].detail == {"part": "body"}
    assert warnings[2].detail == {"filename": "orders.zip", "position": "2"}
    assert len({w.fingerprint for w in warnings}) == 3


def test_the_same_answer_gives_the_same_fingerprints():
    """So writing them again (a retried save) adds nothing."""
    assert [w.fingerprint for w in parse_step_warnings(LOST)] == [
        w.fingerprint for w in parse_step_warnings(LOST)
    ]


def test_val_017_shows_its_attachments_name():
    entry = warning_entry("VAL-017", {"filename": "Purchase_Order_March.pdf", "position": "1"})
    assert 'the attachment "Purchase_Order_March.pdf".' in entry.message
    assert "{" not in entry.message


def test_val_017_with_no_name_says_attachment_never_the_placeholder():
    assert 'the attachment "attachment".' in warning_entry("VAL-017", None).message


def test_every_other_warning_is_its_catalog_entry_unchanged():
    for code in ("VAL-002", "VAL-016", "VAL-018"):
        assert warning_entry(code, {"filename": "x.zip"}) == CATALOG[code]


def test_no_other_warning_code_has_a_placeholder_to_fill():
    """`warning_entry` fills VAL-017's only. A placeholder in another VAL
    entry would reach the reviewer as written."""
    with_placeholders = {
        code
        for code, entry in CATALOG.items()
        if code.startswith("VAL-") and "{" in entry.message + entry.action
    }
    assert with_placeholders == {"VAL-017"}


def test_the_acknowledgement_records_the_name_as_the_reviewer_read_it():
    text = acknowledgement_text("VAL-017", None, None, {"filename": "orders.zip", "position": "1"})
    assert 'the attachment "orders.zip".' in text and "{filename}" not in text


def test_the_parse_step_codes_are_not_validations_own():
    assert validation.PARSE_STEP_CODES == {"VAL-017", "VAL-018"}
    assert not validation.PARSE_STEP_CODES & set(validation._ALL_CODES)
