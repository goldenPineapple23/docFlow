"""
The product's fuzzy step against the full scan (Stage 4; D-201).

The index variants were measured on the 250,000-item tenant and none keeps
parity with the full scan on fastener catalogs. A faster full scan does, and
the founder chose it. It was measured here first, in the bench
(`SortedScan`, 57 ms a line), and is now the product's own
`matching.score_candidates`. This measures, on the largest tenant of the
mix:

* **the product** (`ProductScan`): `matching.resolve_line` as a document's
  line meets it, each line's time for fastener and general lines apart, and
  parity with the full scan (`matching.score_candidates_full_scan`) on the
  same 100 lines. This is the run a change to the fuzzy step is proven by;
* the bench's own scan with **token-sorted keys** (`SortedScan`), beside
  it: the product should take no longer;
* **a 40-line order**, cold (the catalog read, its exact-SKU table, the
  keys) and warm (the catalog kept between documents, with a check that it
  has not changed: PR B, not built);
* **memory**: the catalog as matching holds it, and its keys;
* **several cores**: the description pass on threads, and with rapidfuzz's
  own many-core call when numpy is there (it is not a product dependency).

    python scripts/bench/index_variants.py --mix 250000 --variants scan

The lines are the same on every run of the same mix and seed. The database
is this machine's.
"""

from __future__ import annotations

import os
import statistics
import sys
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import catalog_gen
import catalog_sizing as sizing
import index_variants as v

ORDER_LINES = 40  # the pass mark's order: 4 s for 40 lines
FRESHNESS_RUNS = 20
CORE_LINES = 50  # lines the several-cores comparison is timed on
THREADS = (2, 4)

# Has this tenant's catalog changed since its keys were built? Every item
# write sets updated_at, and a retired item keeps its row.
FRESHNESS = "SELECT count(*), max(updated_at) FROM items WHERE tenant_id = %s"


def sort_tokens(key: str) -> str:
    """A description key with its tokens in order: what token_sort_ratio
    compares. Both sides sorted once, the plain ratio gives the same score."""
    return " ".join(sorted(key.split()))


class SortedScan(v.FastScan):
    """FastScan with each item's description tokens sorted once, so a line
    costs one plain ratio an item for the description, as for the SKU."""

    def __init__(self, matching, catalog):
        self.matching, self.catalog = matching, catalog
        self.description_keys = [
            sort_tokens(matching.normalize_description(item.description)) for item in catalog.items
        ]
        self.sku_keys = [matching.sku_comparison_key(item.sku) for item in catalog.items]
        self.widened = 0

    def asked(self, line: v.Line) -> tuple:
        from rapidfuzz import fuzz

        m = self.matching
        return (
            (sort_tokens(m.normalize_description(line.description)), self.description_keys, fuzz.ratio),
            (m.sku_comparison_key(line.sku), self.sku_keys, fuzz.ratio),
        )


class ProductScan:
    """The product's own path: `matching.resolve_line` on a catalog of its
    own, so that catalog's keys are built here, at first need, and timed."""

    def __init__(self, matching, catalog):
        self.matching = matching
        self.catalog = matching.Catalog(catalog.items)
        started = time.perf_counter()
        self.catalog.scan_keys()
        self.build_seconds = time.perf_counter() - started
        self.widened = 0  # lines that needed more than FAST_SCAN_KEEP

    def resolve(self, line: v.Line):
        process = self.matching.process
        extract, limits = process.extract, []

        def counted(query, choices, *, scorer, limit):
            limits.append(limit)
            return extract(query, choices, scorer=scorer, limit=limit)

        with mock.patch.object(process, "extract", counted):
            match = v.product(self.matching, line, self.catalog)
        self.widened += any(limit > self.matching.FAST_SCAN_KEEP for limit in limits)
        return match


def measure(scan_class, matching, catalog, lines: dict, reference: dict) -> dict:
    """One scan on the timing lines: the key build, each line's time, parity."""
    started = time.perf_counter()
    scan = scan_class(matching, catalog)
    built = getattr(scan, "build_seconds", time.perf_counter() - started)
    found: dict = {"build_seconds": built, "ms": [], "by_family": [], "parity": [], "differ": 0}
    for family in v.FAMILIES:
        taken, matches = [], []
        for line in lines[family]:
            started = time.perf_counter()
            matches.append(scan.resolve(line))
            taken.append((time.perf_counter() - started) * 1000)
        pairs = list(zip(matches, reference[family]))
        match = sum(1 for a, b in pairs if v.outcome(a) != v.outcome(b))
        five = sum(1 for a, b in pairs if v.recorded(a) != v.recorded(b))
        found["ms"] += taken
        found["by_family"].append(f"{family} {v.spread(taken)}")
        found["parity"].append(f"{family} match {match}, five {five}")
        found["differ"] += match + five
    found["widened"] = scan.widened
    return found


def order_seconds(once: float, ms: list[float]) -> float:
    """A 40-line order: what is paid once, and 40 lines at the mean."""
    return once + ORDER_LINES * statistics.mean(ms) / 1000 if ms else once


def memory(conn, matching, tenant_id) -> list[str]:
    """What one tenant's catalog holds in a worker's memory, read and built
    again under the tracer (which would slow the timings if it ran there)."""
    tracemalloc.start()
    live = conn.execute(v.CATALOG_READ, (tenant_id,)).fetchall()
    catalog = matching.Catalog([matching.CatalogItem(*row) for row in live])
    del live
    held_catalog, _ = tracemalloc.get_traced_memory()
    catalog.scan_keys()  # the product's own keys, held until they have been measured
    held_all, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    items = len(catalog)
    return [
        f"memory, {items} items: the catalog as matching holds it (rows, items, the exact-SKU table) "
        f"{sizing.megabytes(held_catalog)}; the keys (token-sorted descriptions, SKU keys) "
        f"{sizing.megabytes(held_all - held_catalog)}; together {sizing.megabytes(held_all)}, "
        f"{held_all / max(items, 1):.0f} bytes an item; peak while building {sizing.megabytes(peak)}"
    ]


def several_cores(scan: SortedScan, lines: list[v.Line]) -> list[str]:
    """The description pass alone, every item scored for each line: on one
    core, in chunks on threads, and with rapidfuzz's many-core call."""
    from rapidfuzz import fuzz, process

    keys = scan.description_keys
    queries = [scan.asked(line)[0][0] for line in lines[:CORE_LINES]]

    def timed(score) -> str:
        taken = []
        for query in queries:
            started = time.perf_counter()
            score(query)
            taken.append((time.perf_counter() - started) * 1000)
        return v.spread(taken)

    def one(query: str, some: list[str]):
        return process.extract(query, some, scorer=fuzz.ratio, limit=v.FAST_KEEP)

    out = [
        f"this machine has {os.cpu_count()} cores; the description pass alone, ms p50/p95/max over "
        f"{len(queries)} lines, every item scored: one core {timed(lambda q: one(q, keys))}"
    ]
    for threads in THREADS:
        size = -(-len(keys) // threads)
        chunks = [keys[i : i + size] for i in range(0, len(keys), size)]
        with ThreadPoolExecutor(threads) as pool:
            spread = timed(lambda q, pool=pool, chunks=chunks: list(pool.map(lambda c: one(q, c), chunks)))
        out.append(f"in {threads} chunks on {threads} threads: {spread}")
    try:
        import numpy
    except ImportError:
        out.append("rapidfuzz's many-core call (process.cdist) was not measured: numpy is not installed")
        return out
    for workers in (1, *THREADS):
        spread = timed(
            lambda q, w=workers: process.cdist([q], keys, scorer=fuzz.ratio, workers=w, dtype=numpy.float64)
        )
        out.append(f"process.cdist with {workers} workers (scores only, the best not yet picked): {spread}")
    return out


def run(database_url: str, mix: str, seed: int) -> int:
    import psycopg

    from docflow_core import matching

    sizing.refuse_unless_local(database_url)
    sizes = catalog_gen.parse_mix(mix)
    failed = 0

    def emit(title: str, lines: list[str]) -> None:
        nonlocal failed
        sizing.emit(title, lines)
        if len("\n".join(lines)) > v.ANNOTATION_LIMIT:
            failed += 1
            print(f"::error title=catalog sizing: a report was cut::{title}", flush=True)

    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("SET search_path = public, extensions")
        conn.execute("SET statement_timeout = 0")
        version = conn.execute("SHOW server_version").fetchone()[0]
        emit(
            "scan: inputs",
            [
                f"mix {mix}: {len(sizes)} tenants, {sum(sizes)} items; seed {seed}; Postgres {version}",
                f"commit {os.environ.get('GITHUB_SHA', '')[:7] or 'not in CI'}; "
                f"runner {os.environ.get('RUNNER_OS', sys.platform)}, {os.cpu_count()} cores",
                f"the largest tenant, {max(sizes)} items; timings on {v.TIMING_LINES} lines a family, parity "
                f"with the full scan on {v.PARITY_LINES}; no line carries its item's exact SKU; the lines "
                "are the same on every run of this mix and seed",
                "no index is built: every item is scored. 'the product' is matching.resolve_line as a "
                "document's line meets it; 'the full scan' is matching.score_candidates_full_scan, the "
                "per-item loop it must agree with",
            ],
        )
        ctx = v.prepare(conn, sizes, seed, matching)
        catalog, lines, reference = ctx["catalog"], ctx["lines"], ctx["reference"]

        in_product = measure(ProductScan, matching, catalog, lines, reference)
        sorted_keys = measure(SortedScan, matching, catalog, lines, reference)
        if in_product["differ"]:
            # The run is the proof of parity at this size: a difference fails it.
            failed += 1
            print(
                "::error title=catalog sizing: the product differs from the full scan::"
                + " | ".join(in_product["parity"]),
                flush=True,
            )
        emit(
            "scan: each line and parity",
            [
                *ctx["today"],
                f"the product (matching.score_candidates, keeping {matching.FAST_SCAN_KEEP}): ms a line "
                f"p50/p95/max {' | '.join(in_product['by_family'])}; lines that differ from the full "
                f"scan's of {v.PARITY_LINES} a family: {' | '.join(in_product['parity'])}; kept more than "
                f"{matching.FAST_SCAN_KEEP} on {in_product['widened']} lines",
                f"the bench's scan with token-sorted keys, beside it: ms a line p50/p95/max "
                f"{' | '.join(sorted_keys['by_family'])}; lines that differ from the full scan's of "
                f"{v.PARITY_LINES} a family: {' | '.join(sorted_keys['parity'])}; kept more than "
                f"{v.FAST_KEEP} on {sorted_keys['widened']} lines",
                ctx["loaded"],
            ],
        )

        taken = []
        for _ in range(FRESHNESS_RUNS):
            started = time.perf_counter()
            conn.execute(FRESHNESS, (ctx["tenant_id"],)).fetchone()
            taken.append(time.perf_counter() - started)
        fresh = statistics.median(taken)
        cold = ctx["read_seconds"] + ctx["catalog_seconds"]
        emit(
            "scan: a 40-line order cold and warm",
            [
                f"paid once a document, as the product is (nothing kept between documents): the catalog "
                f"read {ctx['read_seconds']:.2f} s, its exact-SKU table {ctx['catalog_seconds']:.2f} s, and "
                f"the keys {in_product['build_seconds']:.2f} s, built at the first line that needs them",
                f"a {ORDER_LINES}-line order of unmatched lines in the product: "
                f"{order_seconds(cold + in_product['build_seconds'], in_product['ms']):.2f} s",
                f"if the catalog and its keys were kept between documents (PR B, not built): the check "
                f"that the catalog has not changed, {fresh * 1000:.1f} ms (median of {FRESHNESS_RUNS}; "
                f"count and newest updated_at of the tenant's items), and the same order "
                f"{order_seconds(fresh, in_product['ms']):.2f} s",
                f"a {ORDER_LINES}-line order by the full scan on every line: "
                f"{order_seconds(cold, [1000 * s for s in ctx['today_seconds']]):.0f} s",
                *memory(conn, matching, ctx["tenant_id"]),
            ],
        )
        emit("scan: several cores", several_cores(SortedScan(matching, catalog), lines["fastener"]))
    return 1 if failed else 0
