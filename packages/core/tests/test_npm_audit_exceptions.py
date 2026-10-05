"""
The web dependency audit passes one named advisory and nothing else
(scripts/ci/npm_audit.py, .github/audit-exceptions.txt; founder, 2026-10-05).

`fixtures/npm_audit/web_2026-10-05.json` is npm's own report for apps/web on
2026-10-05, unedited: five packages marked high, all through one advisory,
GHSA-vfj7-8cjw-p6xm (braces, reached only through the lint chain). The planted
advisories below are made up and named so.
"""

import copy
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
EXCEPTIONS = REPO / ".github" / "audit-exceptions.txt"
REPORT = Path(__file__).parent / "fixtures" / "npm_audit" / "web_2026-10-05.json"

BRACES = "GHSA-vfj7-8cjw-p6xm"
PLANTED = "GHSA-0000-0000-0000"
BEFORE_REVIEW = date(2026, 10, 5)


def _npm_audit():
    spec = importlib.util.spec_from_file_location("npm_audit", REPO / "scripts" / "ci" / "npm_audit.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


def _plant(report: dict, severity: str, advisory: str = PLANTED) -> dict:
    """The same report with one more package, carrying a made-up advisory."""
    planted = copy.deepcopy(report)
    planted["vulnerabilities"]["acme-test-package"] = {
        "name": "acme-test-package",
        "severity": severity,
        "isDirect": True,
        "via": [
            {
                "source": 0,
                "name": "acme-test-package",
                "dependency": "acme-test-package",
                "title": "Planted advisory for the audit test",
                "url": f"https://github.com/advisories/{advisory}",
                "severity": severity,
                "range": "*",
            }
        ],
        "effects": [],
        "range": "*",
        "nodes": ["node_modules/acme-test-package"],
        "fixAvailable": False,
    }
    planted["metadata"]["vulnerabilities"][severity] += 1
    planted["metadata"]["vulnerabilities"]["total"] += 1
    return planted


def _run(report, capsys, exceptions=None, today=BEFORE_REVIEW) -> tuple[int, str]:
    module = _npm_audit()
    if exceptions is None:
        exceptions = module.read_exceptions(EXCEPTIONS, "web")
    code = module.check("web", report, exceptions, today)
    return code, capsys.readouterr().out


def test_the_recorded_report_is_the_braces_advisory_and_nothing_else():
    advisories = _npm_audit().advisories(_report())
    assert [(a.advisory, a.package, a.severity) for a in advisories] == [(BRACES, "braces", "high")]


def test_the_audit_passes_with_the_exception_on_file(capsys):
    code, out = _run(_report(), capsys)
    assert code == 0
    assert f"::notice title=web: audit exception in use::{BRACES}" in out
    assert "Review by 2026-11-05." in out
    assert "::error" not in out and "::warning" not in out
    assert "1 passing by exception, 0 refused" in out


def test_without_the_exception_the_same_report_fails(capsys):
    code, out = _run(_report(), capsys, exceptions={})
    assert code == 1
    assert f"::error title=web: high advisory in braces::{BRACES}" in out


@pytest.mark.parametrize("severity", ["high", "critical"])
def test_a_planted_advisory_still_fails_the_audit_with_the_exception_on_file(severity, capsys):
    code, out = _run(_plant(_report(), severity), capsys)
    assert code == 1
    assert f"::error title=web: {severity} advisory in acme-test-package::{PLANTED}" in out
    # The braces exception still applies to braces, and only to braces.
    assert f"::notice title=web: audit exception in use::{BRACES}" in out
    assert "1 passing by exception, 1 refused" in out


def test_a_planted_moderate_advisory_does_not_fail_as_before(capsys):
    """`npm audit --audit-level=high` passed moderates; so does this."""
    code, _ = _run(_plant(_report(), "moderate"), capsys)
    assert code == 0


def test_an_exception_for_another_advisory_does_not_cover_braces(capsys):
    module = _npm_audit()
    other = {PLANTED: module.AuditException(PLANTED, date(2026, 11, 5), "made up for the test")}
    code, out = _run(_report(), capsys, exceptions=other)
    assert code == 1
    assert f"::error title=web: high advisory in braces::{BRACES}" in out
    assert f"::notice title=web: stale audit exception::{PLANTED}" in out


def test_an_exception_for_another_suite_does_not_apply(tmp_path):
    path = tmp_path / "exceptions.txt"
    path.write_text(f"other {BRACES} review=2026-11-05  # made up for the test\n", encoding="utf-8")
    assert _npm_audit().read_exceptions(path, "web") == {}


def test_the_day_before_the_warning_starts_there_is_no_warning(capsys):
    code, out = _run(_report(), capsys, today=date(2026, 10, 28))
    assert code == 0
    assert "::warning" not in out


@pytest.mark.parametrize("today", [date(2026, 10, 29), date(2026, 11, 4)])
def test_for_the_seven_days_before_the_review_date_every_run_warns_and_still_passes(today, capsys):
    code, out = _run(_report(), capsys, today=today)
    assert code == 0
    assert f"::warning title=web: audit exception ends soon::{BRACES}" in out
    assert "stops passing on 2026-11-05" in out


@pytest.mark.parametrize("today", [date(2026, 11, 5), date(2026, 12, 1)])
def test_from_the_review_date_the_exception_ends_and_the_job_fails(today, capsys):
    """Founder, 2026-10-05: an exception can't be forgotten. Until the line is
    removed or re-dated with a reason, the advisory fails as if unlisted."""
    code, out = _run(_report(), capsys, today=today)
    assert code == 1
    assert f"::error title=web: high advisory in braces::{BRACES}" in out
    assert "Its exception ended on 2026-11-05" in out
    assert "audit exception in use" not in out


def test_a_new_date_puts_the_exception_back_in_force(capsys):
    module = _npm_audit()
    redated = {BRACES: module.AuditException(BRACES, date(2026, 12, 5), "made up for the test")}
    code, out = _run(_report(), capsys, exceptions=redated, today=date(2026, 11, 5))
    assert code == 0
    assert "Review by 2026-12-05." in out


def test_a_clean_report_announces_the_exception_as_stale(capsys):
    report = _report()
    report["vulnerabilities"] = {}
    for level in report["metadata"]["vulnerabilities"]:
        report["metadata"]["vulnerabilities"][level] = 0
    code, out = _run(report, capsys)
    assert code == 0
    assert f"::notice title=web: stale audit exception::{BRACES}" in out


@pytest.mark.parametrize(
    "report",
    [
        {"error": {"code": "ENOAUDIT", "summary": "the registry did not answer"}},
        {},
        {"vulnerabilities": {}},
        [],
        None,
    ],
)
def test_a_report_that_cannot_be_read_fails(report, capsys):
    code, out = _run(report, capsys)
    assert code == 1
    assert "::error title=web: dependency audit could not be read::" in out


def test_a_report_that_counts_high_findings_it_does_not_list_fails(capsys):
    report = _report()
    for package in report["vulnerabilities"].values():
        package["via"] = [via for via in package["via"] if isinstance(via, str)]
    code, out = _run(report, capsys)
    assert code == 1
    assert "counts 5 high or critical findings but lists no such advisory" in out


def test_a_missing_or_broken_report_file_fails(tmp_path, capsys):
    module = _npm_audit()
    assert module.main(["npm_audit.py", "web", str(tmp_path / "absent.json")]) == 1
    broken = tmp_path / "npm-audit.json"
    broken.write_text("npm error", encoding="utf-8")
    assert module.main(["npm_audit.py", "web", str(broken)]) == 1
    assert capsys.readouterr().out.count("could not be read") == 2


@pytest.mark.parametrize(
    "line",
    [
        f"web {BRACES} review=2026-11-05",  # no reason
        f"web {BRACES}  # no review date",
        f"web {BRACES} review=soon  # not a date",
    ],
)
def test_an_exception_without_a_reason_or_a_review_date_is_refused(line, tmp_path):
    path = tmp_path / "exceptions.txt"
    path.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _npm_audit().read_exceptions(path, "web")


def test_the_exceptions_on_file_are_exactly_the_approved_one():
    """Adding an exception is a deliberate, visible change to this test too."""
    exceptions = _npm_audit().read_exceptions(EXCEPTIONS, "web")
    assert set(exceptions) == {BRACES}
    assert exceptions[BRACES].review == date(2026, 11, 5)
    assert exceptions[BRACES].reason


def test_the_web_job_audits_dev_dependencies_through_the_exception_check():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("\n  web:\n")
    block = workflow[start : workflow.index("\n  web-live:\n", start)]
    assert "npm audit --json > npm-audit.json" in block
    assert "scripts/ci/npm_audit.py" in block
    assert block.index("npm audit --json") < block.index("scripts/ci/npm_audit.py")
    # Dev dependencies stay in the audit, and nothing else switches it off.
    for flag in ("--omit", "--production", "--only", "continue-on-error"):
        assert flag not in block, f"the web job's audit must not use {flag}"
    assert block.count("npm audit") == 1
