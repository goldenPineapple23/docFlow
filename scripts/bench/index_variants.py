"""
Index variants for the candidate query (Stage 4; founder, 2026-10-11).

The sizing runs showed that the two GiST trigram indexes of
scripts/bench/trigram_indexes.sql order a tenant's items but do not narrow
the search: on a 250,000-item fastener catalog each candidate query read its
tenant's whole part of the index, about 116 ms a line for the two queries,
over the 100 ms pass mark before any scoring. This job measures the
alternatives on the same rows, on the same machine, so the founder can
choose one before PR 2 is built.

    python scripts/bench/index_variants.py --mix 250000 --variants all

It loads the mix once, with no trigram index. Then, for each variant, it
creates the two indexes, measures, and drops them:

* size, bytes a row and build time;
* the plan: which index was used, and buffers read against the index's pages;
* p50, p95 and max of the description query and the SKU query, for fastener
  lines and for general "Acme Test" lines apart, fetching 50, 100 and 200
  candidates;
* parity: for each line, today's full scan in Python (matching.resolve_line
  over the whole catalog) against the same code over the fetched candidates.
  The applied match and the five recorded candidates must be the same;
* the design's cut-off guard (A3): on how many lines the K-th candidate is
  still similar (0.5 or more), and on how many the 4K-th is too, which is a
  line the design would send to the full scan.

It also times today's full scan per line on this machine, because that is
what a fallback costs and what top-K must beat; and a faster full scan (the
catalog's comparison keys built once, `FastScan` below), alone and as the
fallback behind each variant. The faster scan lives here only: nothing in
the product changes.

**It refuses any database that is not on this machine**, as the sizing job
does. Every figure is a GitHub annotation (RUNBOOK 1.6). GitHub keeps ten
notices from one step, and 4,096 characters of each: the inputs, the full
scan, and one per variant (for GIN, one per threshold), each written short.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import statistics
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import catalog_gen  # noqa: E402
import catalog_sizing as sizing  # noqa: E402

KS = (50, 100, 200)  # candidates fetched from each list
TIMING_LINES = 200  # per family
PARITY_LINES = 50  # per family: the first of the timing lines
PLAN_LINES = 5  # per family: lines whose plan and buffers are read
GUARD_SIMILARITY = 0.5  # the design's cut-off guard (A3): "still similar"
GUARD_FACTOR = 4  # ... and the wider fetch it asks for before the full scan
THRESHOLDS = (0.3, 0.5, 0.7)  # pg_trgm.similarity_threshold, for the GIN variant
FAST_KEEP = 100  # the faster full scan: items kept from each score before today's code runs on them
GENERAL_PREFIX = "Acme Test "  # catalog_gen's general families; the rest are fasteners
FAMILIES = ("fastener", "general")
SKU_KINDS = ("one character wrong", "no SKU", "the buyer's own number")
NOTICES_PER_STEP = 10

# The catalog as matching.load_catalog reads it.
CATALOG_READ = (
    "SELECT id, sku, description, unit_of_measure FROM items WHERE tenant_id = %s AND deleted_at IS NULL"
)

EXPRESSIONS = {
    "description": "lower(description)",
    "SKU": "lower(regexp_replace(sku, '[^A-Za-z0-9]', '', 'g'))",
}


@dataclass(frozen=True)
class Variant:
    name: str
    what: str
    creates: dict[str, str]  # "description" / "SKU" -> its create index statement
    thresholds: tuple[float, ...] = ()  # empty: the K nearest; else: items above the threshold
    needs_tenants: int = 1  # the control without tenant_id says nothing on one tenant


@dataclass(frozen=True)
class Line:
    description: str  # as the query sends it: lowercased
    sku: str | None  # letters and digits only, lowercased; None when the buyer wrote none
    sku_kind: str


def _create(name: str, method: str, opclass: str, tenant: bool = True) -> dict[str, str]:
    return {
        field: (
            f"create index bench_{name}_{field.lower()} on items using {method} "
            f"({'tenant_id, ' if tenant else ''}{expression} extensions.{opclass}) where deleted_at is null"
        )
        for field, expression in EXPRESSIONS.items()
    }


def baseline_creates() -> dict[str, str]:
    """The two statements of trigram_indexes.sql, word for word: the baseline
    is what migration 0037 would carry today."""
    creates = [
        s
        for s in sizing.statements(sizing.INDEX_SQL.read_text(encoding="utf-8"))
        if s.startswith("create index")
    ]
    by_name = {re.search(r"create index (\w+)", s).group(1): s for s in creates}
    return {"description": by_name["idx_items_trgm_description"], "SKU": by_name["idx_items_trgm_sku"]}


def variants() -> list[Variant]:
    return [
        Variant("baseline", "GiST with tenant_id, signature length 12 (the default)", baseline_creates()),
        *(
            Variant(
                f"siglen{n}",
                f"GiST with tenant_id, signature length {n}",
                _create(f"siglen{n}", "gist", f"gist_trgm_ops(siglen={n})"),
            )
            for n in (64, 128, 256)
        ),
        Variant(
            "gin",
            "GIN with tenant_id, items above a similarity threshold",
            _create("gin", "gin", "gin_trgm_ops"),
            thresholds=THRESHOLDS,
        ),
        Variant(
            "no_tenant",
            "control: GiST without tenant_id, signature length 12",
            _create("no_tenant", "gist", "gist_trgm_ops", tenant=False),
            needs_tenants=2,
        ),
    ]


def chosen(names: str) -> list[Variant]:
    """`all`, or some of the variants' names separated by commas."""
    every = variants()
    if names.strip().lower() == "all":
        return every
    by_name = {v.name: v for v in every}
    asked = [n.strip() for n in names.split(",") if n.strip()]
    unknown = [n for n in asked if n not in by_name]
    if unknown or not asked:
        raise ValueError(
            f"can't read the variants {names!r}: write all, or some of {', '.join(by_name)} with commas"
        )
    return [by_name[n] for n in asked]


def index_name(statement: str) -> str:
    return re.search(r"create index (\w+)", statement).group(1)


# ── The queries ─────────────────────────────────────────────────────────────
# Each returns the SQL and the order of its parameters: "t" the tenant, "v"
# the line's value. A literal % is written %% because the driver binds.


def nearest(expression: str, k: int, with_similarity: bool = False) -> tuple[str, str]:
    select, marks = ("id", "tv")
    if with_similarity:
        select, marks = (f"id, similarity({expression}, %s::text)", "vtv")
    return (
        f"SELECT {select} FROM items WHERE tenant_id = %s AND deleted_at IS NULL "
        f"ORDER BY {expression} <-> %s::text LIMIT {k}",
        marks,
    )


def above_threshold(expression: str, k: int, with_similarity: bool = False) -> tuple[str, str]:
    select, marks = ("id", "tvv")
    if with_similarity:
        select, marks = (f"id, similarity({expression}, %s::text)", "vtvv")
    return (
        f"SELECT {select} FROM items WHERE tenant_id = %s AND deleted_at IS NULL "
        f"AND {expression} %% %s::text ORDER BY similarity({expression}, %s::text) DESC, id LIMIT {k}",
        marks,
    )


def count_above_threshold(expression: str) -> tuple[str, str]:
    return (
        "SELECT count(*) FROM items WHERE tenant_id = %s AND deleted_at IS NULL "
        f"AND {expression} %% %s::text",
        "tv",
    )


def bind(marks: str, tenant_id: uuid.UUID, value: str) -> tuple:
    return tuple(tenant_id if mark == "t" else value for mark in marks)


# ── The lines ───────────────────────────────────────────────────────────────


def bench_lines(rows: list[tuple[str, str]], seed: int) -> list[Line]:
    """What a buyer might write for each sampled item, as an UNMATCHED line
    meets it: no line carries the item's exact SKU (an exact SKU is matched
    before any candidate is fetched). The description is as written, or one
    token dropped, or two tokens swapped; the SKU has one character wrong, or
    is missing, or is the buyer's own part number. All nine pairs, in turn."""
    rng = random.Random(f"docflow-bench-variants:{seed}")
    out = []
    for number, (sku, description) in enumerate(rows):
        tokens = description.split()
        if number % 3 == 1 and len(tokens) > 2:
            tokens.pop(rng.randrange(len(tokens)))
        elif number % 3 == 2 and len(tokens) > 2:
            i = rng.randrange(len(tokens) - 1)
            tokens[i], tokens[i + 1] = tokens[i + 1], tokens[i]
        kind = SKU_KINDS[(number // 3) % 3]
        key: str | None = sizing.sku_key(sku)
        if kind == "one character wrong" and key:
            i = rng.randrange(len(key))
            key = key[:i] + ("x" if key[i] != "x" else "y") + key[i + 1 :]
        elif kind == "no SKU":
            key = None
        elif kind == "the buyer's own number":
            key = f"bp{rng.randrange(10**5, 10**6)}"
        out.append(Line(" ".join(tokens).lower(), key or None, kind))
    return out


# ── Parity and the guard: no database ───────────────────────────────────────


def outcome(match) -> tuple:
    """What matching would store for the line: the design's parity (A3)."""
    return (match.item_id, match.method, match.score, match.provenance)


def recorded(match) -> list[dict]:
    return [candidate.as_json() for candidate in match.candidates]


def resolve(matching, line: Line, catalog):
    return matching.resolve_line(
        raw_sku=line.sku, raw_description=line.description, catalog=catalog, sku_rules={}
    )


def from_candidates(matching, line: Line, catalog, ids: list[uuid.UUID]):
    """Today's code over the fetched candidates only."""
    seen: dict[uuid.UUID, None] = dict.fromkeys(ids)
    items = [item for item in (catalog.get(i) for i in seen) if item is not None]
    return resolve(matching, line, matching.Catalog(items))


def guard(similarities: list[float], k: int) -> tuple[bool, bool]:
    """(the K-th is still similar, the 4K-th is too). The second is a line
    the design's cut-off guard sends to the full scan."""
    close = len(similarities) >= k and similarities[k - 1] >= GUARD_SIMILARITY
    wide = GUARD_FACTOR * k
    return close, close and len(similarities) >= wide and similarities[wide - 1] >= GUARD_SIMILARITY


def scores(match) -> list[tuple]:
    """The recorded candidates without their items. When two paths record
    the same scores and other items, they differ only in which of several
    equally scored items made the five."""
    return [(c["score"], c["matched_on"], c["eligible"], c["blocked_reason"]) for c in recorded(match)]


def spread(values: list[float]) -> str:
    if not values:
        return "no lines"
    return f"{sizing.percentile(values, 0.5):.1f}/{sizing.percentile(values, 0.95):.1f}/{max(values):.1f}"


# GitHub cuts an annotation's text at 4,096 characters without a word (the
# first run of this job lost half the GIN variant's report that way), so a
# variant's report is written short. LEGEND goes out once, with the inputs.
ANNOTATION_LIMIT = 4096
LEGEND = (
    "reading a variant: 'ms' is p50/p95/max a line over the timing lines; 'match' = lines whose applied "
    "match differs from today's full scan; 'five' = lines whose five recorded candidates differ; 'ties "
    "only' = of those, lines with the same scores and other items among equally scored ones; wrong / none "
    "/ own = those lines by SKU kind (one character wrong, no SKU, the buyer's own number); 'guard' = "
    f"lines whose K-th candidate is still at {GUARD_SIMILARITY} similarity or more, and lines whose "
    f"{GUARD_FACTOR}K-th is too, which the design sends to the full scan; 'with scan' = both queries plus "
    "the faster full scan on those lines, and how many lines then differ from today's full scan"
)
_KIND_WORDS = dict(zip(SKU_KINDS, ("wrong", "none", "own")))


def report_for_k(k: int, measured: dict[str, dict]) -> list[str]:
    """One variant's four lines for one number of candidates fetched."""
    ms, parity, guarded, with_scan = [], [], [], []
    for family, found in measured.items():
        ms.append(
            f"{family} description {spread(found['ms']['description'])}, SKU {spread(found['ms']['SKU'])}"
        )
        kinds = ", ".join(f"{_KIND_WORDS[kind]} {found['five'][kind]}" for kind in SKU_KINDS)
        parity.append(
            f"{family} match {found['match']}, five {sum(found['five'].values())} "
            f"(ties only {found['ties']}; {kinds})"
        )
        guarded.append(f"{family} {k}th on {found['close']}, {GUARD_FACTOR * k}th on {found['full']}")
        with_scan.append(
            f"{family} {spread(found['with_scan_ms'])} ms, scan on {found['full']}, "
            f"differs {found['with_scan_differs']}"
        )
    return [
        f"fetching {k}, ms: " + " | ".join(ms),
        f"fetching {k}, parity on {PARITY_LINES}: " + " | ".join(parity),
        f"fetching {k}, guard on {PARITY_LINES}: " + " | ".join(guarded),
        f"fetching {k}, with scan on {PARITY_LINES}: " + " | ".join(with_scan),
    ]


def notices(asked: list[Variant]) -> int:
    """How many notices a run of these variants prints: the inputs, the full
    scan, and one for each variant or, for GIN, each threshold."""
    return 2 + sum(max(1, len(variant.thresholds)) for variant in asked)


# ── A faster full scan: no database, and not in the product ─────────────────


class FastScan:
    """Today's full scan with its preparation done once (founder,
    2026-10-11: measured here, nothing in the product changes).

    Today every line rebuilds every item's comparison keys. This builds them
    once for the catalog, has rapidfuzz score every item in one call per
    score, keeps the best `FAST_KEEP` by each score, and runs today's code
    on those. Every item is still scored, by today's scorers on today's
    keys. An item left out scores no more than the last one kept, so when
    the fifth candidate scores above that, the five are the full scan's own;
    when it does not (a tie at the cut), four times as many are kept and the
    line is looked at again."""

    def __init__(self, matching, catalog):
        self.matching, self.catalog = matching, catalog
        self.description_keys = [matching.normalize_description(item.description) for item in catalog.items]
        self.sku_keys = [matching.sku_comparison_key(item.sku) for item in catalog.items]
        self.widened = 0  # lines that needed more than FAST_KEEP

    def asked(self, line: Line) -> tuple:
        """For each score: the line's key, every item's key, the scorer."""
        from rapidfuzz import fuzz

        m = self.matching
        return (
            (m.normalize_description(line.description), self.description_keys, fuzz.token_sort_ratio),
            (m.sku_comparison_key(line.sku), self.sku_keys, fuzz.ratio),
        )

    def resolve(self, line: Line):
        from rapidfuzz import process

        m = self.matching
        asked = self.asked(line)
        keep, widened = FAST_KEEP, False
        while True:
            kept: set[int] = set()
            cut = None  # the best score an item left out could have
            for key, keys, scorer in asked:
                if not key:
                    continue
                best = process.extract(key, keys, scorer=scorer, limit=keep)
                kept.update(index for _, _, index in best)
                if len(best) == keep and keep < len(keys):
                    low = m._as_score(best[-1][1])
                    cut = low if cut is None else max(cut, low)
            match = resolve(m, line, m.Catalog([self.catalog.items[i] for i in sorted(kept)]))
            full = len(match.candidates) >= m.MAX_CANDIDATES
            if cut is None or cut <= 0 or (full and match.candidates[m.MAX_CANDIDATES - 1].score > cut):
                self.widened += widened
                return match
            keep, widened = keep * 4, True


def measure_fast_scan(matching, catalog, lines: dict[str, list[Line]], reference: dict[str, list]) -> dict:
    """The faster full scan on the same lines: the key build's time and
    memory, each line's time, and parity with today's full scan."""
    import tracemalloc

    started = time.perf_counter()
    fast = FastScan(matching, catalog)
    build_seconds = time.perf_counter() - started
    # Built a second time under the memory tracer, which would slow the timing above. It is
    # kept until it has been measured: released first, it reads 0.0 MB (the 250,000 run did).
    tracemalloc.start()
    again = FastScan(matching, catalog)
    held, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del again

    found: dict = {"matches": {}, "ms": {}}
    timing, parity = [], []
    for family in FAMILIES:
        found["matches"][family], found["ms"][family] = [], []
        for line in lines[family]:
            started = time.perf_counter()
            found["matches"][family].append(fast.resolve(line))
            found["ms"][family].append((time.perf_counter() - started) * 1000)
        pairs = list(zip(found["matches"][family], reference[family]))
        timing.append(f"{family} {spread(found['ms'][family])} over {len(lines[family])} lines")
        parity.append(
            f"{family} applied match differs on {sum(1 for a, b in pairs if outcome(a) != outcome(b))}, "
            f"the five candidates differ on {sum(1 for a, b in pairs if recorded(a) != recorded(b))}"
        )
    found["report"] = [
        "the faster full scan (comparison keys built once a document; measured here, not in the product): "
        f"keys built in {build_seconds:.2f} s; they hold {sizing.megabytes(held)}, "
        f"{sizing.megabytes(peak)} at the peak of building them",
        "the faster full scan, ms a line p50/p95/max: " + " | ".join(timing) + f"; more than {FAST_KEEP} "
        f"items had to be kept on {fast.widened} lines",
        f"the faster full scan, parity with today's on {PARITY_LINES} lines a family: " + " | ".join(parity),
    ]
    return found


# ── Measuring one variant ───────────────────────────────────────────────────


def _plan(conn, sql: str, params: tuple) -> tuple[str, int]:
    """(the scan and its index, buffers read) from one EXPLAIN ANALYZE."""
    import psycopg

    with psycopg.ClientCursor(conn) as cur:
        cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params)
        plan = cur.fetchone()[0][0]["Plan"]
    scans: list[str] = []

    def walk(node: dict) -> None:
        if "Index Name" in node:
            scans.append(f"{node['Node Type']} on {node['Index Name']}")
        elif node["Node Type"] == "Seq Scan":
            scans.append("Seq Scan")
        for child in node.get("Plans", []):
            walk(child)

    walk(plan)
    buffers = plan.get("Shared Hit Blocks", 0) + plan.get("Shared Read Blocks", 0)
    return ", ".join(dict.fromkeys(scans)) or plan["Node Type"], buffers


def _built(conn, variant: Variant, rows: int, block: int) -> tuple[list[str], dict[str, int]]:
    lines, pages = [], {}
    for field, statement in variant.creates.items():
        started = time.perf_counter()
        conn.execute(statement)
        seconds = time.perf_counter() - started
        name = index_name(statement)
        size = conn.execute("SELECT pg_relation_size(%s::regclass)", (f"public.{name}",)).fetchone()[0]
        pages[field] = size // block
        lines.append(
            f"{field} index {name}: built in {seconds:.0f} s; {sizing.megabytes(size)}, "
            f"{size / rows:.0f} bytes a row, {pages[field]} pages"
        )
    conn.execute("ANALYZE items")
    return lines, pages


def _measure(conn, variant: Variant, threshold: float | None, pages: dict[str, int], ctx: dict) -> list[str]:
    matching, catalog, tenant_id = ctx["matching"], ctx["catalog"], ctx["tenant_id"]
    query = nearest if threshold is None else above_threshold
    if threshold is not None:
        conn.execute("SELECT set_config('pg_trgm.similarity_threshold', %s, false)", (str(threshold),))
    out: list[str] = []

    def values(line: Line) -> dict[str, str]:
        return {"description": line.description, **({"SKU": line.sku} if line.sku else {})}

    # The plan and its buffers, fetching the first K.
    parts = []
    for field, expression in EXPRESSIONS.items():
        sql, marks = query(expression, KS[0])
        per_family = []
        scan = ""
        for family in FAMILIES:
            read = []
            for line in ctx["lines"][family][:PLAN_LINES]:
                if field in values(line):
                    scan, buffers = _plan(conn, sql, bind(marks, tenant_id, values(line)[field]))
                    read.append(buffers)
            per_family.append(f"{family} {int(statistics.median(read)) if read else 'none'}")
        parts.append(f"{field} {scan}; buffers {', '.join(per_family)}; index pages {pages[field]}")
    out.append(f"plan at {KS[0]}: " + " | ".join(parts))

    # The candidates of each parity line, once, as far as the guard looks.
    deepest = GUARD_FACTOR * max(KS)
    fetched: dict[str, list[dict[str, tuple[list, list]]]] = {}
    for family in FAMILIES:
        fetched[family] = []
        for line in ctx["lines"][family][:PARITY_LINES]:
            lists = {}
            for field, value in values(line).items():
                sql, marks = query(EXPRESSIONS[field], deepest, with_similarity=True)
                rows = conn.execute(sql, bind(marks, tenant_id, value)).fetchall()
                lists[field] = ([r[0] for r in rows], [float(r[1]) for r in rows])
            fetched[family].append(lists)

    for k in KS:
        measured: dict[str, dict] = {}
        for family in FAMILIES:
            lines = ctx["lines"][family]
            per_line: list[dict[str, float]] = [{} for _ in lines]  # each line's ms, by list
            for field, expression in EXPRESSIONS.items():
                sql, marks = query(expression, k)
                asked = [(n, values(line)[field]) for n, line in enumerate(lines) if field in values(line)]
                for _, value in asked[: sizing.WARM_UP]:
                    conn.execute(sql, bind(marks, tenant_id, value)).fetchall()
                for n, value in asked:
                    started = time.perf_counter()
                    conn.execute(sql, bind(marks, tenant_id, value)).fetchall()
                    per_line[n][field] = (time.perf_counter() - started) * 1000
            found = {
                "ms": {field: [ms[field] for ms in per_line if field in ms] for field in EXPRESSIONS},
                "match": 0,  # lines whose applied match differs from today's full scan
                "five": Counter(),  # lines whose five recorded candidates differ, by the line's SKU kind
                "ties": 0,  # of those, lines with the same scores and other items among the equal ones
                "close": 0,  # lines whose K-th candidate is still similar
                "full": 0,  # lines the design's guard sends to the full scan
                "with_scan_ms": [],
                "with_scan_differs": 0,
            }
            rows = zip(lines, fetched[family], ctx["reference"][family])
            for n, (line, lists, reference) in enumerate(rows):
                ids = [i for listed, _ in lists.values() for i in listed[:k]]
                got = from_candidates(matching, line, catalog, ids)
                found["match"] += outcome(got) != outcome(reference)
                if recorded(got) != recorded(reference):
                    found["five"][line.sku_kind] += 1
                    found["ties"] += scores(got) == scores(reference)
                flags = [guard(similarities, k) for _, similarities in lists.values()]
                found["close"] += any(c for c, _ in flags)
                fell_back = any(f for _, f in flags)
                found["full"] += fell_back
                # The index first, and the faster full scan for the lines the guard sends on.
                final = ctx["fast"]["matches"][family][n] if fell_back else got
                found["with_scan_ms"].append(
                    sum(per_line[n].values()) + (ctx["fast"]["ms"][family][n] if fell_back else 0)
                )
                same = outcome(final) == outcome(reference) and recorded(final) == recorded(reference)
                found["with_scan_differs"] += not same
            measured[family] = found
        out += report_for_k(k, measured)

    if threshold is not None:
        passing = []
        for family in FAMILIES:
            sql, marks = count_above_threshold(EXPRESSIONS["description"])
            counts = [
                conn.execute(sql, bind(marks, tenant_id, line.description)).fetchone()[0]
                for line in ctx["lines"][family][:PARITY_LINES]
            ]
            passing.append(
                f"{family} median {int(statistics.median(counts))}, max {max(counts)}, "
                f"none on {sum(1 for c in counts if c == 0)}, "
                f"under {matching.MAX_CANDIDATES} on {sum(1 for c in counts if c < matching.MAX_CANDIDATES)}"
            )
        out.append("items above the threshold by description: " + " | ".join(passing))
    return out


def _drop(conn, variant: Variant) -> None:
    for statement in variant.creates.values():
        conn.execute(f"DROP INDEX IF EXISTS public.{index_name(statement)}")


def prepare(conn, sizes: list[int], seed: int, matching) -> dict:
    """Load the mix with no trigram index, draw the lines from the largest
    tenant, read its catalog as matching does, and run today's full scan on
    the parity lines: the reference everything else is compared with."""
    tenants, load_seconds = sizing.load_mix(conn, sizes, seed, annotate=False)
    conn.execute("ANALYZE items")
    rows = conn.execute("SELECT count(*) FROM items").fetchone()[0]
    tenant_id = tenants[sizes.index(max(sizes))]

    ctx: dict = {"matching": matching, "tenant_id": tenant_id, "lines": {}, "reference": {}, "rows": rows}
    for family, like in (("fastener", "NOT LIKE"), ("general", "LIKE")):
        sample = conn.execute(
            f"SELECT sku, description FROM items WHERE tenant_id = %s AND description {like} %s "
            "ORDER BY id LIMIT %s",
            (tenant_id, GENERAL_PREFIX + "%", TIMING_LINES),
        ).fetchall()
        ctx["lines"][family] = bench_lines([(sku, description) for sku, description in sample], seed)

    # Today's full scan: the catalog read, the comparison data, then each line.
    started = time.perf_counter()
    live = conn.execute(CATALOG_READ, (tenant_id,)).fetchall()
    ctx["read_seconds"] = time.perf_counter() - started
    started = time.perf_counter()
    ctx["catalog"] = matching.Catalog([matching.CatalogItem(*row) for row in live])
    ctx["catalog_seconds"] = time.perf_counter() - started
    scan_lines = []
    ctx["today_seconds"] = []
    for family in FAMILIES:
        taken, ctx["reference"][family] = [], []
        for number, line in enumerate(ctx["lines"][family][:PARITY_LINES], start=1):
            started = time.perf_counter()
            ctx["reference"][family].append(resolve(matching, line, ctx["catalog"]))
            taken.append(time.perf_counter() - started)
            if number % 10 == 0:
                print(f"   full scan, {family}: {number} lines done", flush=True)
        ctx["today_seconds"] += taken
        applied = sum(1 for match in ctx["reference"][family] if match.matched)
        scan_lines.append(
            f"{family} lines: {statistics.median(taken):.2f} s a line (median), {max(taken):.2f} s the "
            f"slowest, over {len(taken)} lines; the full scan applies a match on {applied} of them"
            if taken
            else f"{family} lines: none in this catalog"
        )
    ctx["today"] = [
        f"the {max(sizes)}-item tenant: catalog read from the database in {ctx['read_seconds']:.2f} s, "
        f"its exact-SKU table built in {ctx['catalog_seconds']:.2f} s (both once a document)",
        *scan_lines,
    ]
    ctx["loaded"] = (
        f"the load: {load_seconds:.0f} s for {sum(sizes)} rows with no trigram index; "
        f"{rows} items rows in the table"
    )
    return ctx


def run(database_url: str, mix: str, seed: int, names: str) -> int:
    import psycopg

    from docflow_core import matching

    if names.strip().lower() == "scan":
        import full_scan

        return full_scan.run(database_url, mix, seed)
    sizing.refuse_unless_local(database_url)
    sizes = catalog_gen.parse_mix(mix)
    asked = chosen(names)
    not_measured = [v for v in asked if len(sizes) < v.needs_tenants]
    asked = [v for v in asked if v not in not_measured]
    if notices(asked) > NOTICES_PER_STEP:
        raise ValueError(f"{len(asked)} variants is more than one run can report")
    failed = 0

    def emit(title: str, lines: list[str]) -> None:
        nonlocal failed
        sizing.emit(title, lines)
        length = len("\n".join(lines))
        if length > ANNOTATION_LIMIT:
            failed += 1
            message = f"{title}: {length} characters, and GitHub keeps {ANNOTATION_LIMIT}; its end is cut"
            print(message, flush=True)
            if os.environ.get("GITHUB_ACTIONS") == "true":
                print(f"::error title=catalog sizing: a report was cut::{message}", flush=True)

    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("SET search_path = public, extensions")
        conn.execute("SET statement_timeout = 0")
        version = conn.execute("SHOW server_version").fetchone()[0]
        block = int(conn.execute("SHOW block_size").fetchone()[0])
        for statement in sizing.statements(sizing.INDEX_SQL.read_text(encoding="utf-8")):
            if statement.startswith("create extension"):
                conn.execute(statement)
        try:
            conn.execute("create extension if not exists btree_gin with schema extensions")
        except psycopg.Error as error:
            # The GIN variant then fails on its own and says so; the others still run.
            print(f"btree_gin could not be created: {type(error).__name__}: {error}", flush=True)
        # A sizing run on this same throwaway database leaves the baseline's indexes behind.
        for variant in variants():
            _drop(conn, variant)

        emit(
            "variants: inputs",
            [
                f"mix {mix}: {len(sizes)} tenants, {sum(sizes)} items; seed {seed}; Postgres {version}",
                f"commit {os.environ.get('GITHUB_SHA', '')[:7] or 'not in CI'}; "
                f"runner {os.environ.get('RUNNER_OS', sys.platform)}",
                "variants: " + "; ".join(f"{v.name} ({v.what})" for v in asked),
                *(f"{v.name} is not measured: the mix has one tenant" for v in not_measured),
                f"candidates fetched from each list: {', '.join(map(str, KS))}; timings on {TIMING_LINES} "
                f"lines a family, parity on {PARITY_LINES}; no line carries its item's exact SKU",
                "loaded once with no trigram index; each variant's indexes are built on the loaded rows",
                LEGEND,
            ],
        )

        ctx = prepare(conn, sizes, seed, matching)
        rows = ctx["rows"]
        ctx["fast"] = measure_fast_scan(matching, ctx["catalog"], ctx["lines"], ctx["reference"])
        emit(
            # No comma in a title: GitHub reads one as the end of the title.
            "variants: today's full scan in Python on this runner",
            [*ctx["today"], *ctx["fast"]["report"], ctx["loaded"]],
        )

        for variant in asked:
            try:
                lines, pages = _built(conn, variant, rows, block)
                for threshold in variant.thresholds or (None,):
                    # One annotation for each threshold: together they would be cut.
                    title = variant.name if threshold is None else f"{variant.name} at {threshold}"
                    what = variant.what if threshold is None else f"{variant.what} of {threshold}"
                    measured = _measure(conn, variant, threshold, pages, ctx)
                    emit(f"variants: {title}", [what, *lines, *measured])
            except psycopg.Error as error:
                failed += 1
                message = f"{variant.name} ({variant.what}) was not measured: {type(error).__name__}: {error}"
                print(message, flush=True)
                if os.environ.get("GITHUB_ACTIONS") == "true":
                    text = " ".join(message.split()).replace("%", "%25")
                    print(f"::error title=catalog sizing: variants: {variant.name}::{text}", flush=True)
            finally:
                _drop(conn, variant)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--mix", required=True, help="one catalog size per tenant, e.g. 250000,100000x2,50000x7"
    )
    parser.add_argument("--seed", type=int, default=sizing.DEFAULT_SEED)
    parser.add_argument("--variants", default="all", help="all, or names with commas, e.g. baseline,siglen64")
    parser.add_argument("--database-url", default=os.environ.get("SUPERUSER_DATABASE_URL", ""))
    args = parser.parse_args(argv)
    if not args.database_url:
        print("No database: set SUPERUSER_DATABASE_URL (CI's local stack) or pass --database-url.")
        return 2
    try:
        return run(args.database_url, args.mix, args.seed, args.variants)
    except (sizing.NotALocalDatabase, ValueError) as refused:
        message = str(refused)
        print(message)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(
                f"::error title=catalog sizing: variants not run::{message.replace('%', '%25')}", flush=True
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
