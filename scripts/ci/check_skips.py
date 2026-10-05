"""
Fail CI if any test was skipped without an approval on file.

A skipped test is a test that proved nothing. Until Phase 5.5, CI skipped 348
of 391 API tests (every RLS and tenant-isolation test among them) and still
reported green (docs/REVIEW-PHASE5.md H7). This check makes a skip visible and
deliberate: every skipped test must be listed in .github/approved-skips.txt
with the reason the founder accepted, or the job fails.

Usage:
  python scripts/ci/check_skips.py <suite-name> <junit.xml> [<approved-skips.txt>]

Each non-comment line of the approvals file is
  <suite-name> <test id>  # reason
where the test id is "<classname>::<name>" as JUnit records it, e.g.
  worker tests.test_conversion::test_doc_converts  # why this may skip

Stale approvals (listed, but the test ran) are reported, not failed, so that
fixing an environment gap doesn't break the build; delete them when seen.

A line whose suite is written "<suite-name>-staging" is a skip on
docflow-staging only (founder, 2026-10-05; D-189). It approves nothing here.
The opposite: that test MUST be in this job's report, passed, and the job
fails if it is skipped, failed or missing. Its result is printed as an
annotation, so the run page shows that it ran.

It also lists failed tests as GitHub annotations (`::error::`), so a failure
can be read from the run's summary page without opening the log.
"""

import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_APPROVALS = Path(__file__).resolve().parents[2] / ".github" / "approved-skips.txt"


def read_approvals(path: Path, suite: str) -> set[str]:
    approved: set[str] = set()
    if not path.exists():
        return approved
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise SystemExit(f"malformed approval line: {raw!r}")
        if parts[0] == suite:
            approved.add(parts[1].strip())
    return approved


def skipped_tests(junit_path: Path) -> list[tuple[str, str]]:
    tree = ET.parse(junit_path)
    found: list[tuple[str, str]] = []
    for case in tree.iter("testcase"):
        skip = case.find("skipped")
        if skip is None:
            continue
        test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
        found.append((test_id, skip.get("message", "")))
    return found


def failed_tests(junit_path: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for case in ET.parse(junit_path).iter("testcase"):
        for tag in ("failure", "error"):
            bad = case.find(tag)
            if bad is not None:
                message = (bad.get("message") or "").strip().splitlines()
                first = message[0][:300] if message else tag
                found.append((f"{case.get('classname', '')}::{case.get('name', '')}", first))
                break
    return found


def test_outcomes(junit_path: Path) -> dict[str, tuple[str, str]]:
    """test id -> (passed | skipped | failed, seconds as JUnit records them)."""
    found: dict[str, tuple[str, str]] = {}
    for case in ET.parse(junit_path).iter("testcase"):
        test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
        if case.find("failure") is not None or case.find("error") is not None:
            outcome = "failed"
        elif case.find("skipped") is not None:
            outcome = "skipped"
        else:
            outcome = "passed"
        if found.get(test_id, ("passed", ""))[0] == "passed":  # one bad entry is enough
            found[test_id] = (outcome, case.get("time", "?"))
    return found


def check_must_run(suite: str, must_run: set[str], outcomes: dict[str, tuple[str, str]]) -> int:
    """Each test that may skip on docflow-staging only must have run and
    passed here. Prints one annotation per test; returns how many did not."""
    not_proven = 0
    for test_id in sorted(must_run):
        outcome, seconds = outcomes.get(test_id, ("not in the report", ""))
        if outcome == "passed":
            print(
                f"::notice title={suite}: must run in CI::{test_id}: ran and passed ({seconds}s). "
                "It may skip on docflow-staging only."
            )
        else:
            not_proven += 1
            print(
                f"::error title={suite}: must run in CI::{test_id}: {outcome}. "
                "It may skip on docflow-staging only, so in CI it has to run and pass."
            )
    return not_proven


def annotate_failures(suite: str, failures: list[tuple[str, str]]) -> None:
    """GitHub shows at most 10 error annotations per step, so group them."""
    if not failures:
        return
    groups = 10
    size = max(1, -(-len(failures) // groups))
    for start in range(0, len(failures), size):
        chunk = failures[start : start + size]
        body = "%0A".join(f"{t} -- {m}".replace("%", "%25") for t, m in chunk)
        title = f"{suite}: failed tests {start + 1}-{start + len(chunk)} of {len(failures)}"
        print(f"::error title={title}::{body}")


# The ids given to the steps before the skip check in ci.yml, in job order,
# and the names they show in the run's step list.
STEP_NAMES = {
    "supabase": "Start the local Supabase stack",
    "deps": "pip install -r requirements.lock.txt",
    "core": "pip install packages/core",
    "roundtrip": "Migration 0036 forward -> reverse -> forward",
    "app_role": "Turn on the four logins",
    "libreoffice": "Install LibreOffice",
    "ruff": "ruff check",
    "mypy": "mypy",
}


def no_report_reason(report_name: str, steps_json: str | None = None) -> str:
    """
    Why there is no junit report, from the outcomes of the steps before it
    (the workflow passes `toJSON(steps)` as CI_STEPS). A setup step that
    failed means pytest never ran, and the message names that step, rather
    than pointing at a Test step that was skipped (2026-09-29: a worker run
    whose local Supabase stack failed to start read as a pytest failure).
    """
    raw = os.environ.get("CI_STEPS", "") if steps_json is None else steps_json
    try:
        steps = json.loads(raw) if raw else {}
    except ValueError:
        steps = {}
    outcomes = {k: (v or {}).get("outcome") for k, v in steps.items()} if isinstance(steps, dict) else {}
    for step_id, name in STEP_NAMES.items():
        if outcomes.get(step_id) == "failure":
            return (
                f"pytest never ran: the step \"{name}\" failed first, so nothing was tested. "
                "That step's own log has the cause."
            )
    if outcomes.get("test") == "failure":
        return (
            f"pytest wrote no {report_name}, so it stopped before or while collecting tests. "
            "Look for an earlier annotation from the Test step."
        )
    return (
        f"pytest wrote no {report_name}, and no named step before it failed. "
        "The first failed step in the job's step list says why."
    )

def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(__doc__, file=sys.stderr)
        return 2
    suite, junit_path = argv[1], Path(argv[2])
    if not junit_path.exists():
        # No report: say why on the run page instead of a bare traceback.
        print(f"::error title={suite}: no test report::{no_report_reason(junit_path.name)}")
        return 1
    approvals_path = Path(argv[3]) if len(argv) == 4 else DEFAULT_APPROVALS

    annotate_failures(suite, failed_tests(junit_path))
    approved = read_approvals(approvals_path, suite)
    skipped = skipped_tests(junit_path)
    unapproved = [(t, why) for t, why in skipped if t not in approved]
    stale = sorted(approved - {t for t, _ in skipped})

    total = sum(1 for _ in ET.parse(junit_path).iter("testcase"))
    failed = len(failed_tests(junit_path))
    summary = f"{total} tests, {failed} failed, {len(skipped)} skipped, {len(unapproved)} unapproved"
    print(f"[{suite}] {summary}")
    # Also as an annotation, so the counts are on the run page, not only in the log.
    print(f"::notice title={suite} test counts::{summary}")
    for test_id in stale:
        print(f"  stale approval (test ran; remove it): {test_id}")
    for test_id, why in unapproved:
        print(f"  UNAPPROVED SKIP: {test_id}\n      reason given: {why}")
    must_run = read_approvals(approvals_path, f"{suite}-staging")
    not_proven = check_must_run(suite, must_run, test_outcomes(junit_path))
    return 1 if unapproved or not_proven else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
