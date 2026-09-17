"""
Database-free unit tests for catalog-matching normalization, scoring and the
Section 7.6 precedence rules. The behavior that needs real rows and real RLS
is in apps/api/tests/test_matching.py.

The load-bearing assertions here are the negative ones. A purchase-order line
that matches the *wrong* catalog item produces a wrong export that a customer
acts on, which CLAUDE.md's opening paragraph calls worse than a crash. So
these tests care much more that "Colombian Whole Bean 5lb" never resolves to
"Colombian Whole Bean 2lb" than that every legitimate variant resolves --
an unmatched line is a question for a reviewer, a wrongly matched line is a
wrong shipment.

All catalog data below is fictional, built from docs/sample_po.txt's
invented SKUs (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from docflow_core.matching import (
    EXACT_MATCH_SCORE,
    FUZZY_MATCH_THRESHOLD,
    LEARNED_RULE_MATCH_SCORE,
    MAX_CANDIDATES,
    PROVENANCE_EXACT,
    PROVENANCE_FUZZY,
    Catalog,
    CatalogItem,
    SkuRule,
    UomRule,
    description_similarity,
    measure_signature,
    measures_agree,
    normalize_description,
    normalize_sku,
    resolve_line,
    resolve_uom,
    sku_comparison_key,
    sku_similarity,
    uom_mismatch,
)

# ── A small fictional catalog, straight from docs/sample_po.txt ─────────────

CF_1001 = uuid4()
CF_2210 = uuid4()
SY_0045 = uuid4()
CUP_12 = uuid4()


def _catalog(*items: CatalogItem) -> Catalog:
    return Catalog(list(items))


def _sample_catalog() -> Catalog:
    return _catalog(
        CatalogItem(CF_1001, "CF-1001", "Colombian Whole Bean 5lb", "CS"),
        CatalogItem(CF_2210, "CF-2210", "Ethiopian Yirgacheffe 5lb", "CS"),
        CatalogItem(SY_0045, "SY-0045", "Vanilla Syrup 750ml", "EA"),
        CatalogItem(CUP_12, "CUP-12", "12oz Paper Cups (1000ct)", "BOX"),
    )


# ── normalization ───────────────────────────────────────────────────────────


def test_description_key_is_case_punctuation_and_spacing_insensitive():
    assert normalize_description("Colombian Whole Bean, 5lb.") == "colombian whole bean 5lb"
    assert normalize_description("COLOMBIAN  WHOLE\tBEAN 5LB") == "colombian whole bean 5lb"


def test_description_key_joins_a_measure_written_with_a_space():
    """"5 lb" and "5lb" are the same measure; a buyer's typesetting is not a
    different product."""
    assert normalize_description("Colombian Whole Bean 5 lb") == "colombian whole bean 5lb"
    assert normalize_description("12 oz Paper Cups (1000 ct)") == "12oz paper cups 1000ct"


def test_description_key_does_not_glue_a_number_onto_a_real_word():
    assert normalize_description("24 Vanilla Syrup") == "24 vanilla syrup"


def test_description_key_keeps_a_decimal_measure_intact():
    assert normalize_description("Cold Brew 1.5 L") == "cold brew 1.5l"


def test_description_key_of_nothing_is_empty():
    assert normalize_description(None) == ""
    assert normalize_description("   ") == ""


def test_sku_exact_key_strips_padding_and_invisible_characters_only():
    """Section 7.15.2 Step 4: catalogs carry these. They cannot be allowed to
    defeat an exact match, and stripping them is not a rewrite of what the
    document said."""
    assert normalize_sku("  cf-1001 ") == "CF-1001"
    assert normalize_sku("CF-1001​") == "CF-1001"
    assert normalize_sku("CF  1001") == "CF 1001"


def test_sku_exact_key_keeps_punctuation_so_two_real_skus_stay_distinct():
    """"CF-1001" and "CF1001" can be two different products in a real
    catalog. Collapsing them at the identity layer would invent a match."""
    assert normalize_sku("CF-1001") != normalize_sku("CF1001")


def test_sku_comparison_key_drops_punctuation_for_scoring_only():
    assert sku_comparison_key("CF-1001") == sku_comparison_key("CF1001") == "cf1001"


# ── the measure guard ───────────────────────────────────────────────────────


def test_measure_signature_reads_every_number_and_unit():
    assert measure_signature("12oz Paper Cups (1000ct)") == (("1000", "ct"), ("12", "oz"))
    assert measure_signature("Paper Cups 1000 ct 12 oz") == (("1000", "ct"), ("12", "oz"))


def test_measures_agree_across_spacing_and_case():
    assert measures_agree("Colombian Whole Bean 5lb", "COLOMBIAN WHOLE BEAN 5 LB")


def test_measures_disagree_on_a_different_pack_size():
    assert not measures_agree("Colombian Whole Bean 5lb", "Colombian Whole Bean 2lb")
    assert not measures_agree("Vanilla Syrup 750ml", "Vanilla Syrup 1L")
    assert not measures_agree("12oz Paper Cups (1000ct)", "16oz Paper Cups (1000ct)")


# ── scoring ─────────────────────────────────────────────────────────────────


def test_scores_are_decimals_never_floats():
    score = description_similarity("Colombian Whole Bean 5lb", "Columbian Whole Bean 5lb")
    assert isinstance(score, Decimal)
    assert Decimal("0") <= score <= Decimal("1")
    assert isinstance(sku_similarity("CF-1001", "CF-1002"), Decimal)


def test_word_order_is_noise_in_a_description():
    assert description_similarity("Syrup, Vanilla 750ml", "Vanilla Syrup 750ml") == Decimal("1.0000")


def test_a_qualifier_that_makes_it_a_different_sku_scores_below_threshold():
    """
    The case that rules out `token_set_ratio` outright: it scores this pair
    1.00 because one description contains the other. "Organic" is a different
    SKU and a different shipment.
    """
    score = description_similarity("Colombian Whole Bean 5lb", "Colombian Whole Bean 5lb Organic")
    assert score < FUZZY_MATCH_THRESHOLD


def test_missing_text_never_scores_as_similar():
    assert description_similarity(None, "Colombian Whole Bean 5lb") == Decimal("0.0000")
    assert sku_similarity("CF-1001", None) == Decimal("0.0000")


# ── resolve_line: precedence ────────────────────────────────────────────────


def test_learned_rule_beats_an_exact_sku_match():
    """
    Section 7.6 orders learned mappings first. A human told us what this
    buyer's wording means; that outranks the SKU printed next to it, which is
    frequently the buyer's own internal part number.
    """
    catalog = _sample_catalog()
    rule = SkuRule(rule_id=uuid4(), buyer_scoped=True, item_id=CF_2210)
    result = resolve_line(
        raw_sku="CF-1001",
        raw_description="House Blend Beans",
        catalog=catalog,
        sku_rules={normalize_description("House Blend Beans"): rule},
    )
    assert result.item_id == CF_2210
    assert result.method == "learned_rule"
    assert result.score == LEARNED_RULE_MATCH_SCORE
    assert result.provenance == f"learned_rule:{rule.rule_id}"
    assert result.learned_rule_id == rule.rule_id


def test_exact_sku_beats_a_fuzzy_description_match():
    catalog = _sample_catalog()
    result = resolve_line(
        raw_sku="CF-2210",
        # Scores 1.00 against CF-1001's description -- and loses anyway.
        raw_description="Colombian Whole Bean 5 lb",
        catalog=catalog,
        sku_rules={},
    )
    assert result.item_id == CF_2210
    assert result.method == "exact_sku"
    assert result.score == EXACT_MATCH_SCORE
    assert result.provenance == PROVENANCE_EXACT


def test_exact_sku_match_ignores_padding_and_case():
    result = resolve_line(
        raw_sku=" cf-1001 ", raw_description=None, catalog=_sample_catalog(), sku_rules={}
    )
    assert result.item_id == CF_1001
    assert result.method == "exact_sku"


def test_a_rule_pointing_at_a_retired_item_is_not_applied():
    """A retired SKU is not in the live catalog, so the rule cannot fire. The
    line falls through and is surfaced normally rather than resolving to an
    item that no longer exists."""
    catalog = _sample_catalog()
    rule = SkuRule(rule_id=uuid4(), buyer_scoped=True, item_id=uuid4())
    result = resolve_line(
        raw_sku="CF-1001",
        raw_description="House Blend Beans",
        catalog=catalog,
        sku_rules={normalize_description("House Blend Beans"): rule},
    )
    assert result.method == "exact_sku"
    assert result.item_id == CF_1001


# ── resolve_line: fuzzy, above and below threshold ──────────────────────────


def test_above_threshold_fuzzy_applies_and_records_its_score():
    result = resolve_line(
        raw_sku=None,
        raw_description="Syrup, Vanilla 750 ml",
        catalog=_sample_catalog(),
        sku_rules={},
    )
    assert result.item_id == SY_0045
    assert result.method == "fuzzy"
    assert result.provenance == PROVENANCE_FUZZY
    assert result.score is not None and result.score >= FUZZY_MATCH_THRESHOLD
    assert result.candidates and result.candidates[0].item_id == SY_0045


def test_a_misspelling_still_matches_because_the_measures_agree():
    result = resolve_line(
        raw_sku=None,
        raw_description="Columbian Whole Bean 5lb",
        catalog=_sample_catalog(),
        sku_rules={},
    )
    assert result.item_id == CF_1001
    assert result.method == "fuzzy"


def test_below_threshold_fuzzy_is_a_suggestion_and_never_applied():
    """
    Section 7.6 / Section 10, the rule this whole module exists to obey. An
    abbreviation a person would recognize is still not something the system
    may decide by itself.
    """
    result = resolve_line(
        raw_sku=None,
        raw_description="Colombian WB 5lb",
        catalog=_sample_catalog(),
        sku_rules={},
    )
    assert result.item_id is None
    assert result.method is None
    assert result.candidates, "a below-threshold result must still surface its candidates"
    assert result.candidates[0].item_id == CF_1001
    assert result.candidates[0].score < FUZZY_MATCH_THRESHOLD


def test_a_different_pack_size_is_never_auto_applied_however_well_it_scores():
    """
    The reason the measure guard exists. This pair scores 0.96 -- higher than
    several pairs that *should* match -- because string similarity is blind
    to the one character that changes the product.
    """
    catalog = _catalog(CatalogItem(CF_1001, "CF-1001", "Colombian Whole Bean 2lb", "CS"))
    result = resolve_line(
        raw_sku=None, raw_description="Colombian Whole Bean 5lb", catalog=catalog, sku_rules={}
    )
    assert result.item_id is None
    assert result.candidates[0].score >= FUZZY_MATCH_THRESHOLD
    assert result.candidates[0].eligible is False
    assert result.candidates[0].blocked_reason == "measure_mismatch"


def test_two_genuinely_different_skus_that_share_words_do_not_match():
    catalog = _catalog(CatalogItem(SY_0045, "SY-0045", "Hazelnut Syrup 750ml", "EA"))
    result = resolve_line(
        raw_sku=None, raw_description="Vanilla Syrup 750ml", catalog=catalog, sku_rules={}
    )
    assert result.item_id is None


def test_a_near_miss_sku_is_never_auto_applied():
    """"CF-1002" against a catalog holding only "CF-1001" differs by one
    character and scores high. It must not resolve."""
    catalog = _catalog(CatalogItem(CF_1001, "CF-1001", None, "CS"))
    result = resolve_line(raw_sku="CF-1002", raw_description=None, catalog=catalog, sku_rules={})
    assert result.item_id is None


def test_a_sku_written_without_its_hyphen_resolves_by_similarity():
    catalog = _catalog(CatalogItem(CF_1001, "CF-1001", "Colombian Whole Bean 5lb", "CS"))
    result = resolve_line(raw_sku="CF1001", raw_description=None, catalog=catalog, sku_rules={})
    assert result.item_id == CF_1001
    assert result.method == "fuzzy"


def test_two_equally_good_candidates_are_ambiguous_and_neither_is_applied():
    """
    CLAUDE.md Section 7.15.2 Step 4 says a catalog containing "duplicate
    description with different SKUs" is a warning, not a blocker -- so this
    catalog is one a tenant really has. Picking one of two identical scores
    by sort order would be an invented answer.
    """
    other = uuid4()
    catalog = _catalog(
        CatalogItem(CF_1001, "CF-1001", "Colombian Whole Bean 5lb", "CS"),
        CatalogItem(other, "CF-1001-B", "Colombian Whole Bean 5 lb", "CS"),
    )
    result = resolve_line(
        raw_sku=None, raw_description="Colombian Whole Bean 5lb", catalog=catalog, sku_rules={}
    )
    assert result.item_id is None
    assert {c.item_id for c in result.candidates} == {CF_1001, other}


def test_an_empty_catalog_yields_no_match_and_no_candidates():
    result = resolve_line(
        raw_sku="CF-1001", raw_description="Colombian Whole Bean 5lb", catalog=_catalog(), sku_rules={}
    )
    assert result.item_id is None
    assert result.candidates == []


def test_candidates_are_capped_and_ordered_best_first():
    items = [
        CatalogItem(uuid4(), f"CF-10{index:02d}", f"Colombian Whole Bean {index}lb", "CS")
        for index in range(1, 10)
    ]
    result = resolve_line(
        raw_sku=None,
        raw_description="Colombian Whole Bean 5lb",
        catalog=_catalog(*items),
        sku_rules={},
    )
    assert len(result.candidates) == MAX_CANDIDATES
    scores = [c.score for c in result.candidates]
    assert scores == sorted(scores, reverse=True)


def test_a_line_with_neither_sku_nor_description_matches_nothing():
    result = resolve_line(raw_sku=None, raw_description=None, catalog=_sample_catalog(), sku_rules={})
    assert result.item_id is None
    assert result.candidates == []


def test_an_ambiguous_normalized_sku_is_not_an_exact_match():
    """Two live catalog rows whose SKUs differ only by padding normalize to
    one key. That is a question, not an identity."""
    other = uuid4()
    catalog = _catalog(
        CatalogItem(CF_1001, "CF-1001", "Colombian Whole Bean 5lb", "CS"),
        CatalogItem(other, " CF-1001 ", "Colombian Whole Bean 5lb", "CS"),
    )
    assert catalog.exact_sku("CF-1001") is None
    assert len(catalog.ambiguous_sku("CF-1001")) == 2


def test_candidate_json_carries_scores_as_strings():
    result = resolve_line(
        raw_sku=None, raw_description="Colombian WB 5lb", catalog=_sample_catalog(), sku_rules={}
    )
    payload = result.candidates[0].as_json()
    assert isinstance(payload["score"], str)
    assert UUID(payload["item_id"])
    assert payload["eligible"] is True


# ── units of measure: suggest, flag, never correct ──────────────────────────


def test_a_uom_alias_rule_is_found_by_its_normalized_key():
    rule = UomRule(rule_id=uuid4(), buyer_scoped=False, unit_of_measure="CASE")
    rules = {normalize_description("CS"): rule}
    assert resolve_uom("cs", rules) is rule
    assert resolve_uom(" CS ", rules) is rule
    assert resolve_uom("EA", rules) is None


def test_uom_mismatch_is_reported_when_both_units_are_known_and_differ():
    assert uom_mismatch("EA", "CS") is True
    assert uom_mismatch("CS", "cs") is False


def test_uom_mismatch_is_not_reported_when_either_unit_is_missing():
    """A missing unit is not a disagreement, and every extraction field is
    nullable (Section 7.1)."""
    assert uom_mismatch(None, "CS") is False
    assert uom_mismatch("CS", None) is False
