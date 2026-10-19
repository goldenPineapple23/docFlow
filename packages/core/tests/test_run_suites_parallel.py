"""
scripts/run_suites_parallel.py (D-211): the three suites together. What is
tested here needs no database: the guard runs before the script opens a
connection, the logs stay outside the repository, and the counts it reads
are the suites' own six. The run itself is the parallel proof (RUNBOOK 1.4).
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "run_suites_parallel.py"
_spec = importlib.util.spec_from_file_location("run_suites_parallel", _SCRIPT)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

OFF_MACHINE = "postgresql://nobody:nothing@staging-stand-in.invalid:5432/none"


def _no_connection(*args, **kwargs):
    raise AssertionError("the runner opened a connection before its guard")


def test_an_off_machine_database_without_the_flag_is_refused_before_any_connection(monkeypatch, tmp_path):
    for name in runner.database_guard.URL_SETTINGS:
        monkeypatch.setenv(name, OFF_MACHINE)
    monkeypatch.delenv("DOCFLOW_STAGING_RUN", raising=False)
    monkeypatch.setattr(runner, "read_counts", _no_connection)
    monkeypatch.setattr(runner, "read_size", _no_connection)
    with pytest.raises(runner.database_guard.StagingDatabaseRefused):
        runner.main([str(tmp_path / "logs")])


def test_the_logs_go_outside_the_repository(monkeypatch, capsys):
    monkeypatch.setattr(runner, "read_counts", _no_connection)
    assert runner.main([str(runner.REPO / "logs")]) == 2
    assert "outside the repository" in capsys.readouterr().out


def test_it_counts_what_each_suite_counts_alone():
    from tests import database_guard

    assert [label for label, _query in runner.leftover_count.COUNTS] == [
        "tenants",
        "founder alerts",
        "e-mails",
        "scheduled jobs",
        "platform-admin rows",
        "users with no tenant",
    ]
    assert runner.database_guard.URL_SETTINGS == database_guard.URL_SETTINGS


def test_it_runs_the_three_suites_with_the_parallel_flag_and_the_durations():
    source = _SCRIPT.read_text(encoding="utf-8")
    assert [suite for suite, _folder, _venv in runner.SUITES] == ["core", "worker", "api"]
    assert '"DOCFLOW_TEST_PARALLEL_RUN": "1"' in source
    assert '"-q", "-rs", "--durations=25"' in source
    # It never sets the staging flag: that is the founder's grant, set outside it.
    code = source.split('"""', 2)[2]  # everything after the docstring
    assert "DOCFLOW_STAGING_RUN" not in code
