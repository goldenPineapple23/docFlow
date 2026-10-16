"""
What the mutation corpus raises, block by block (D-203, PR 1b-i).

Runs every corpus case through the parser in-process and records, for each
broad block, the exception that reached it: its type, and the module of
the innermost frame that raised it (the library). A block that turns an
exception into a catalog code (`ConversionError`, `FileRejection`,
`ImportParseError` raised inside an `except`) is seen as the wrapper's
constructor runs; `documents.parse`'s own block is seen by wrapping the two
calls inside its `try`. Anything that escapes `parse` entirely is recorded
too: today that is a crashed job.

Run from `apps/parse`, the result to a file outside the repository:

    .venv/Scripts/python.exe tests/record_corpus_exceptions.py <out.json>

In-process and unsandboxed: the types are the libraries' own, the timings
are this machine's, not the sandbox's.
"""

from __future__ import annotations

import collections
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from docflow_core import file_types  # noqa: E402
from mutation_corpus import cases  # noqa: E402

from parse_service.parsing import conversion, documents, tables  # noqa: E402

WRAPPERS = (file_types.FileRejection, conversion.ConversionError, tables.ImportParseError)

seen: list[dict] = []
current: dict = {}


def _describe(exc: BaseException) -> dict:
    tb = exc.__traceback__
    library = "unknown"
    while tb is not None:
        library = str(tb.tb_frame.f_globals.get("__name__") or "unknown")
        tb = tb.tb_next
    kind = type(exc)
    return {"type": f"{kind.__module__}.{kind.__qualname__}", "raised_in": library}


def _record(block: str, exc: BaseException, wrapped_as: str) -> None:
    seen.append({"case": current.get("case_id"), "block": block, "wrapped_as": wrapped_as, **_describe(exc)})


def _hook_wrapper(cls: type) -> None:
    original = cls.__init__

    def __init__(self, *args, **kwargs):  # noqa: N807
        original(self, *args, **kwargs)
        cause = sys.exc_info()[1]
        if cause is None or isinstance(cause, WRAPPERS):
            return  # our own check, not a library's exception
        frame = sys._getframe(1)
        while frame.f_code.co_name == "__init__":
            frame = frame.f_back
        block = f"{frame.f_globals.get('__name__')}:{frame.f_code.co_qualname}"
        code = getattr(self, "error_code", None) or getattr(self, "code", None)
        _record(block, cause, f"{type(self).__name__}({code})")

    cls.__init__ = __init__


def _hook_call(module, name: str) -> None:
    original = getattr(module, name)

    def wrapped(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except (conversion.ConversionError, MemoryError):
            raise
        except Exception as exc:
            _record("parse_service.parsing.documents:parse", exc, "DOC-005 (documents.parse)")
            raise

    setattr(module, name, wrapped)


def main(out_path: str) -> int:
    for cls in WRAPPERS:
        _hook_wrapper(cls)
    _hook_call(documents, "prepare_artifacts")
    _hook_call(documents, "artifact_parts")

    all_cases = cases()
    results = []
    started_all = time.monotonic()
    for index, case in enumerate(all_cases, 1):
        current["case_id"] = case.case_id
        print(f"[{index}/{len(all_cases)}] {case.case_id}", flush=True)
        started = time.monotonic()
        try:
            parse = documents.parse if case.kind == "document" else tables.parse
            answer = parse(case.content, f"upload{case.extension}")
            outcome, code = answer.get("outcome"), answer.get("code")
        except BaseException as exc:  # noqa: BLE001 -- a measurement: record what escaped
            _record(f"escaped:{case.kind}", exc, "nothing (a crashed job today)")
            outcome, code = "escaped", type(exc).__name__
        results.append(
            {"case": case.case_id, "outcome": outcome, "code": code, "seconds": round(time.monotonic() - started, 3)}
        )

    total = round(time.monotonic() - started_all, 1)
    by_block: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for item in seen:
        by_block[item["block"]][f"{item['type']} (raised in {item['raised_in']}) -> {item['wrapped_as']}"] += 1
    outcomes = collections.Counter(f"{r['outcome']} {r['code'] or ''}".strip() for r in results)
    slowest = sorted(results, key=lambda r: r["seconds"], reverse=True)[:5]
    Path(out_path).write_text(
        json.dumps(
            {
                "cases": len(all_cases),
                "seconds_total": total,
                "outcomes": dict(outcomes),
                "by_block": {k: dict(v) for k, v in sorted(by_block.items())},
                "slowest": slowest,
                "seen": seen,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"cases={len(all_cases)} seconds_total={total}")
    for outcome, count in sorted(outcomes.items()):
        print(f"  outcome {outcome}: {count}")
    for block, counter in sorted(by_block.items()):
        print(block)
        for line, count in counter.most_common():
            print(f"  {count:4d}  {line}")
    print("slowest:", ", ".join(f"{r['case']} {r['seconds']}s" for r in slowest))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
