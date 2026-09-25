"""
Capture the recorded responses the example-prompting tests replay in CI
(apps/api/tests/test_example_prompting_golden.py; Section 7.13, D-141).

Makes two real calls to the pinned extraction model -- the golden fixture and
the contamination document, each with the three past examples -- and writes
the model's structured output, exactly as returned, next to the fixtures.
Re-run it whenever the extraction prompt, the example addendum, the schema or
the model ID changes (Section 7.1), then run the live tests too.

Usage, from the repo root, with ANTHROPIC_API_KEY in .env:

    apps/api/.venv/Scripts/python.exe scripts/record_example_fixtures.py

Refuses to write a response that fails its own test's assertions: a recorded
fixture that encodes a wrong answer would make CI green for the wrong reason.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

import anthropic
from docflow_core.config import get_settings
from docflow_core.extraction import (
    EXTRACTION_MODEL,
    build_text_content,
    extract_document,
)
from tests import test_example_prompting_golden as t


def main() -> int:
    settings = get_settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set.")
        return 1
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    examples = t.load_examples()

    jobs = [
        (t.SAMPLE_PO_PATH, t.RECORDED_GOLDEN_PATH, t._assert_matches_section_8_3, t.EXPECTED_GOLDEN_PATH),
        (t.CONTAMINATION_PO_PATH, t.RECORDED_CONTAMINATION_PATH, t._assert_contamination_po,
         t.EXPECTED_CONTAMINATION_PATH),
    ]
    for source, target, check, expected in jobs:
        result = extract_document(
            client, build_text_content(source.read_text(encoding="utf-8")), examples=examples
        )
        check(result)
        t._assert_no_example_value_leaked(result, examples, expected)
        target.write_text(json.dumps(result.raw_response, indent=2) + "\n", encoding="utf-8")
        print(
            f"recorded {target.name}: model={EXTRACTION_MODEL} input_tokens={result.input_tokens} "
            f"example_tokens={result.example_input_tokens} cost=${result.est_cost_usd:.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
