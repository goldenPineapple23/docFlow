"""
The web dependency audit (scripts/ci/npm_audit.py; founder, 2026-10-23;
D-219): a high or critical advisory in a runtime dependency blocks when the
job says so, one only in dev dependencies warns, and a report that can't be
read fails. There are no exceptions.

`fixtures/npm_audit/web_2026-10-05.json` is npm's own report for apps/web on
2026-10-05, unedited: five packages marked high, all through one advisory,
GHSA-vfj7-8cjw-p6xm (braces, reached only through the lint chain).
`web_runtime_2026-10-05.json` is the same day's `npm audit --omit=dev`, also
unedited: nothing reported. So braces is the recorded dev-only finding. The
planted advisories and the planted runtime path below are made up and named
so.
"""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
DAILY = REPO / ".github" / "workflows" / "dependency-audit.yml"
REPORT = Path(__file__).parent / "fixtures" / "npm_audit" / "web_2026-10-05.json"
RUNTIME_REPORT = Path(__file__).parent / "fixtures" / "npm_audit" / "web_runtime_2026-10-05.json"

BRACES = "GHSA-vfj7-8cjw-p6xm"
PLANTED = "GHSA-0000-0000-0000"


def _npm_audit():
    spec = importlib.util.spec_from_file_location("npm_audit", REPO / "scripts" / "ci" / "npm_audit.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


def _runtime_report() -> dict:
    return json.loads(RUNTIME_REPORT.read_text(encoding="utf-8"))


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


def _braces_through_a_runtime_dependency() -> tuple[dict, dict]:
    """The planted runtime path: a made-up runtime package starts depending on
    braces. npm then reports the same advisory with dev dependencies left out
    too, so braces (npm's own entry, unedited) and the package that brought it
    appear in both reports."""
    full = _report()
    carrier = {
        "name": "acme-test-runtime-package",
        "severity": "high",
        "isDirect": True,
        "via": ["braces"],
        "effects": [],
        "range": "*",
        "nodes": ["node_modules/acme-test-runtime-package"],
        "fixAvailable": False,
    }
    full["vulnerabilities"]["acme-test-runtime-package"] = carrier
    full["metadata"]["vulnerabilities"]["high"] += 1
    full["metadata"]["vulnerabilities"]["total"] += 1

    runtime = _runtime_report()
    runtime["vulnerabilities"] = {
        "braces": copy.deepcopy(full["vulnerabilities"]["braces"]),
        "acme-test-runtime-package": copy.deepcopy(carrier),
    }
    runtime["metadata"]["vulnerabilities"]["high"] = 2
    runtime["metadata"]["vulnerabilities"]["total"] = 2
    return full, runtime


_REAL_RUNTIME = object()


def _run(report, capsys, blocking=True, runtime=_REAL_RUNTIME) -> tuple[int, str]:
    if runtime is _REAL_RUNTIME:
        runtime = _runtime_report()
    code = _npm_audit().check("web", report, runtime, blocking)
    return code, capsys.readouterr().out


def test_the_recorded_report_is_the_braces_advisory_and_nothing_else():
    advisories = _npm_audit().advisories(_report())
    assert [(a.advisory, a.package, a.severity) for a in advisories] == [(BRACES, "braces", "high")]


def test_the_real_runtime_report_is_clean():
    assert _npm_audit().advisories(_runtime_report()) == []


@pytest.mark.parametrize("blocking", [True, False])
def test_a_dev_only_advisory_warns_and_passes_either_way(blocking, capsys):
    code, out = _run(_report(), capsys, blocking=blocking)
    assert code == 0
    assert f"::warning title=web: dev-only high advisory in braces::{BRACES}" in out
    assert "::error" not in out
    assert "1 high or critical (0 in runtime dependencies, 1 dev-only)" in out


@pytest.mark.parametrize("severity", ["high", "critical"])
def test_a_runtime_advisory_blocks_when_blocking(severity, capsys):
    full = _plant(_report(), severity)
    runtime = _plant(_runtime_report(), severity)
    code, out = _run(full, capsys, blocking=True, runtime=runtime)
    assert code == 1
    assert f"::error title=web: {severity} advisory in acme-test-package::{PLANTED}" in out
    # braces is still dev-only beside it, and only warns.
    assert f"::warning title=web: dev-only high advisory in braces::{BRACES}" in out
    assert "(1 in runtime dependencies, 1 dev-only); blocking: yes" in out


@pytest.mark.parametrize("severity", ["high", "critical"])
def test_a_runtime_advisory_only_warns_when_not_blocking(severity, capsys):
    full = _plant(_report(), severity)
    runtime = _plant(_runtime_report(), severity)
    code, out = _run(full, capsys, blocking=False, runtime=runtime)
    assert code == 0
    assert (
        f"::warning title=web: {severity} advisory in acme-test-package "
        f"(not blocking: no package file changed)::{PLANTED}"
    ) in out
    assert "::error" not in out
    assert "blocking: no" in out


@pytest.mark.parametrize("blocking", [True, False])
def test_a_planted_dev_only_critical_advisory_warns_either_way(blocking, capsys):
    code, out = _run(_plant(_report(), "critical"), capsys, blocking=blocking)
    assert code == 0
    assert f"::warning title=web: dev-only critical advisory in acme-test-package::{PLANTED}" in out
    assert "::error" not in out


def test_a_moderate_runtime_advisory_does_not_block(capsys):
    full = _plant(_report(), "moderate")
    runtime = _plant(_runtime_report(), "moderate")
    code, out = _run(full, capsys, blocking=True, runtime=runtime)
    assert code == 0
    assert "acme-test-package" not in out


def test_a_dev_advisory_that_reaches_a_runtime_dependency_blocks(capsys):
    """The same id, once npm reports it with dev dependencies left out, is a
    runtime finding: there is no exception left to hide it."""
    full, runtime = _braces_through_a_runtime_dependency()
    code, out = _run(full, capsys, blocking=True, runtime=runtime)
    assert code == 1
    assert f"::error title=web: high advisory in braces::{BRACES}" in out
    assert "::warning" not in out
    assert "(1 in runtime dependencies, 0 dev-only)" in out


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
@pytest.mark.parametrize("blocking", [True, False])
def test_a_report_that_cannot_be_read_fails_either_way(report, blocking, capsys):
    code, out = _run(report, capsys, blocking=blocking)
    assert code == 1
    assert "::error title=web: dependency audit could not be read::" in out


@pytest.mark.parametrize(
    "runtime",
    [{"error": {"code": "ENOAUDIT", "summary": "the registry did not answer"}}, {}, [], None],
)
@pytest.mark.parametrize("blocking", [True, False])
def test_a_runtime_report_that_cannot_be_read_fails_either_way(runtime, blocking, capsys):
    code, out = _run(_report(), capsys, blocking=blocking, runtime=runtime)
    assert code == 1
    assert "::error title=web: runtime dependency audit could not be read::" in out


def test_a_report_that_counts_high_findings_it_does_not_list_fails(capsys):
    report = _report()
    for package in report["vulnerabilities"].values():
        package["via"] = [via for via in package["via"] if isinstance(via, str)]
    code, out = _run(report, capsys, blocking=False)
    assert code == 1
    assert "counts 5 high or critical findings but lists no such advisory" in out


def test_a_missing_or_broken_report_file_fails(tmp_path, capsys):
    module = _npm_audit()
    good, runtime, absent = str(REPORT), str(RUNTIME_REPORT), str(tmp_path / "absent.json")
    broken = tmp_path / "npm-audit.json"
    broken.write_text("npm error", encoding="utf-8")
    for blocking in ("yes", "no"):
        assert module.main(["npm_audit.py", "web", absent, runtime, "--blocking", blocking]) == 1
        assert module.main(["npm_audit.py", "web", str(broken), runtime, "--blocking", blocking]) == 1
        assert module.main(["npm_audit.py", "web", good, absent, "--blocking", blocking]) == 1
        assert module.main(["npm_audit.py", "web", good, str(broken), "--blocking", blocking]) == 1
    assert capsys.readouterr().out.count("could not be read") == 8


@pytest.mark.parametrize(
    "tail",
    [[], ["--blocking"], ["--blocking", "maybe"], ["--blocking", "YES"], ["--strict", "yes"]],
)
def test_the_command_line_needs_blocking_yes_or_no(tail):
    argv = ["npm_audit.py", "web", str(REPORT), str(RUNTIME_REPORT), *tail]
    assert _npm_audit().main(argv) == 2


@pytest.mark.parametrize("blocking", ["yes", "no"])
def test_the_two_real_reports_pass_through_the_command_line(blocking, capsys):
    argv = ["npm_audit.py", "web", str(REPORT), str(RUNTIME_REPORT), "--blocking", blocking]
    assert _npm_audit().main(argv) == 0
    out = capsys.readouterr().out
    assert f"web: 1 advisories reported, 1 high or critical (0 in runtime dependencies, 1 dev-only); blocking: {blocking}" in out


def test_the_exceptions_file_and_its_code_are_retired():
    """Founder, 2026-10-23: no exceptions mechanism. Bringing one back is a
    deliberate, visible change to this test too."""
    assert not (REPO / ".github" / "audit-exceptions.txt").exists()
    source = (REPO / "scripts" / "ci" / "npm_audit.py").read_text(encoding="utf-8")
    assert "audit-exceptions" not in source and "review=" not in source


def _job(workflow: str, job: str, next_job: str) -> str:
    start = workflow.index(f"\n  {job}:\n")
    return workflow[start : workflow.index(f"\n  {next_job}:\n", start)]


def test_the_web_job_audits_both_trees_and_blocks_only_on_a_package_file_change():
    block = _job(WORKFLOW.read_text(encoding="utf-8"), "web", "web-live")
    assert "          fetch-depth: 2\n" in block
    changed = (
        'scripts/ci/deps_changed.sh" apps/web/package.json apps/web/package-lock.json\n'
    )
    full = "          npm audit --json > npm-audit.json || true\n"
    runtime = "          npm audit --omit=dev --json > npm-audit-runtime.json || true\n"
    script = (
        'scripts/ci/npm_audit.py" web npm-audit.json npm-audit-runtime.json '
        '--blocking "${{ steps.deps_changed.outputs.blocking }}"\n'
    )
    for text in (changed, full, runtime, script):
        assert block.count(text) == 1, text
    assert "        id: deps_changed\n" in block
    assert block.index(changed) < block.index(full) < block.index(runtime) < block.index(script)
    # Dev dependencies stay in the audit (they warn): the only `--omit` is the
    # runtime report's. Nothing switches the audit off.
    assert block.count("npm audit") == 2
    assert block.count("--omit") == 1
    for flag in ("--production", "--only", "continue-on-error", "audit-exceptions"):
        assert flag not in block, f"the web job's audit must not use {flag}"


def test_the_daily_web_audit_always_blocks():
    workflow = DAILY.read_text(encoding="utf-8")
    assert '--blocking yes\n' in workflow
    assert "npm-audit.json npm-audit-runtime.json --blocking yes" in workflow
    assert "--blocking no" not in workflow and "deps_changed" not in workflow
