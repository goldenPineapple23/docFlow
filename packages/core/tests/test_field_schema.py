"""
The per-tenant field schema's rules, without a database (D-120).

What must hold:
  * the built-in defaults are exactly the validation constants every tenant
    had before per-tenant schemas existed;
  * only what differs from a default is stored, so a field DocFlow adds later
    starts at its own default for every tenant;
  * a locked field stays required however it is stored;
  * overall confidence is the minimum over the tenant's REQUIRED fields --
    the Section 7.1 rule that an empty optional field used to break.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from docflow_core.field_schema import (
    DEFAULT_SCHEMA,
    FieldSchema,
    FieldSchemaError,
    _resolve,
)
from docflow_core.validation import REQUIRED_HEADER_FIELDS, REQUIRED_LINE_FIELDS


def test_the_defaults_are_the_rules_every_tenant_already_had():
    assert set(DEFAULT_SCHEMA.required_header_fields) == set(REQUIRED_HEADER_FIELDS)
    assert set(DEFAULT_SCHEMA.required_line_fields) == set(REQUIRED_LINE_FIELDS)
    assert DEFAULT_SCHEMA.hidden_header_fields == ()
    assert DEFAULT_SCHEMA.version == 0
    rules = DEFAULT_SCHEMA.rules()
    assert rules.required_header == DEFAULT_SCHEMA.required_header_fields
    assert rules.hidden_line == ()


def test_only_what_differs_from_the_default_is_stored():
    schema = _resolve(3, {"header": {"payment_terms": "hidden", "order_date": "required"}})
    assert schema.overrides() == {"header": {"payment_terms": "hidden", "order_date": "required"}}
    # Unmentioned fields keep their defaults.
    assert schema.state("header", "notes") == "optional"
    assert schema.state("header", "currency") == "required"
    assert DEFAULT_SCHEMA.overrides() == {}


def test_a_locked_field_stays_required_however_it_was_stored():
    schema = _resolve(4, {"header": {"po_number": "hidden"}, "line": {"quantity": "optional"}})
    assert schema.state("header", "po_number") == "required"
    assert schema.state("line", "quantity") == "required"


def test_saving_a_locked_field_as_optional_is_refused():
    from docflow_core import field_schema

    with pytest.raises(FieldSchemaError) as caught:
        field_schema.save(
            session=None,  # never reached: the check runs before any SQL
            tenant_id=None,
            states={"header": {"po_number": "optional"}},
            actor_user_id=None,
        )
    assert caught.value.code == "FLD-002"


@pytest.mark.parametrize("states", [{"header": {"nonsense": "required"}}, {"header": {"notes": "maybe"}}])
def test_saving_an_unknown_field_or_state_is_refused(states):
    from docflow_core import field_schema

    with pytest.raises(FieldSchemaError) as caught:
        field_schema.save(session=None, tenant_id=None, states=states, actor_user_id=None)
    assert caught.value.code == "FLD-001"


def test_overall_confidence_is_the_minimum_of_required_fields_only():
    """The founder's test batch showed 0% because an optional field the
    document didn't have scored zero (D-115's open item, fixed here)."""
    confidence = {
        "po_number": 0.98,
        "buyer_name": 0.95,
        "order_total": 0.91,
        "currency": 0.88,
        "payment_terms": 0.0,  # not on the document at all
        "notes": 0.0,
    }
    assert DEFAULT_SCHEMA.overall_confidence(confidence) == Decimal("0.88")

    # Make one of those required and it counts again.
    stricter = _resolve(2, {"header": {"payment_terms": "required"}})
    assert stricter.overall_confidence(confidence) == Decimal("0")

    # Hiding a required field takes it out of the score.
    looser = _resolve(3, {"header": {"currency": "hidden"}})
    assert looser.overall_confidence(confidence) == Decimal("0.91")


def test_an_order_with_no_scored_required_field_is_zero_not_an_error():
    assert DEFAULT_SCHEMA.overall_confidence({}) == Decimal("0")
    assert DEFAULT_SCHEMA.overall_confidence({"notes": 0.9}) == Decimal("0")


def test_the_screen_payload_lists_every_field_with_its_state():
    schema = FieldSchema(version=7, header=dict(DEFAULT_SCHEMA.header), line=dict(DEFAULT_SCHEMA.line))
    payload = schema.as_dict()
    assert payload["version"] == 7
    po = next(f for f in payload["fields"] if f["name"] == "po_number")
    assert (po["level"], po["state"], po["locked"]) == ("header", "required", True)
    assert {f["level"] for f in payload["fields"]} == {"header", "line"}
