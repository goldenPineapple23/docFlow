"""
The mutation corpus through the parser (D-203, PR 1b-i).

Each of the 369 broken files must:
- never answer `error`: a parser's own failure on a malformed file is the
  file's, and keeps its catalog code; `error` is for our bugs only;
- answer exactly what `fixtures/mutation_corpus_codes.json` recorded on the
  code before PR 1b-i (founder, 2026-10-16). The only change allowed when it
  was recorded: the three `po.ods` cases that escaped the parser then (a
  crashed job) answer DOC-005. Any other change fails here and is reported.

In-process (this machine, CI's unit job) through the job's own `_parse`, so
`error` shows here too; over HTTP (CI's image job) every answer comes out of
a real sandboxed job.
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import FIXTURES, post, via_http
from tests.mutation_corpus import Case, cases, mutations

CASES = cases()
RECORDED = json.loads((FIXTURES / "mutation_corpus_codes.json").read_text(encoding="utf-8"))["cases"]


def _answer(case: Case) -> dict:
    if via_http():
        status, body = post(case.kind, case.content, case.extension)
        assert status == 200, f"{case.case_id}: HTTP {status} {body}"
        return body
    from parse_service.job import _parse

    return _parse(case.kind, case.content, f"upload{case.extension}")


def test_the_corpus_is_the_recorded_one():
    assert len(CASES) == 369
    assert sorted(c.case_id for c in CASES) == sorted(RECORDED)


def test_the_corpus_is_the_same_bytes_every_run():
    content = (FIXTURES / "positive" / "po.docx").read_bytes()
    first, second = mutations("positive/po.docx", content), mutations("positive/po.docx", content)
    assert first == second
    assert all(mutated != content for _, mutated in first)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.case_id)
def test_a_broken_file_keeps_its_recorded_answer_and_is_never_ours(case):
    body = _answer(case)
    assert body.get("outcome") != "error", (
        f"{case.case_id}: answered error, {body.get('error_type')} at {body.get('location')}"
    )
    got = "ok" if body.get("outcome") == "ok" else f"{body.get('outcome')} {body.get('code') or body.get('cause')}"
    assert got == RECORDED[case.case_id]
