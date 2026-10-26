"""
The Python dependency audits (scripts/ci/pip_audit.py, ci.yml's four Python
jobs, the daily workflow; founder, 2026-10-23; D-219): a known vulnerability
in what an app ships blocks when the job says so, one in a dev tool warns,
and a report that can't be read fails. Any severity counts; there are no
exceptions.

`fixtures/pip_audit/api_2026-10-26.json` is pip-audit 2.10.1's own report
for apps/api/requirements.lock.txt on 2026-10-26, unedited: 78 packages,
nothing known. The planted package and advisory below are made up and named
so.
"""

import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
DAILY = REPO / ".github" / "workflows" / "dependency-audit.yml"
REPORT = Path(__file__).parent / "fixtures" / "pip_audit" / "api_2026-10-26.json"

PLANTED = {
    "name": "acme-test-package",
    "version": "1.0.0",
    "vulns": [
        {
            "id": "PYSEC-0000-0",
            "fix_versions": ["1.0.1"],
            "aliases": ["GHSA-0000-0000-0000"],
            "description": "Planted for the audit test",
        }
    ],
}
PLANTED_TEXT = "acme-test-package 1.0.0: PYSEC-0000-0 (GHSA-0000-0000-0000), fixed in 1.0.1"


def _pip_audit():
    spec = importlib.util.spec_from_file_location("pip_audit_ci", REPO / "scripts" / "ci" / "pip_audit.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


def _plant(report: dict) -> dict:
    planted = copy.deepcopy(report)
    planted["dependencies"].append(copy.deepcopy(PLANTED))
    return planted


def _run(capsys, runtime, dev=None, blocking=True, app="api") -> tuple[int, str]:
    reports = {"runtime": runtime}
    if dev is not None:
        reports["dev"] = dev
    code = _pip_audit().check(app, reports, blocking)
    return code, capsys.readouterr().out


def test_the_recorded_report_is_78_packages_and_nothing_known():
    audited, found = _pip_audit().findings(_report())
    assert (audited, found) == (78, [])


@pytest.mark.parametrize("blocking", [True, False])
def test_a_clean_report_passes_either_way(blocking, capsys):
    code, out = _run(capsys, _report(), blocking=blocking)
    assert code == 0
    assert "::error" not in out and "::warning" not in out
    mode = "blocking: yes" if blocking else "blocking: no (requirements files unchanged)"
    assert f"::notice title=api: dependency audit::78 packages audited, 0 with a known vulnerability; {mode}" in out


def test_a_runtime_finding_blocks_when_blocking(capsys):
    code, out = _run(capsys, _plant(_report()), blocking=True)
    assert code == 1
    assert f"::error title=api: known vulnerability in a runtime dependency::{PLANTED_TEXT}" in out
    assert "79 packages audited, 1 with a known vulnerability; blocking: yes" in out


def test_a_runtime_finding_only_warns_when_not_blocking(capsys):
    code, out = _run(capsys, _plant(_report()), blocking=False)
    assert code == 0
    assert (
        "::warning title=api: known vulnerability in a runtime dependency "
        f"(not blocking: no requirements file changed)::{PLANTED_TEXT}"
    ) in out
    assert "::error" not in out


@pytest.mark.parametrize("blocking", [True, False])
def test_a_dev_tool_finding_warns_either_way(blocking, capsys):
    code, out = _run(capsys, _report(), dev=_plant({"dependencies": []}), blocking=blocking, app="parse")
    assert code == 0
    assert f"::warning title=parse: known vulnerability in a dev tool (warning only)::{PLANTED_TEXT}" in out
    assert "::notice title=parse (dev tools): dependency audit::1 packages audited, 1 with a known vulnerability; warning only" in out
    assert "::error" not in out


def test_a_finding_with_no_alias_and_no_fix_says_so(capsys):
    report = _plant(_report())
    report["dependencies"][-1]["vulns"][0].update(aliases=[], fix_versions=[])
    code, out = _run(capsys, report, blocking=True)
    assert code == 1
    assert "acme-test-package 1.0.0: PYSEC-0000-0, no fixed version yet" in out


@pytest.mark.parametrize(
    "report",
    [
        None,
        [],
        {},
        {"dependencies": "none"},
        {"dependencies": [{"version": "1.0"}]},
        {"dependencies": [{"name": "acme-test-package", "version": "1.0.0"}]},
        {"dependencies": [{"name": "acme-test-package", "skip_reason": "could not be resolved"}]},
        {"dependencies": [{"name": "acme-test-package", "version": "1.0.0", "vulns": [{}]}]},
    ],
)
@pytest.mark.parametrize("blocking", [True, False])
def test_a_report_that_cannot_be_read_fails_either_way(report, blocking, capsys):
    code, out = _run(capsys, report, blocking=blocking)
    assert code == 1
    assert "::error title=api: dependency audit could not be read::" in out


def test_a_skipped_package_is_named(capsys):
    report = _report()
    report["dependencies"].append({"name": "acme-test-package", "skip_reason": "could not be resolved"})
    code, out = _run(capsys, report, blocking=False)
    assert code == 1
    assert "acme-test-package was not audited: could not be resolved" in out


@pytest.mark.parametrize("blocking", [True, False])
def test_an_unreadable_dev_report_fails_too(blocking, capsys):
    code, out = _run(capsys, _report(), dev={}, blocking=blocking, app="parse")
    assert code == 1
    assert "::error title=parse (dev tools): dependency audit could not be read::" in out


def test_missing_or_broken_files_fail_once_each(tmp_path, capsys):
    module = _pip_audit()
    good, absent = str(REPORT), str(tmp_path / "absent.json")
    broken = tmp_path / "pip-audit.json"
    broken.write_text("pip-audit error", encoding="utf-8")
    for blocking in ("yes", "no"):
        assert module.main(["pip_audit.py", "api", "--blocking", blocking, f"runtime={absent}"]) == 1
        assert module.main(["pip_audit.py", "api", "--blocking", blocking, f"runtime={broken}"]) == 1
        assert module.main(["pip_audit.py", "parse", "--blocking", blocking, f"runtime={good}", f"dev={absent}"]) == 1
    assert capsys.readouterr().out.count("could not be read") == 6


@pytest.mark.parametrize(
    "tail",
    [
        [],
        ["--blocking", "yes"],
        ["--blocking", "maybe", f"runtime={REPORT}"],
        ["--strict", "yes", f"runtime={REPORT}"],
        ["--blocking", "yes", f"dev={REPORT}"],
        ["--blocking", "yes", f"runtime={REPORT}", f"runtime={REPORT}"],
        ["--blocking", "yes", f"other={REPORT}"],
        ["--blocking", "yes", "runtime="],
    ],
)
def test_the_command_line_is_strict(tail):
    assert _pip_audit().main(["pip_audit.py", "api", *tail]) == 2


@pytest.mark.parametrize("blocking", ["yes", "no"])
def test_the_real_report_passes_through_the_command_line(blocking, capsys):
    assert _pip_audit().main(["pip_audit.py", "api", "--blocking", blocking, f"runtime={REPORT}"]) == 0
    assert "78 packages audited, 0 with a known vulnerability" in capsys.readouterr().out


# ci.yml and the daily workflow ---------------------------------------------

AUDIT_FLAGS = "--no-deps --disable-pip --strict --progress-spinner off -f json -o"
JOBS = {
    # job: (runtime requirements, dev requirements, files whose change blocks)
    "core": ("../../apps/worker/requirements.lock.txt", None, "packages/core/pyproject.toml apps/worker/requirements.lock.txt"),
    "api": ("requirements.lock.txt", None, "apps/api/requirements.txt apps/api/requirements.lock.txt"),
    "worker": ("requirements.lock.txt", None, "apps/worker/requirements.txt apps/worker/requirements.lock.txt"),
    "parse": ("requirements.lock.txt", "requirements-dev.txt", "apps/parse/requirements.txt apps/parse/requirements.lock.txt"),
}
ORDER = ["web", "web-live", "core", "api", "worker", "parse"]


def _job(workflow: str, job: str) -> str:
    start = workflow.index(f"\n  {job}:\n")
    later = [workflow.index(f"\n  {j}:\n") for j in ORDER if workflow.index(f"\n  {j}:\n") > start]
    return workflow[start : min(later) if later else len(workflow)]


@pytest.mark.parametrize("job", sorted(JOBS))
def test_each_python_job_audits_its_requirements_and_blocks_only_on_their_change(job):
    runtime, dev, files = JOBS[job]
    block = _job(WORKFLOW.read_text(encoding="utf-8"), job)
    assert "          fetch-depth: 2\n" in block
    assert block.count("      - run: pip install pip-audit==2.10.1\n") == 1
    assert "pip install pip-audit\n" not in block
    changed = f'scripts/ci/deps_changed.sh" {files}\n'
    pip = f"pip-audit -r {runtime} {AUDIT_FLAGS} pip-audit-runtime.json || true\n"
    reports = "runtime=pip-audit-runtime.json" + (" dev=pip-audit-dev.json" if dev else "")
    wrapper = (
        f'scripts/ci/pip_audit.py" {job} --blocking "${{{{ steps.deps_changed.outputs.blocking }}}}" '
        f"{reports}\n"
    )
    for text in (changed, pip, wrapper):
        assert block.count(text) == 1, text
    assert block.index(changed) < block.index(pip) < block.index(wrapper)
    if dev:
        dev_pip = f"pip-audit -r {dev} {AUDIT_FLAGS} pip-audit-dev.json || true\n"
        assert block.count(dev_pip) == 1
    # No bare `pip-audit` on the machine's environment, and nothing that lets
    # the audit step fail quietly.
    assert not re.search(r"run: pip-audit\s*\n", block)
    audit = block[block.index(changed) :]
    assert "continue-on-error" not in audit


def test_the_daily_workflow_runs_every_day_and_reads_only_the_repository():
    workflow = DAILY.read_text(encoding="utf-8")
    assert re.search(r'  schedule:\n    - cron: "30 6 \* \* \*"', workflow)
    assert "  workflow_dispatch:\n" in workflow
    assert "permissions:\n  contents: read\n" in workflow
    assert "secrets." not in workflow and "write" not in workflow.split("jobs:")[0]


def test_the_daily_python_audit_always_blocks_and_every_app_reports():
    workflow = DAILY.read_text(encoding="utf-8")
    python = workflow[workflow.index("\n  python:\n") :]
    assert python.count("      - run: pip install pip-audit==2.10.1\n") == 1
    for app in ("api", "worker", "parse"):
        assert f'scripts/ci/pip_audit.py" {app} --blocking yes runtime=pip-audit-runtime.json' in python
        assert f"working-directory: apps/{app}\n" in python
    assert "dev=pip-audit-dev.json" in python
    assert "--blocking no" not in python
    assert python.count("        if: success() || failure()\n") == 3
    assert python.count(f"pip-audit -r requirements.lock.txt {AUDIT_FLAGS} pip-audit-runtime.json || true\n") == 3
    assert python.count(f"pip-audit -r requirements-dev.txt {AUDIT_FLAGS} pip-audit-dev.json || true\n") == 1
