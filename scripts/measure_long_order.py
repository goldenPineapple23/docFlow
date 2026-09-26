"""
Measure how long an order one extraction call can read (review M1; D-158
follow-up 2: "measure the real ceiling with a fake order of 300+ lines, before
and after streaming").

Builds a fake purchase order with N lines -- deterministic, every name
unmistakably fake (CLAUDE.md Section 0 rule 4) -- sends it through the real
`docflow_core.extraction.extract_document` exactly as the worker does, and
reports:

  * the outcome (read, or the catalog code: DOC-020 means the answer hit
    EXTRACTION_MAX_TOKENS and nothing was kept);
  * input and output tokens, cost, wall-clock time, output tokens per second;
  * how many lines came back, and how many match the printed order exactly
    (SKU, description, quantity, unit, unit price, line total) -- a read that
    finishes but gets lines wrong is not a pass.

Each run is one real, paid call to the pinned model. Usage, from the repo root,
with ANTHROPIC_API_KEY in .env:

    apps/api/.venv/Scripts/python.exe scripts/measure_long_order.py 300
    apps/api/.venv/Scripts/python.exe scripts/measure_long_order.py 150 300 --json out.json

`--print-order N` writes the generated order to stdout and makes no call.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages" / "core"))

_PRODUCTS = [
    ("Test Paper Cups 12oz (1000ct)", "BOX"),
    ("Test Colombian Whole Bean 5lb", "CS"),
    ("Test Vanilla Syrup 750ml", "EA"),
    ("Test Oat Milk Barista 32oz", "CS"),
    ("Test Compostable Lids 12-16oz", "BOX"),
    ("Test Hot Sleeve Kraft (1200ct)", "CS"),
    ("Test Espresso Blend Decaf 2lb", "BG"),
    ("Test Chai Concentrate 1gal", "EA"),
    ("Test Stir Sticks Birch 7in", "BOX"),
    ("Test Napkins 2-ply White (6000ct)", "CS"),
]


def build_order(n_lines: int, seed: int = 2291) -> tuple[str, list[dict[str, str]], str]:
    """The printed order, its lines as printed, and the printed order total."""
    rng = random.Random(seed + n_lines)
    lines: list[dict[str, str]] = []
    for i in range(1, n_lines + 1):
        description, unit = _PRODUCTS[(i - 1) % len(_PRODUCTS)]
        quantity = rng.randint(1, 48)
        unit_price = Decimal(rng.randint(150, 9900)) / 100
        lines.append(
            {
                "sku": f"TST-{i:04d}",
                "description": f"{description} #{i}",
                "quantity": str(quantity),
                "unit": unit,
                "unit_price": f"{unit_price:.2f}",
                "line_total": f"{unit_price * quantity:.2f}",
            }
        )
    total = sum((Decimal(line["line_total"]) for line in lines), Decimal(0))
    rows = "\n".join(
        f"{line['sku']:<10}  {line['description']:<42} {line['quantity']:>4}  {line['unit']:<4} "
        f"{'$' + line['unit_price']:>10}  {'$' + line['line_total']:>11}"
        for line in lines
    )
    text = (
        "PURCHASE ORDER\n\n"
        "Acme Test Long Order Cafe\n"
        "200 Test Avenue, Portland, OR 97204\n"
        "orders@acmetestlongorder.example\n\n"
        f"PO Number: LONG-{n_lines:04d}\n"
        "Date: April 2, 2026\n"
        "Terms: Net 30\n\n"
        f"{'ITEM':<10}  {'DESCRIPTION':<42} {'QTY':>4}  {'UOM':<4} {'UNIT PRICE':>9}  {'TOTAL':>11}\n"
        f"{rows}\n\n"
        f"{'ORDER TOTAL:':>74}  ${total:,.2f}\n"
    )
    return text, lines, f"{total:.2f}"


def _plain(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return f"{value:.2f}" if value == value.quantize(Decimal("0.01")) else str(value)
    return str(value)


def measure(client, n_lines: int) -> dict:
    from docflow_core.extraction import (
        EXTRACTION_MAX_TOKENS,
        EXTRACTION_MODEL,
        build_text_content,
        extract_document,
    )

    text, expected, total = build_order(n_lines)
    started = time.monotonic()
    result = extract_document(client, build_text_content(text))
    seconds = time.monotonic() - started

    exact = 0
    if result.ok:
        by_sku = {line["sku"]: line for line in expected}
        for line in result.lines:
            want = by_sku.get(line.get("sku"))
            if want is None:
                continue
            got = {
                "sku": line.get("sku"),
                "description": line.get("description"),
                "quantity": str(int(line["quantity"])) if line.get("quantity") is not None else None,
                "unit": line.get("unit"),
                "unit_price": _plain(line.get("unit_price")),
                "line_total": _plain(line.get("line_total")),
            }
            exact += got == want
    return {
        "lines_printed": n_lines,
        "model": EXTRACTION_MODEL,
        "max_tokens": EXTRACTION_MAX_TOKENS,
        "outcome": "read" if result.ok else result.error_code,
        "lines_returned": len(result.lines) if result.ok else 0,
        "lines_exact": exact,
        "order_total_exact": result.ok and _plain(result.header.get("order_total")) == total,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": f"{result.est_cost_usd:.4f}" if result.est_cost_usd is not None else None,
        "seconds": round(seconds, 1),
        "output_tokens_per_second": round((result.output_tokens or 0) / seconds, 1) if seconds else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("lines", nargs="*", type=int)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--print-order", type=int)
    args = parser.parse_args()

    if args.print_order:
        print(build_order(args.print_order)[0])
        return 0

    import anthropic
    from docflow_core.config import get_settings

    settings = get_settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set.")
        return 1
    # No retries: a measurement reports what one call does, and a retried
    # dropped connection would be paid for again.
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=0)
    results = []
    for n in args.lines:
        row = measure(client, n)
        results.append(row)
        print(json.dumps(row), flush=True)
    if args.json:
        args.json.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
