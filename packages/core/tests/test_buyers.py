"""
Database-free unit tests for buyer-name normalization and near-duplicate
scoring (CLAUDE.md Section 7.6). The identification/auto-creation behavior
that needs real rows and real RLS is tested in apps/api/tests/test_buyers.py.

The two assertions that matter most here are opposites of each other:
a spelling or punctuation variant of one company MUST be flagged for a
human, and two genuinely different companies MUST NOT be -- because a
false near-duplicate is what would eventually tempt someone into a wrong
merge, and Section 7.6's whole posture is "suggest, flag, let the human
decide."
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from docflow_core.buyers import (
    NEAR_DUPLICATE_THRESHOLD,
    buyer_similarity_key,
    find_near_duplicate_candidates,
    is_near_duplicate,
    name_similarity,
    normalize_buyer_name,
)

# ── normalization: the match key ───────────────────────────────────────────


def test_normalize_lowercases_and_strips_punctuation():
    assert normalize_buyer_name("Bella's Coffee House, LLC.") == "bellas coffee house llc"


def test_normalize_collapses_whitespace_and_trims():
    assert normalize_buyer_name("  Acme   Test\tDistributor \n") == "acme test distributor"


def test_normalize_is_case_insensitive():
    assert normalize_buyer_name("ACME TEST DISTRIBUTOR") == normalize_buyer_name("Acme Test Distributor")


def test_normalize_treats_missing_and_blank_names_as_no_buyer():
    assert normalize_buyer_name(None) == ""
    assert normalize_buyer_name("   ") == ""


def test_match_key_keeps_legal_suffixes_so_they_are_never_silently_merged():
    """
    Section 10: "never auto-merge buyers". Two entities differing only by
    suffix must land on two different match keys, so they become two rows
    and a flagged candidate -- not one row.
    """
    assert normalize_buyer_name("Acme Test Inc") != normalize_buyer_name("Acme Test LLC")


# ── the comparison key ─────────────────────────────────────────────────────


def test_similarity_key_drops_trailing_legal_suffixes():
    assert buyer_similarity_key("Acme Test Distributor, Inc.") == "acme test distributor"
    assert buyer_similarity_key("Acme Test Distributor LLC") == "acme test distributor"


def test_similarity_key_of_a_suffix_only_name_is_not_empty():
    """Otherwise every company literally named after its entity type would
    score as identical to every other one."""
    assert buyer_similarity_key("LLC") == "llc"


def test_similarity_key_does_not_strip_a_suffix_word_used_mid_name():
    assert buyer_similarity_key("Co Op Test Grocers") == "co op test grocers"


# ── scoring ────────────────────────────────────────────────────────────────


def test_similarity_is_a_decimal_never_a_float():
    score = name_similarity("Acme Test Distributor", "Acme Test Distributors")
    assert isinstance(score, Decimal)
    assert Decimal("0") <= score <= Decimal("1")


def test_suffix_only_difference_is_flagged():
    assert is_near_duplicate("Bella's Coffee House", "Bella's Coffee House, LLC")


def test_spelling_variant_is_flagged():
    assert is_near_duplicate("Bella's Coffee House", "Bellas Coffee House Inc")
    assert is_near_duplicate("Acme Test Distributor", "Acme Test Distributors")


def test_word_order_difference_is_flagged():
    assert is_near_duplicate("Bella's Coffee House", "Coffee House Bella's")


def test_two_genuinely_different_companies_are_not_flagged():
    """
    The load-bearing negative case. Both pairs below share real words with
    each other -- a looser scorer (token_set_ratio, or a lower threshold)
    would flag them, and a founder shown enough false pairs eventually
    merges a wrong one.
    """
    assert not is_near_duplicate("Acme Test Distributor", "Acme Test Manufacturing")
    assert not is_near_duplicate("Northwind Test Supply Co", "Northstar Test Supply Co")
    assert not is_near_duplicate("Bella's Coffee House", "Bella's Tea House")
    assert not is_near_duplicate("Bella's Coffee House", "Riverbend Test Hardware")


def test_missing_name_never_scores_as_similar():
    assert name_similarity(None, "Acme Test Distributor") == Decimal("0.0000")
    assert name_similarity("", "") == Decimal("0.0000")


# ── candidate selection ────────────────────────────────────────────────────


def test_candidates_are_only_those_at_or_above_threshold_highest_first():
    close_id, closer_id, unrelated_id = uuid4(), uuid4(), uuid4()
    existing = [
        # A typo'd variant: flagged, but a little further away than the
        # suffix-only variant below.
        (close_id, "Bellas Coffee Housse Inc"),
        (unrelated_id, "Riverbend Test Hardware"),
        (closer_id, "Bella's Coffee House LLC"),
    ]
    candidates = find_near_duplicate_candidates("Bella's Coffee House", existing)

    assert [c.existing_buyer_id for c in candidates] == [closer_id, close_id]
    assert all(c.similarity_score >= NEAR_DUPLICATE_THRESHOLD for c in candidates)
    assert unrelated_id not in {c.existing_buyer_id for c in candidates}


def test_no_existing_buyers_means_no_candidates():
    assert find_near_duplicate_candidates("Acme Test Distributor", []) == []
