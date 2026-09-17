"""
The Section 7.8 classification decision, tested without a database.

`relationship_to` is the whole judgment call -- duplicate, change order, or
nothing -- so it is pinned here on its own, and the database behavior (the
stored relationship, the untouched earlier document, tenant isolation) is in
apps/api/tests/test_duplicates.py against the real staging database.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import uuid4

from docflow_core.duplicates import (
    RELATION_CHANGE_ORDER,
    RELATION_DUPLICATE,
    PO_NUMBER_KEY_SQL,
    normalize_po_number,
    relationship_to,
)


def test_identical_content_is_a_duplicate():
    assert (
        relationship_to(
            same_content=True, same_po_number=True, buyer_id=uuid4(), other_buyer_id=uuid4()
        )
        == RELATION_DUPLICATE
    )


def test_identical_content_is_never_also_a_change_order():
    """The same bytes carry the same PO number by definition. "This is a copy
    of that exact document" is the stronger, more useful statement."""
    buyer = uuid4()
    assert (
        relationship_to(
            same_content=True, same_po_number=True, buyer_id=buyer, other_buyer_id=buyer
        )
        == RELATION_DUPLICATE
    )


def test_the_same_po_number_with_different_content_from_the_same_buyer_is_a_change_order():
    """**Phase 2 exit criterion (pure half):** the same PO number twice is
    flagged."""
    buyer = uuid4()
    assert (
        relationship_to(
            same_content=False, same_po_number=True, buyer_id=buyer, other_buyer_id=buyer
        )
        == RELATION_CHANGE_ORDER
    )


def test_the_same_po_number_from_two_known_different_buyers_is_not_a_relationship():
    """"PO-1001" is the thousand-and-first order a business has ever placed.
    Two of a distributor's buyers reaching that number independently is
    ordinary, not suspicious."""
    assert (
        relationship_to(
            same_content=False, same_po_number=True, buyer_id=uuid4(), other_buyer_id=uuid4()
        )
        is None
    )


def test_an_unknown_buyer_on_either_side_still_flags():
    """
    DECISIONS.md D-078: this errs toward surfacing, the opposite direction
    from D-055's conservative buyer-merge threshold, because nothing here can
    be auto-applied. A false flag costs one glance; a missed revision ships
    the wrong order.
    """
    known = uuid4()
    assert (
        relationship_to(same_content=False, same_po_number=True, buyer_id=None, other_buyer_id=known)
        == RELATION_CHANGE_ORDER
    )
    assert (
        relationship_to(same_content=False, same_po_number=True, buyer_id=known, other_buyer_id=None)
        == RELATION_CHANGE_ORDER
    )
    assert (
        relationship_to(same_content=False, same_po_number=True, buyer_id=None, other_buyer_id=None)
        == RELATION_CHANGE_ORDER
    )


def test_different_content_and_a_different_po_number_is_nothing():
    buyer = uuid4()
    assert (
        relationship_to(
            same_content=False, same_po_number=False, buyer_id=buyer, other_buyer_id=buyer
        )
        is None
    )


# ── the PO-number comparison key ────────────────────────────────────────────


def test_the_po_key_ignores_only_what_carries_no_meaning():
    assert normalize_po_number("PO-1001") == "PO-1001"
    assert normalize_po_number("po-1001") == "PO-1001"
    assert normalize_po_number("  PO-1001  ") == "PO-1001"
    assert normalize_po_number("PO  1001") == "PO 1001"
    assert normalize_po_number("PO\t1001\n") == "PO 1001"


def test_the_po_key_keeps_punctuation_because_it_can_be_the_difference():
    """"PO-1001" and "PO1001" are two strings a buyer's system can issue
    independently. Collapsing them would invent a relationship."""
    assert normalize_po_number("PO-1001") != normalize_po_number("PO1001")


def test_a_missing_po_number_has_no_key():
    assert normalize_po_number(None) == ""
    assert normalize_po_number("") == ""
    assert normalize_po_number("   ") == ""


def test_the_sql_key_expression_is_defined_once_and_takes_a_column():
    """
    The SQL half of `normalize_po_number` lives in one constant so the query
    and the expression index in 0006 cannot drift apart. The equivalence of
    the two halves is asserted against the real database in
    apps/api/tests/test_duplicates.py.
    """
    rendered = PO_NUMBER_KEY_SQL.format(column="h.po_number")
    assert rendered == "btrim(regexp_replace(upper(h.po_number), '\\s+', ' ', 'g'))"
