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
what a fallback costs and what top-K must beat.

**It refuses any database that is not on this machine**, as the sizing job
does. Every figure is a GitHub annotation (RUNBOOK 1.6). GitHub keeps ten
notices from one step: the inputs, the full scan and one per variant.
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
GENERAL_PREFIX = "Acme Test "  # catalog_gen's general families; the rest are fasteners
FAMILIES = ("fastener", "general")
SKU_KINDS = ("one character wrong", "no SKU", "the buyer's own number")
NOTICES_PER_STEP = 10

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


def spread(values: list[float]) -> str:
    if not values:
        return "no lines"
    return f"{sizing.percentile(values, 0.5):.1f}/{sizing.percentile(values, 0.95):.1f}/{max(values):.1f}"


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
            f"{field} index {name}: built in {seconds:.0f} s; {sizing.megabytes(size)} ({size} bytes), "
            f"{size / rows:.0f} bytes a row, {pages[field]} pages"
        )
    conn.execute("ANALYZE items")
    return lines, pages


def _measure(conn, variant: Variant, threshold: float | None, pages: dict[str, int], ctx: dict) -> list[str]:
    matching, catalog, tenant_id = ctx["matching"], ctx["catalog"], ctx["tenant_id"]
    query = nearest if threshold is None else above_threshold
    prefix = "" if threshold is None else f"threshold {threshold}: "
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
        parts.append(f"{field} {scan}, buffers {' / '.join(per_family)} of {pages[field]} index pages")
    out.append(f"{prefix}plan fetching {KS[0]}: " + " | ".join(parts))

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
        timing, parity, guarded = [], [], []
        for family in FAMILIES:
            lines = ctx["lines"][family]
            taken = {}
            for field, expression in EXPRESSIONS.items():
                sql, marks = query(expression, k)
                asked = [values(line)[field] for line in lines if field in values(line)]
                for value in asked[: sizing.WARM_UP]:
                    conn.execute(sql, bind(marks, tenant_id, value)).fetchall()
                taken[field] = []
                for value in asked:
                    started = time.perf_counter()
                    conn.execute(sql, bind(marks, tenant_id, value)).fetchall()
                    taken[field].append((time.perf_counter() - started) * 1000)
            timing.append(f"{family} description {spread(taken['description'])}, SKU {spread(taken['SKU'])}")

            match_differs, candidates_differ, close, full_scan = 0, Counter(), 0, 0
            for line, lists, reference in zip(lines, fetched[family], ctx["reference"][family]):
                ids = [i for found, _ in lists.values() for i in found[:k]]
                got = from_candidates(matching, line, catalog, ids)
                if outcome(got) != outcome(reference):
                    match_differs += 1
                if recorded(got) != recorded(reference):
                    candidates_differ[line.sku_kind] += 1
                flags = [guard(similarities, k) for _, similarities in lists.values()]
                close += any(c for c, _ in flags)
                full_scan += any(f for _, f in flags)
            by_kind = ", ".join(f"{kind}: {candidates_differ[kind]}" for kind in SKU_KINDS)
            parity.append(
                f"{family} applied match differs on {match_differs}, the five candidates differ on "
                f"{sum(candidates_differ.values())} ({by_kind})"
            )
            guarded.append(
                f"{family} the {k}th is still at {GUARD_SIMILARITY} or more on {close} lines, "
                f"the {GUARD_FACTOR * k}th too on {full_scan} (full scan by the design's guard)"
            )
        out.append(
            f"{prefix}fetching {k}, ms p50/p95/max over {TIMING_LINES} lines a family: " + " | ".join(timing)
        )
        out.append(f"{prefix}fetching {k}, parity on {PARITY_LINES} lines a family: " + " | ".join(parity))
        out.append(f"{prefix}fetching {k}, guard on {PARITY_LINES} lines a family: " + " | ".join(guarded))

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
                f"none on {sum(1 for c in counts if c == 0)} lines, "
                f"fewer than {matching.MAX_CANDIDATES} on "
                f"{sum(1 for c in counts if c < matching.MAX_CANDIDATES)}"
            )
        out.append(f"{prefix}items above the threshold by description: " + " | ".join(passing))
    return out


def _drop(conn, variant: Variant) -> None:
    for statement in variant.creates.values():
        conn.execute(f"DROP INDEX IF EXISTS public.{index_name(statement)}")


def run(database_url: str, mix: str, seed: int, names: str) -> int:
    import psycopg

    from docflow_core import matching

    sizing.refuse_unless_local(database_url)
    sizes = catalog_gen.parse_mix(mix)
    asked = chosen(names)
    if len(asked) + 2 > NOTICES_PER_STEP:
        raise ValueError(f"{len(asked)} variants is more than one run can report")
    failed = 0

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

        sizing.emit(
            "variants: inputs",
            [
                f"mix {mix}: {len(sizes)} tenants, {sum(sizes)} items; seed {seed}; Postgres {version}",
                f"commit {os.environ.get('GITHUB_SHA', '')[:7] or 'not in CI'}; "
                f"runner {os.environ.get('RUNNER_OS', sys.platform)}",
                "variants: " + "; ".join(f"{v.name} ({v.what})" for v in asked),
                f"candidates fetched from each list: {', '.join(map(str, KS))}; timings on {TIMING_LINES} "
                f"lines a family, parity on {PARITY_LINES}; no line carries its item's exact SKU",
                "loaded once with no trigram index; each variant's indexes are built on the loaded rows",
            ],
        )

        tenants, load_seconds = sizing.load_mix(conn, sizes, seed, annotate=False)
        conn.execute("ANALYZE items")
        rows = conn.execute("SELECT count(*) FROM items").fetchone()[0]
        tenant_id = tenants[sizes.index(max(sizes))]

        ctx: dict = {"matching": matching, "tenant_id": tenant_id, "lines": {}, "reference": {}}
        for family, like in (("fastener", "NOT LIKE"), ("general", "LIKE")):
            sample = conn.execute(
                f"SELECT sku, description FROM items WHERE tenant_id = %s AND description {like} %s "
                "ORDER BY id LIMIT %s",
                (tenant_id, GENERAL_PREFIX + "%", TIMING_LINES),
            ).fetchall()
            ctx["lines"][family] = bench_lines([(sku, description) for sku, description in sample], seed)

        # Today's full scan: the catalog read, the comparison data, then each line.
        started = time.perf_counter()
        live = conn.execute(
            "SELECT id, sku, description, unit_of_measure FROM items "
            "WHERE tenant_id = %s AND deleted_at IS NULL",
            (tenant_id,),
        ).fetchall()
        read_seconds = time.perf_counter() - started
        started = time.perf_counter()
        ctx["catalog"] = matching.Catalog([matching.CatalogItem(*row) for row in live])
        catalog_seconds = time.perf_counter() - started
        scan_lines = []
        for family in FAMILIES:
            taken, ctx["reference"][family] = [], []
            for number, line in enumerate(ctx["lines"][family][:PARITY_LINES], start=1):
                started = time.perf_counter()
                ctx["reference"][family].append(resolve(matching, line, ctx["catalog"]))
                taken.append(time.perf_counter() - started)
                if number % 10 == 0:
                    print(f"   full scan, {family}: {number} lines done", flush=True)
            applied = sum(1 for match in ctx["reference"][family] if match.matched)
            scan_lines.append(
                f"{family} lines: {statistics.median(taken):.2f} s a line (median), {max(taken):.2f} s the "
                f"slowest, over {len(taken)} lines; the full scan applies a match on {applied} of them"
                if taken
                else f"{family} lines: none in this catalog"
            )
        sizing.emit(
            # No comma in a title: GitHub reads one as the end of the title.
            "variants: today's full scan in Python on this runner",
            [
                f"the {max(sizes)}-item tenant: catalog read from the database in {read_seconds:.2f} s, "
                f"its exact-SKU table built in {catalog_seconds:.2f} s (both once a document)",
                *scan_lines,
                f"the load: {load_seconds:.0f} s for {sum(sizes)} rows with no trigram index; "
                f"{rows} items rows in the table",
            ],
        )

        for variant in asked:
            if len(sizes) < variant.needs_tenants:
                sizing.emit(
                    f"variants: {variant.name}", [f"{variant.what}: not measured, the mix has one tenant"]
                )
                continue
            try:
                lines, pages = _built(conn, variant, rows, block)
                for threshold in variant.thresholds or (None,):
                    lines += _measure(conn, variant, threshold, pages, ctx)
                sizing.emit(f"variants: {variant.name}", [variant.what, *lines])
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
