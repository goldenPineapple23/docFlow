"""
The fuzzy step's fast scan returns what the full scan returns (D-201).

`matching.score_candidates` no longer runs the per-item code on every catalog
item: rapidfuzz scores the whole catalog in one call per field, and the
per-item code runs on the best `FAST_SCAN_KEEP` by each. That is only allowed
to change how long a line takes. `matching.score_candidates_full_scan` is the
loop as it was, kept as the definition, and every test here holds the fast
scan to it: the same applied match, score and provenance, and the same five
candidates with the same scores, `eligible` and `blocked_reason`, in the same
order.

A line that gets a different candidate list is a reviewer shown the wrong
alternatives, and one that gets a different winner is a wrong SKU pre-filled
(Section 7.6). So the cases that could differ are named one by one below: a
cut that falls among equally scored items, duplicate descriptions under
different SKUs (7.15.2 Step 4), D-066's hard pairs, lines with a field
missing, and the library handing back less than it was asked for.

The catalogs are the bench's generated ones (scripts/bench/catalog_gen.py):
fake, from a fixed seed, with fastener families whose siblings differ by one
token. No test here needs a database.
"""

from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from rapidfuzz import fuzz

from docflow_core import matching
from docflow_core.matching import Catalog, CatalogItem, SkuRule

BENCH = Path(__file__).resolve().parents[3] / "scripts" / "bench"


def _bench(name: str):
    """A bench module, loaded once for the whole run (test_bench_catalog.py
    loads the same files, and a dataclass looks its own module up by name)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, BENCH / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gen = _bench("catalog_gen")
_bench("catalog_sizing")
variants = _bench("index_variants")

# (seed, tenant number): three catalogs in different house styles.
CATALOGS = [(7, 1), (11, 2), (20261011, 3)]
ITEMS = 1500
LINES_A_FAMILY = 9  # one of each of the nine kinds of line


def _items(seed: int, tenant: int, count: int) -> list[CatalogItem]:
    return [
        CatalogItem(UUID(int=number), made.sku, made.description, made.unit_of_measure)
        for number, made in enumerate(gen.catalog(seed, tenant, count), start=1)
    ]


def _lines(items: list[CatalogItem], seed: int) -> list:
    """Unmatched lines of all nine kinds for each family: the description as
    written, a token dropped, two tokens swapped; the SKU one character
    wrong, absent, or the buyer's own number."""
    rows = [(item.sku, item.description or "") for item in items]
    general = [row for row in rows if row[1].startswith(variants.GENERAL_PREFIX)]
    fasteners = [row for row in rows if not row[1].startswith(variants.GENERAL_PREFIX)]
    assert len(general) >= LINES_A_FAMILY and len(fasteners) >= LINES_A_FAMILY
    return variants.bench_lines(fasteners[:LINES_A_FAMILY], seed) + variants.bench_lines(
        general[:LINES_A_FAMILY], seed
    )


def _full_scan_line(monkeypatch, sku, description, catalog, sku_rules=None):
    """`resolve_line` as it was: the full scan in the fast scan's place."""
    with monkeypatch.context() as patch:
        patch.setattr(matching, "score_candidates", matching.score_candidates_full_scan)
        return matching.resolve_line(
            raw_sku=sku, raw_description=description, catalog=catalog, sku_rules=sku_rules or {}
        )


def _stored(match) -> tuple:
    """Everything matching writes on the line."""
    return (
        match.item_id,
        match.method,
        match.score,
        match.provenance,
        match.learned_rule_id,
        [candidate.as_json() for candidate in match.candidates],
    )


def _same(monkeypatch, sku, description, catalog, sku_rules=None):
    """The fast path and the full scan agree on this line; returns the match."""
    reference = _full_scan_line(monkeypatch, sku, description, catalog, sku_rules)
    fast = matching.resolve_line(
        raw_sku=sku, raw_description=description, catalog=catalog, sku_rules=sku_rules or {}
    )
    assert _stored(fast) == _stored(reference), (sku, description)
    assert matching.score_candidates(sku, description, catalog) == matching.score_candidates_full_scan(
        sku, description, catalog
    ), (sku, description)
    return fast


@lru_cache(maxsize=None)
def _generated(seed: int, tenant: int):
    """A generated catalog, its lines, and the full scan's answer for each
    (the slow part, done once for every test that reads it)."""
    items = _items(seed, tenant, ITEMS)
    lines = _lines(items, seed)
    reference = [
        matching.score_candidates_full_scan(line.sku, line.description, Catalog(items)) for line in lines
    ]
    return items, lines, reference


class _Spy:
    """Records what the fast scan asks rapidfuzz for, and passes it through."""

    def __init__(self, monkeypatch, answer=None):
        self.calls: list[dict] = []
        self._real = matching.process.extract
        self._answer = answer or (lambda best: best)
        monkeypatch.setattr(matching.process, "extract", self)

    def __call__(self, query, choices, *, scorer, limit):
        self.calls.append({"query": query, "choices": choices, "scorer": scorer, "limit": limit})
        return self._answer(self._real(query, choices, scorer=scorer, limit=limit))

    @property
    def limits(self) -> list[int]:
        return [call["limit"] for call in self.calls]


# ── Parity on generated catalogs ────────────────────────────────────────────


@pytest.mark.parametrize(("seed", "tenant"), CATALOGS)
@pytest.mark.parametrize("keep", [matching.FAST_SCAN_KEEP, 3])
def test_every_line_gets_the_full_scans_candidates(monkeypatch, seed, tenant, keep):
    """Both families, all nine kinds of line, three catalogs. Keeping only 3
    puts the cut among tied siblings on most lines, which is the case that
    has to keep more and look again."""
    monkeypatch.setattr(matching, "FAST_SCAN_KEEP", keep)
    items, lines, reference = _generated(seed, tenant)
    catalog = Catalog(items)
    spy = _Spy(monkeypatch)
    winners = []
    for line, full in zip(lines, reference, strict=True):
        assert matching.score_candidates(line.sku, line.description, catalog) == full, line
        match = matching.resolve_line(
            raw_sku=line.sku, raw_description=line.description, catalog=catalog, sku_rules={}
        )
        assert match.candidates == full, line
        assert match.method in (None, "fuzzy")
        winners.append(match.item_id)
    # The lines are not all one outcome: some are applied, some are left for the reviewer.
    assert any(winners) and not all(winners)
    assert all(len(full) == matching.MAX_CANDIDATES for full in reference)
    looked_again = any(limit > keep for limit in spy.limits)
    assert looked_again or keep == matching.FAST_SCAN_KEEP


def test_the_applied_match_is_the_full_scans_on_every_line(monkeypatch):
    """The whole of `resolve_line`, not the candidate list alone."""
    items, lines, _ = _generated(*CATALOGS[0])
    catalog = Catalog(items)
    for line in lines:
        _same(monkeypatch, line.sku, line.description, catalog)


def test_the_score_rapidfuzz_ranks_by_is_the_score_that_is_stored():
    """The cut is only safe if the one-call score of every item is the score
    the per-item code gives it. `token_sort_ratio` is the plain ratio of the
    two keys with their tokens sorted; held here on every item, both fields."""
    items, lines, _ = _generated(*CATALOGS[0])
    description_keys, sku_keys = Catalog(items).scan_keys()
    assert description_keys != [matching.normalize_description(item.description) for item in items]
    for line in lines:
        asked = matching._sorted_tokens(matching.normalize_description(line.description))
        for item, key in zip(items, description_keys, strict=True):
            assert matching._as_score(fuzz.ratio(asked, key)) == matching.description_similarity(
                line.description, item.description
            )
        asked_sku = matching.sku_comparison_key(line.sku)
        if not asked_sku:
            continue
        for item, key in zip(items, sku_keys, strict=True):
            assert matching._as_score(fuzz.ratio(asked_sku, key)) == matching.sku_similarity(
                line.sku, item.sku
            )


# ── Every item is still scored ──────────────────────────────────────────────


def test_every_item_is_scored_on_both_fields_and_the_per_item_code_runs_on_few(monkeypatch):
    items, lines, _ = _generated(*CATALOGS[0])
    catalog = Catalog(items)
    both_fields = next(line for line in lines if line.sku and line.description)
    scored: list[int] = []
    per_item = matching._score_items
    monkeypatch.setattr(
        matching,
        "_score_items",
        lambda sku, description, some: scored.append(len(some)) or per_item(sku, description, some),
    )
    spy = _Spy(monkeypatch)
    matching.score_candidates(both_fields.sku, both_fields.description, catalog)

    assert [len(call["choices"]) for call in spy.calls] == [ITEMS, ITEMS]
    assert all(call["scorer"] is fuzz.ratio for call in spy.calls)
    assert spy.limits == [matching.FAST_SCAN_KEEP] * 2
    assert spy.calls[0]["choices"] is catalog.scan_keys()[0]
    assert spy.calls[1]["choices"] is catalog.scan_keys()[1]
    # An ordinary line: the per-item code ran once, on at most the two kept lists.
    assert len(scored) == 1 and scored[0] <= 2 * matching.FAST_SCAN_KEEP


# ── The keys are built only when a line needs them ──────────────────────────


def test_an_order_matched_by_rule_and_exact_sku_never_builds_the_keys(monkeypatch):
    items = _items(7, 1, 400)
    catalog = Catalog(items)
    rule = SkuRule(rule_id=uuid4(), buyer_scoped=True, item_id=items[5].item_id)
    sku_rules = {matching.normalize_description("The buyer's own words for it"): rule}
    order = [(item.sku, item.description) for item in items[:40]]
    order.append((None, "the buyers own words for it"))

    def never(*args, **kwargs):
        raise AssertionError("no line of this order needs the fuzzy step")

    monkeypatch.setattr(matching.process, "extract", never)
    methods = []
    for sku, description in order:
        match = matching.resolve_line(
            raw_sku=sku, raw_description=description, catalog=catalog, sku_rules=sku_rules
        )
        methods.append(match.method)
        assert not catalog.scan_keys_built
    assert methods == ["exact_sku"] * 40 + ["learned_rule"]


def test_the_keys_are_built_at_the_first_line_that_needs_them_and_reused(monkeypatch):
    items = _items(7, 1, 400)
    catalog = Catalog(items)
    typed_wrong = (None, (items[60].description or "").lower() + " zz")
    order = [
        (items[0].sku, items[0].description),  # exact SKU
        (items[1].sku, items[1].description),  # exact SKU
        typed_wrong,  # the first line to need the fuzzy step
        (items[2].sku, items[2].description),  # exact SKU
        ("no-such-sku-9", items[80].description),  # the fuzzy step again
    ]
    built, keys = [], []
    for sku, description in order:
        _same(monkeypatch, sku, description, Catalog(items))  # parity, on a catalog of its own
        matching.resolve_line(raw_sku=sku, raw_description=description, catalog=catalog, sku_rules={})
        built.append(catalog.scan_keys_built)
        keys.append(catalog._scan_keys)
    assert built == [False, False, True, True, True]
    assert keys[2] is keys[3] is keys[4]  # built once, not once a line
    assert [len(part) for part in keys[2]] == [400, 400]


@pytest.mark.parametrize(
    ("count", "expected"), [(matching.FAST_SCAN_KEEP, False), (matching.FAST_SCAN_KEEP + 1, True)]
)
def test_a_catalog_no_bigger_than_what_is_kept_is_scored_as_it_always_was(monkeypatch, count, expected):
    """Nothing would be left out, so nothing is built and nothing is cut."""
    items = _items(7, 1, count)
    catalog = Catalog(items)
    _same(monkeypatch, "no-such-sku-9", items[3].description, catalog)
    assert catalog.scan_keys_built is expected


def test_an_empty_catalog_has_no_candidates():
    catalog = Catalog([])
    assert matching.score_candidates("CF-1001", "Colombian Whole Bean 5lb", catalog) == []
    assert not catalog.scan_keys_built


# ── A cut among equally scored items ────────────────────────────────────────


def _with_duplicates(filler: int, duplicates: int) -> list[CatalogItem]:
    """A catalog holding one description under many SKUs (7.15.2 Step 4
    expects this), with the SKUs in reverse order so that whichever of them
    the library keeps first, it is not the ones the full scan lists."""
    items = _items(7, 1, filler)
    for number in range(duplicates, 0, -1):
        items.append(
            CatalogItem(UUID(int=10_000 + number), f"ZZ-DUP-{number:03d}", "Acme Test Gasket Blue 5lb", "EA")
        )
    return items


@pytest.mark.parametrize(
    ("keep", "filler", "duplicates", "limits"),
    [
        (3, 300, 12, [3, 12, 48]),  # 12 tie at the top: 3 and 12 kept are all ties, 48 reach past them
        (100, 300, 150, [100, 400]),  # the default: 100 kept are all ties
        (100, 10, 140, [100]),  # looking again would keep the whole catalog: the full scan itself
    ],
)
def test_duplicate_descriptions_under_different_skus_list_the_full_scans_five(
    monkeypatch, keep, filler, duplicates, limits
):
    monkeypatch.setattr(matching, "FAST_SCAN_KEEP", keep)
    catalog = Catalog(_with_duplicates(filler, duplicates))
    spy = _Spy(monkeypatch)
    match = _same(monkeypatch, None, "Acme Test Gasket Blue 5 lb", catalog)
    assert [candidate.sku for candidate in match.candidates] == [f"ZZ-DUP-{n:03d}" for n in range(1, 6)]
    assert {candidate.score for candidate in match.candidates} == {matching.EXACT_MATCH_SCORE}
    # Equally good items: nothing is applied, the reviewer chooses (the ambiguity guard).
    assert not match.matched
    # `_same` runs the fast scan twice (through resolve_line, then on its own).
    assert spy.limits == limits * 2


def test_two_items_the_sort_cannot_tell_apart_stay_in_catalog_order(monkeypatch):
    """Same score and same SKU text: the full scan leaves them as the catalog
    has them, so the fast scan must too."""
    items = _items(7, 1, 300)
    twins = [
        CatalogItem(UUID(int=20_000 + n), "TW-1", "Acme Test Twin Valve 2in", "EA") for n in range(8, 0, -1)
    ]
    catalog = Catalog(items[:150] + twins[:4] + items[150:] + twins[4:])
    monkeypatch.setattr(matching, "FAST_SCAN_KEEP", 3)
    match = _same(monkeypatch, "TW-2", "Acme Test Twin Valve 2 in", catalog)
    assert [candidate.item_id for candidate in match.candidates] == [twin.item_id for twin in twins[:5]]


# ── D-066's hard pairs, in a catalog big enough to be cut ───────────────────


def test_a_changed_measure_is_never_applied_whichever_path_scores_it(monkeypatch):
    five, two = UUID(int=30_001), UUID(int=30_002)
    catalog = Catalog(
        _items(7, 1, 300)
        + [
            CatalogItem(five, "CF-1001", "Colombian Whole Bean 5lb", "CS"),
            CatalogItem(two, "CF-1002", "Colombian Whole Bean 2lb", "CS"),
        ]
    )
    # The same product misspelled, the measure written with a space, and a size the catalog lacks.
    for description in ("Columbian Whole Bean 5lb", "Colombian Whole Bean 2 lb", "Colombian Whole Bean 3lb"):
        match = _same(monkeypatch, None, description, catalog)
        assert {five, two} <= {candidate.item_id for candidate in match.candidates}
        if match.matched:
            item = catalog.get(match.item_id)
            assert item is not None and matching.measures_agree(description, item.description)
    blocked = _same(monkeypatch, None, "Colombian Whole Bean 3lb", catalog)
    assert not blocked.matched
    assert [c.blocked_reason for c in blocked.candidates[:2]] == ["measure_mismatch", "measure_mismatch"]


# ── Lines and items with a field missing ────────────────────────────────────


def test_lines_and_items_with_a_field_missing(monkeypatch):
    items = _items(7, 1, 300) + [
        CatalogItem(UUID(int=40_001), "---", None, None),  # no description, and a SKU with no key
        CatalogItem(UUID(int=40_002), "NODESC-1", "", "EA"),
        CatalogItem(UUID(int=40_003), "  nodesc-2 ", "   ", "EA"),
    ]
    catalog = Catalog(items)
    one_wrong = items[10].sku[:-1] + ("x" if items[10].sku[-1] != "x" else "y")
    for sku, description in [
        (one_wrong, None),  # a SKU and no description
        (one_wrong, "   "),
        (None, items[10].description),  # a description and no SKU
        ("", items[10].description),
        ("NODESC-9", None),  # close to the items that have no description
        ("---", "---"),  # nothing in either field once it is normalized
    ]:
        _same(monkeypatch, sku, description, catalog)
    spy = _Spy(monkeypatch)
    for sku, description in [(None, None), ("", ""), ("  ", " \t ")]:
        assert matching.score_candidates(sku, description, catalog) == []
    assert spy.calls == []  # nothing to ask: every item scores zero
    matching.score_candidates(one_wrong, None, catalog)
    assert len(spy.calls) == 1 and spy.calls[0]["choices"] is catalog.scan_keys()[1]


# ── The library handing back less than it was asked for ─────────────────────


def test_a_short_answer_from_the_library_is_never_taken_as_the_whole_catalog(monkeypatch):
    """If fewer items come back than were asked for, what was left out is not
    known to score below the last one kept. The fast scan then keeps more,
    and in the end runs the full scan, which needs no such answer."""
    items, lines, reference = _generated(*CATALOGS[0])
    catalog = Catalog(items)
    spy = _Spy(monkeypatch, answer=lambda best: best[:-1])
    for line, full in list(zip(lines, reference, strict=True))[:4]:
        assert matching.score_candidates(line.sku, line.description, catalog) == full
    assert max(spy.limits) >= ITEMS / matching.FAST_SCAN_WIDEN  # it kept widening to the end
