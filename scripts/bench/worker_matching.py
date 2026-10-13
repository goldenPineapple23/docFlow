"""
Matching, timed where a document's matching runs (Stage 4; D-201, D-202).

The other bench scripts measure on a CI runner, against the runner's own
database. This one is run **inside the worker's machine**, as the worker's
own login, against the database that machine is configured for. It answers
what the runner cannot: how long matching takes on the worker's one shared
core, and what it holds in memory there.

    python worker_matching.py --tenant-id <the bench tenant's id>

It does what the document task does and nothing else: one tenant session,
`matching.load_catalog`, then `matching.resolve_line` for each line. It
measures, for that one tenant:

* the catalog read (`load_catalog`: the query, the items, the exact-SKU
  table) and the keys (`Catalog.scan_keys`), each once a document;
* each unmatched line's time, for fastener and general lines apart;
* a 40-line order from nothing, several times: a fresh `load_catalog`, the
  keys built at the first line that needs them, 40 lines;
* parity with the full scan on a few lines (the 100-line parity is the CI
  run's; the full scan is slow, and slower on a shared core);
* this process's memory after each stage, and its peak.

**It reads and never writes.** Every transaction is set READ ONLY before
anything else runs in it. **It refuses any tenant that is not a bench
tenant** ("Acme Test Bench ..."), so it cannot be pointed at a customer's
catalog. It prints counts, timings and sizes only: no SKU, no description,
no connection string (Section 7.10).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import statistics
import sys
import time
from pathlib import Path
from uuid import UUID

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import index_variants as v  # noqa: E402

BENCH_TENANT_PREFIX = "Acme Test Bench "  # catalog_gen.tenant_name
ORDER_LINES = 40  # the pass mark's order: 4 s for 40 lines
LINE_MARK_MS = 100.0  # the pass mark for one unmatched line, at p95
ORDER_MARK_SECONDS = 4.0


class Refused(RuntimeError):
    """The run was refused before it measured anything."""


def refuse_unless_bench_tenant(name: str | None) -> None:
    if name is None:
        raise Refused("no such tenant is visible to this login")
    if not name.startswith(BENCH_TENANT_PREFIX):
        raise Refused(
            f'this only measures a bench tenant (its name starts "{BENCH_TENANT_PREFIX.strip()}"); '
            "the tenant given is not one"
        )


def sample(items: list, count: int) -> dict[str, list[tuple[str, str]]]:
    """`count` items of each family to write lines from: the same items on
    every run, whatever order the database returned the catalog in (the
    order of md5(sku), as the CI bench draws them)."""
    ordered = sorted(items, key=lambda item: (hashlib.md5(item.sku.encode()).hexdigest(), item.sku))
    rows = [(item.sku, item.description or "") for item in ordered]
    general = [row for row in rows if row[1].startswith(v.GENERAL_PREFIX)]
    fasteners = [row for row in rows if not row[1].startswith(v.GENERAL_PREFIX)]
    return {"fastener": fasteners[:count], "general": general[:count]}


def memory() -> str:
    """This process's resident memory now and at its peak (Linux)."""
    try:
        status = Path("/proc/self/status").read_text(encoding="utf-8")
    except OSError:
        return "memory not read (no /proc on this machine)"
    found = {}
    for line in status.splitlines():
        name, _, value = line.partition(":")
        if name in ("VmRSS", "VmHWM"):
            found[name] = int(value.split()[0]) / 1024
    return f"resident {found.get('VmRSS', 0):.0f} MiB, peak {found.get('VmHWM', 0):.0f} MiB"


def time_lines(matching, catalog, lines: list) -> list[float]:
    taken = []
    for line in lines:
        started = time.perf_counter()
        v.product(matching, line, catalog)
        taken.append((time.perf_counter() - started) * 1000)
    return taken


def parity(matching, catalog, lines: list) -> tuple[int, list[float]]:
    """(lines whose stored result differs from the full scan's, the full
    scan's seconds a line)."""
    differ, seconds = 0, []
    for line in lines:
        started = time.perf_counter()
        reference = v.resolve(matching, line, catalog)
        seconds.append(time.perf_counter() - started)
        fast = v.product(matching, line, catalog)
        differ += v.outcome(fast) != v.outcome(reference) or v.recorded(fast) != v.recorded(reference)
    return differ, seconds


def verdict(value: float, mark: float, unit: str) -> str:
    return f"{'within' if value <= mark else 'OVER'} the {mark:g} {unit} mark"


def read_catalog(db, matching, text, tenant_id: UUID):
    """One read-only tenant session, as the document task opens one."""
    with db.tenant_session(tenant_id) as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        name = session.execute(
            text("SELECT name FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
        ).scalar()
        refuse_unless_bench_tenant(name)
        started = time.perf_counter()
        catalog = matching.load_catalog(session, tenant_id)
        return catalog, time.perf_counter() - started


def run(tenant_id: UUID, lines_a_family: int, parity_lines: int, orders: int, seed: int) -> int:
    from sqlalchemy import text

    from docflow_core import db, matching

    db.use_own_login("worker")
    checked = db.verify_logins(("worker",))
    print(f"login check passed as docflow_worker: {checked == ['worker']}", flush=True)
    print(
        f"machine: {platform.system()} {platform.machine()}, {os.cpu_count()} cores; "
        f"Python {platform.python_version()}; at the start: {memory()}",
        flush=True,
    )

    catalog, read_seconds = read_catalog(db, matching, text, tenant_id)
    print(
        f"the catalog: {len(catalog)} live items; load_catalog (the read, the items, the exact-SKU table) "
        f"{read_seconds:.2f} s; then: {memory()}",
        flush=True,
    )
    if len(catalog) <= matching.FAST_SCAN_KEEP:
        raise Refused(
            f"this catalog has {len(catalog)} items: {matching.FAST_SCAN_KEEP} or fewer take the full scan, "
            "so there is nothing to measure"
        )

    started = time.perf_counter()
    catalog.scan_keys()
    keys_seconds = time.perf_counter() - started
    print(
        f"the keys (once a document, at the first line that needs them): {keys_seconds:.2f} s; "
        f"then: {memory()}",
        flush=True,
    )

    drawn = sample(catalog.items, lines_a_family)
    lines = {family: v.bench_lines(rows, seed) for family, rows in drawn.items()}
    every: list[float] = []
    for family in v.FAMILIES:
        if not lines[family]:
            print(f"{family} lines: none in this catalog")
            continue
        taken = time_lines(matching, catalog, lines[family])
        every += taken
        p95 = v.sizing.percentile(taken, 0.95)
        print(
            f"{family} lines, ms a line p50/p95/max over {len(taken)}: {v.spread(taken)}; "
            f"p95 {verdict(p95, LINE_MARK_MS, 'ms')}",
            flush=True,
        )

    order = [line for family in v.FAMILIES for line in lines[family]][
        :: max(1, 2 * lines_a_family // ORDER_LINES)
    ]
    order = order[:ORDER_LINES]
    del catalog
    seconds = []
    for _ in range(orders):
        started = time.perf_counter()
        fresh, _read = read_catalog(db, matching, text, tenant_id)
        for line in order:
            v.product(matching, line, fresh)
        seconds.append(time.perf_counter() - started)
        del fresh
    if seconds:
        print(
            f"a {len(order)}-line order of unmatched lines from nothing (load_catalog, the keys, the lines), "
            f"{orders} times: {', '.join(f'{s:.2f}' for s in seconds)} s; the slowest "
            f"{verdict(max(seconds), ORDER_MARK_SECONDS, 's')}; then: {memory()}",
            flush=True,
        )

    if parity_lines:
        catalog, _read = read_catalog(db, matching, text, tenant_id)
        for family in v.FAMILIES:
            some = lines[family][:parity_lines]
            if not some:
                continue
            differ, full = parity(matching, catalog, some)
            print(
                f"{family} lines, parity with the full scan on {len(some)}: {differ} differ; the full scan "
                f"{statistics.median(full):.1f} s a line here",
                flush=True,
            )
            if differ:
                print("PARITY FAILED: the product's result differs from the full scan's", flush=True)
                return 1
    print(f"at the end: {memory()}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tenant-id", type=UUID, required=True, help="the bench tenant to measure")
    parser.add_argument("--lines", type=int, default=100, help="lines timed for each family")
    parser.add_argument(
        "--parity-lines", type=int, default=3, help="lines a family also run through the full scan"
    )
    parser.add_argument("--orders", type=int, default=3, help="how many times the 40-line order is run")
    parser.add_argument("--seed", type=int, default=20261011)
    args = parser.parse_args(argv)
    try:
        return run(args.tenant_id, args.lines, args.parity_lines, args.orders, args.seed)
    except Refused as refusal:
        print(f"worker_matching refused: {refusal}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
