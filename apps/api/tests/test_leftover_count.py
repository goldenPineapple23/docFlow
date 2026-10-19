"""
tests/leftover_count.py: a run that does not leave the database as it found
it fails (founder, 2026-10-11; D-198).

The pytest half is proven on a real pytest run, in a process of its own: its
exit code, its last line and its annotation. Only the six counts are
scripted there, so no row is left on any database to make the point.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import leftover_count

HERE = Path(__file__).resolve().parent

CLEAN = {label: 7 for label, _query in leftover_count.COUNTS}

# The three hooks, as tests/conftest.py wires them.
CHILD_CONFTEST = """
import importlib.util
import json
import os

spec = importlib.util.spec_from_file_location("leftover_count", os.environ["LEFTOVER_COUNT_PY"])
leftover_count = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leftover_count)

script = iter(json.loads(os.environ["LEFTOVER_SCRIPT"]))
leftover_count.take = lambda: next(script)


def pytest_sessionstart(session):
    leftover_count.session_start()


def pytest_sessionfinish(session, exitstatus):
    leftover_count.session_finish(session)


def pytest_terminal_summary(terminalreporter):
    leftover_count.report(terminalreporter)
"""


def _with(**changes: int) -> dict[str, int]:
    return {**CLEAN, **changes}


def _run(
    tmp_path, before, after, *, test_body="assert True", in_ci=False, args=("-q", "-rs"), parallel=False
):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(CHILD_CONFTEST, encoding="utf-8")
    (tmp_path / "test_one.py").write_text(f"def test_one():\n    {test_body}\n", encoding="utf-8")
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GITHUB_ACTIONS", "PYTEST_ADDOPTS", "DOCFLOW_TEST_PARALLEL_RUN")
    }
    if parallel:
        env["DOCFLOW_TEST_PARALLEL_RUN"] = "1"
    env["LEFTOVER_COUNT_PY"] = str(HERE / "leftover_count.py")
    env["LEFTOVER_SCRIPT"] = json.dumps([before, after])
    if in_ci:
        env["GITHUB_ACTIONS"] = "true"
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    return done.returncode, lines, done.stdout


@pytest.fixture()
def counts(monkeypatch):
    # A normal run's rules, also when this suite is itself in a parallel run.
    monkeypatch.setattr(leftover_count, "_by_the_runner", False)

    def set_counts(before, after):
        monkeypatch.setattr(leftover_count, "_before", before)
        monkeypatch.setattr(leftover_count, "_after", after)

    return set_counts


def test_plus_zero_on_every_count_is_not_a_failure(counts):
    counts(CLEAN, dict(CLEAN))
    assert leftover_count.failed() is False
    assert leftover_count.verdict() is None
    assert leftover_count.line().endswith("users with no tenant 7 -> 7 (+0)")


def test_a_count_that_went_up_fails_and_is_named(counts):
    counts(CLEAN, _with(tenants=8, **{"e-mails": 9}))
    assert leftover_count.failed() is True
    assert leftover_count.moved() == ["tenants", "e-mails"]
    assert leftover_count.verdict() == (
        "FAILED: this run did not leave the database as it found it: tenants, e-mails"
    )


def test_a_count_that_went_down_fails_too(counts):
    counts(CLEAN, _with(**{"founder alerts": 6}))
    assert leftover_count.failed() is True
    assert "founder alerts 7 -> 6 (-1)" in leftover_count.line()


def test_counts_that_cannot_be_read_again_fail(counts):
    counts(CLEAN, None)
    assert leftover_count.failed() is True
    assert "could not be read again" in leftover_count.verdict()


def test_a_run_with_no_counts_at_all_says_nothing_and_does_not_fail(counts):
    counts(None, None)
    assert leftover_count.failed() is False
    assert leftover_count.line() is None


def test_in_a_parallel_run_the_suite_counts_nothing_and_says_the_runner_does(tmp_path):
    """D-211: the runner counts before and after all three suites."""
    code, lines, out = _run(tmp_path, _with(tenants=8), _with(tenants=9), parallel=True)
    assert code == 0, out
    assert lines[-2] == (
        "left behind by this run: counted by the parallel runner, before and after all three suites"
    ), out
    assert lines[-1].startswith("1 passed in "), out


def test_a_clean_run_exits_zero(tmp_path):
    code, lines, out = _run(tmp_path, CLEAN, CLEAN)
    assert code == 0, out
    assert lines[-2].startswith("left behind by this run (before -> after): tenants 7 -> 7 (+0)"), out
    assert lines[-1].startswith("1 passed in "), out


def test_a_run_that_left_a_row_exits_one_and_its_last_line_says_so(tmp_path):
    code, lines, out = _run(tmp_path, CLEAN, _with(tenants=8))
    assert code == 1, out
    assert "tenants 7 -> 8 (+1)" in lines[-3], out
    assert lines[-2] == "FAILED: this run did not leave the database as it found it: tenants", out
    assert lines[-1].startswith("1 passed, 1 error in "), out


def test_the_default_summary_names_it(tmp_path):
    code, lines, out = _run(tmp_path, CLEAN, _with(tenants=8), args=())
    assert code == 1, out
    assert any(line.startswith(f"ERROR {leftover_count.NODE_ID}") for line in lines), out


def test_a_run_that_cannot_read_its_counts_again_exits_one(tmp_path):
    code, lines, out = _run(tmp_path, CLEAN, None)
    assert code == 1, out
    assert lines[-3] == "left behind by this run: not read at the end of the run", out
    assert lines[-1].startswith("1 passed, 1 error in "), out


def test_a_failed_test_and_a_leftover_are_both_counted(tmp_path):
    code, lines, out = _run(tmp_path, CLEAN, _with(tenants=8), test_body="assert False")
    assert code == 1, out
    assert lines[-1].startswith("1 failed, 1 error in "), out


def test_in_ci_a_leftover_is_an_error_annotation_and_a_clean_run_a_notice(tmp_path):
    left = tmp_path / "left"
    clean = tmp_path / "clean"
    left.mkdir()
    clean.mkdir()
    _code, lines, out = _run(left, CLEAN, _with(tenants=8), in_ci=True)
    annotation = [line for line in lines if line.startswith("::")]
    assert len(annotation) == 1, out
    assert annotation[0].startswith(
        "::error title=api leftover count::left behind by this run (before -> after): tenants 7 -> 8 (+1)"
    ), out
    assert annotation[0].endswith(
        "%0AFAILED: this run did not leave the database as it found it: tenants"
    ), out

    _code, lines, out = _run(clean, CLEAN, CLEAN, in_ci=True)
    annotation = [line for line in lines if line.startswith("::")]
    assert len(annotation) == 1, out
    assert annotation[0].startswith("::notice title=api leftover count::left behind by this run"), out
