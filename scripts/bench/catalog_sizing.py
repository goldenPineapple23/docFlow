"""
The catalog sizing job (Stage 4; D-199, D-200).

Seeds fake catalogs for a mix of tenants into a throwaway database, with the
two trigram indexes of scripts/bench/trigram_indexes.sql in place, and prints
what they cost: the table's size, each index's size, bytes per row, the load
and index build times, and how long the candidate query takes.

It answers one question before anything is loaded on staging: does the Stage
4 load fit under staging's database limit? (Staging is Supabase's Free plan,
500 MB.) Its sizes are the measurement. Its timings are this runner's, not
staging's, and they time the index query alone: the match timings of record
come from the matching benchmark, with the matching code.

    python scripts/bench/catalog_sizing.py --mix 250000,100000x2,50000x7

**It refuses any database that is not on this machine.** It runs in CI
against the runner's own Supabase stack (.github/workflows/catalog-sizing.yml)
and never against docflow-staging or production.

The order is the one a real tenant meets: the indexes exist first (as after
migration 0037), then the catalog arrives. Each index is then rebuilt, which
gives the build time at that many rows (what applying the migration to a
loaded table would cost) and the size a freshly built index has.

In CI every figure is also a GitHub annotation, because CI's job logs can't
be read from the development machine and annotations can (RUNBOOK 1.6).

A run that is cut short still says how far it got. The inputs are printed
before the load, and each tenant gets a line as its catalog finishes (rows
and seconds). GitHub keeps ten notices from one step, so in annotations
those lines travel together: after the first tenant, and each time the load
passes a quarter of its rows.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import catalog_gen  # noqa: E402

INDEX_SQL = HERE / "trigram_indexes.sql"
TRIGRAM_INDEXES = ("idx_items_trgm_description", "idx_items_trgm_sku")
CANDIDATES = 50  # K in the design (A2): candidates asked of each index per line
DEFAULT_SEED = 20261011
DEFAULT_LINES = 200
WARM_UP = 5
# GitHub keeps ten notices from one step. Four are the results; the load's
# progress takes at most five (the first tenant, then each quarter).
PROGRESS_MARKS = (0.25, 0.5, 0.75, 1.0)
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

DESCRIPTION_QUERY = (
    "SELECT id FROM items WHERE tenant_id = %s AND deleted_at IS NULL "
    f"ORDER BY lower(description) <-> %s::text LIMIT {CANDIDATES}"
)
SKU_QUERY = (
    "SELECT id FROM items WHERE tenant_id = %s AND deleted_at IS NULL "
    f"ORDER BY lower(regexp_replace(sku, '[^A-Za-z0-9]', '', 'g')) <-> %s::text LIMIT {CANDIDATES}"
)


class NotALocalDatabase(Exception):
    """The database URL points somewhere other than this machine."""


def refuse_unless_local(database_url: str) -> None:
    host = urlsplit(database_url).hostname
    if host not in LOCAL_HOSTS:
        raise NotALocalDatabase(
            "The catalog sizing job only runs against a database on this machine "
            f"(127.0.0.1 or localhost). This one is on {host or 'an unnamed host'}, so nothing was done."
        )


def statements(sql_text: str) -> list[str]:
    """The statements of a SQL file, comment lines removed, each on one line
    with single spaces: the form the sizing job runs and 0037 is compared in."""
    code = "\n".join(line for line in sql_text.splitlines() if not line.lstrip().startswith("--"))
    return [" ".join(part.split()) for part in code.split(";") if part.strip()]


def sku_key(sku: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", sku).lower()


def order_lines(rows: list[tuple[str, str]], seed: int) -> list[tuple[str, str]]:
    """What a buyer might write for each sampled item: the description and
    SKU as they are, or one token dropped, two tokens swapped, one SKU
    character wrong. Lowercased, as the query sends them."""
    rng = random.Random(f"docflow-bench-lines:{seed}")
    out = []
    for number, (sku, description) in enumerate(rows):
        tokens = description.split()
        if number % 3 == 1 and len(tokens) > 2:
            tokens.pop(rng.randrange(len(tokens)))
        elif number % 3 == 2 and len(tokens) > 2:
            i = rng.randrange(len(tokens) - 1)
            tokens[i], tokens[i + 1] = tokens[i + 1], tokens[i]
        key = sku_key(sku)
        if number % 2 == 1 and len(key) > 3:
            i = rng.randrange(len(key))
            key = key[:i] + ("x" if key[i] != "x" else "y") + key[i + 1 :]
        out.append((" ".join(tokens).lower(), key))
    return out


def megabytes(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def progress_due(loaded_before: int, loaded: int, total: int) -> bool:
    """Whether the tenant lines gathered so far become an annotation now:
    after the first tenant, and each time the load passes a quarter of its
    rows. The last tenant always does."""
    return loaded_before == 0 or any(loaded_before < mark * total <= loaded for mark in PROGRESS_MARKS)


def tenant_line(
    number: int, tenants: int, rows: int, seconds: float, loaded: int, total: int, since: float
) -> str:
    return (
        f"tenant {number} of {tenants}: {rows} rows in {seconds:.0f} s "
        f"({rows / max(seconds, 0.001):.0f} rows a second); "
        f"{loaded} of {total} rows loaded, {since:.0f} s since the load began"
    )


def _annotate(title: str, lines: list[str]) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        body = "%0A".join(line.replace("%", "%25") for line in lines)
        print(f"::notice title=catalog sizing: {title}::{body}", flush=True)


def _summarise(text: str) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(text)


def _heading(title: str) -> None:
    print(f"== catalog sizing: {title}", flush=True)
    _summarise(f"### catalog sizing: {title}\n\n")


def _line(line: str) -> None:
    print(f"   {line}", flush=True)
    _summarise(f"- {line}\n")


def emit(title: str, lines: list[str]) -> None:
    _heading(title)
    for line in lines:
        _line(line)
    _summarise("\n")
    _annotate(title, lines)


def _sizes(conn) -> dict[str, int]:
    found = {
        "table (rows, TOAST and maps)": conn.execute("SELECT pg_table_size('public.items')").fetchone()[0],
        "table and every index": conn.execute("SELECT pg_total_relation_size('public.items')").fetchone()[0],
        "database": conn.execute("SELECT pg_database_size(current_database())").fetchone()[0],
    }
    for name, size in conn.execute(
        "SELECT c.relname, pg_relation_size(c.oid) FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
        "WHERE i.indrelid = 'public.items'::regclass ORDER BY c.relname"
    ):
        found[f"index {name}"] = size
    return found


def _size_lines(after: dict[str, int], before: dict[str, int], rows: int) -> list[str]:
    lines = []
    for name, size in after.items():
        grown = size - before.get(name, 0)
        lines.append(f"{name}: {megabytes(grown)} ({grown} bytes), {grown / rows:.0f} bytes a row")
    return lines


def _time_queries(conn, query: str, tenant_id: uuid.UUID, values: list[str]) -> list[float]:
    taken = []
    for value in values:
        started = time.perf_counter()
        conn.execute(query, (tenant_id, value)).fetchall()
        taken.append((time.perf_counter() - started) * 1000)
    return taken


def _timing_line(label: str, taken: list[float]) -> str:
    timed = taken[WARM_UP:] or taken
    return (
        f"{label}: p50 {percentile(timed, 0.5):.1f} ms, p95 {percentile(timed, 0.95):.1f} ms, "
        f"max {max(timed):.1f} ms over {len(timed)} lines (first query, cold: {taken[0]:.1f} ms)"
    )


def _plan(conn, query: str, tenant_id: uuid.UUID, value: str) -> str:
    import psycopg

    with psycopg.ClientCursor(conn) as cur:
        cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + query, (tenant_id, value))
        rows = [row[0].strip() for row in cur.fetchall()]
    keep = [r for r in rows if re.search(r"Scan|Execution Time|Buffers", r)]
    return " | ".join(keep[:4])


def load_mix(conn, sizes: list[int], seed: int, annotate: bool = True) -> tuple[list[uuid.UUID], float]:
    """One new tenant and its catalog per size, in the order given. Returns
    the tenants' ids and the seconds the load took. Each tenant gets a line
    as it finishes; `annotate=False` keeps those lines out of the
    annotations, for a job that needs its ten notices for its results."""
    total = sum(sizes)
    tenants: list[uuid.UUID] = []
    _heading("the load, tenant by tenant")
    waiting: list[str] = []  # tenant lines not yet in an annotation
    loaded_rows = 0
    load_started = time.perf_counter()
    for number, size in enumerate(sizes, start=1):
        tenant_started = time.perf_counter()
        tenant_id = uuid.uuid4()
        tenants.append(tenant_id)
        conn.execute(
            "INSERT INTO tenants (id, name, status, onboarding_status, created_at, updated_at, "
            "status_changed_at) VALUES (%s, %s, 'active', 'tenant_created', now(), now(), now())",
            (tenant_id, catalog_gen.tenant_name(number, size)),
        )
        with conn.cursor() as cur, cur.copy(
            "COPY items (tenant_id, sku, description, unit_of_measure, barcode, external_id, raw_data) "
            "FROM STDIN"
        ) as copy:
            for item in catalog_gen.catalog(seed, number, size):
                copy.write_row(
                    (
                        tenant_id,
                        item.sku,
                        item.description,
                        item.unit_of_measure,
                        item.barcode,
                        item.external_id,
                        json.dumps(item.raw),
                    )
                )
        # The COPY has ended and committed: this tenant's rows are in, indexes and all.
        now = time.perf_counter()
        loaded_before, loaded_rows = loaded_rows, loaded_rows + size
        line = tenant_line(
            number, len(sizes), size, now - tenant_started, loaded_rows, total, now - load_started
        )
        _line(line)
        waiting.append(line)
        if annotate and progress_due(loaded_before, loaded_rows, total):
            _annotate(f"loaded {loaded_rows} of {total} rows", waiting)
            waiting = []
    _summarise("\n")
    return tenants, time.perf_counter() - load_started


def run(database_url: str, mix: str, seed: int, lines: int) -> None:
    import psycopg

    refuse_unless_local(database_url)
    sizes = catalog_gen.parse_mix(mix)
    total = sum(sizes)

    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("SET search_path = public, extensions")
        # A large load and an index build are long statements by design.
        conn.execute("SET statement_timeout = 0")
        version = conn.execute("SHOW server_version").fetchone()[0]
        rows_before = conn.execute("SELECT count(*) FROM items").fetchone()[0]

        for statement in statements(INDEX_SQL.read_text(encoding="utf-8")):
            conn.execute(statement)
        before = _sizes(conn)

        emit(
            "inputs",
            [
                f"mix {mix}: {len(sizes)} tenants, {total} items; seed {seed}",
                f"largest tenant {max(sizes)} items; Postgres {version}; "
                f"items rows before the run: {rows_before}",
                f"commit {os.environ.get('GITHUB_SHA', '')[:7] or 'not in CI'}; "
                f"runner {os.environ.get('RUNNER_OS', sys.platform)}",
                "indexes created first, then the catalogs loaded (as after migration 0037)",
            ],
        )

        tenants, load_seconds = load_mix(conn, sizes, seed)
        conn.execute("ANALYZE items")
        rows_added = conn.execute("SELECT count(*) FROM items").fetchone()[0] - rows_before
        if rows_added != total:
            raise SystemExit(f"seeded {rows_added} rows, expected {total}: the figures would be wrong")
        loaded = _sizes(conn)

        emit(
            "sizes as loaded",
            _size_lines(loaded, before, total)
            + [f"load: {load_seconds:.0f} s for {total} rows with both trigram indexes in place"],
        )

        # The candidate query, on the largest tenant.
        largest = tenants[sizes.index(max(sizes))]
        sample = conn.execute(
            "SELECT sku, description FROM items WHERE tenant_id = %s ORDER BY id LIMIT %s", (largest, lines)
        ).fetchall()
        asked = order_lines([(sku, description) for sku, description in sample], seed)
        by_description = _time_queries(conn, DESCRIPTION_QUERY, largest, [d for d, _ in asked])
        by_sku = _time_queries(conn, SKU_QUERY, largest, [s for _, s in asked])
        emit(
            "candidate query on the largest tenant",
            [
                f"the {CANDIDATES} nearest items, asked of the {max(sizes)}-item tenant; "
                "this runner's times, not staging's",
                _timing_line("by description", by_description),
                _timing_line("by SKU", by_sku),
                "plan by description: " + _plan(conn, DESCRIPTION_QUERY, largest, asked[0][0]),
                "plan by SKU: " + _plan(conn, SKU_QUERY, largest, asked[0][1]),
            ],
        )

        # Each index built fresh at this many rows.
        rebuilt = []
        for name in TRIGRAM_INDEXES:
            started = time.perf_counter()
            conn.execute(f"REINDEX INDEX {name}")
            seconds = time.perf_counter() - started
            size = conn.execute("SELECT pg_relation_size(%s::regclass)", (f"public.{name}",)).fetchone()[0]
            rebuilt.append(
                f"{name}: built in {seconds:.0f} s; {megabytes(size)} ({size} bytes), "
                f"{size / (rows_before + total):.0f} bytes a row"
            )
        after_rebuild = _sizes(conn)
        emit(
            "each trigram index rebuilt at this many rows",
            rebuilt
            + [
                "table and every index after the rebuild: "
                f"{megabytes(after_rebuild['table and every index'] - before['table and every index'])}",
                "database after the rebuild: "
                f"{megabytes(after_rebuild['database'])} in all, "
                f"{megabytes(after_rebuild['database'] - before['database'])} of it from this run",
            ],
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--mix", required=True, help="one catalog size per tenant, e.g. 250000,100000x2,50000x7"
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--lines", type=int, default=DEFAULT_LINES, help="order lines to time the query with")
    parser.add_argument("--database-url", default=os.environ.get("SUPERUSER_DATABASE_URL", ""))
    args = parser.parse_args(argv)
    if not args.database_url:
        print("No database: set SUPERUSER_DATABASE_URL (CI's local stack) or pass --database-url.")
        return 2
    try:
        run(args.database_url, args.mix, args.seed, args.lines)
    except (NotALocalDatabase, ValueError) as refused:
        message = str(refused)
        print(message)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::error title=catalog sizing: not run::{message.replace('%', '%25')}", flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
