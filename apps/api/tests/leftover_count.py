"""
What a test run left behind (the Stage 3 checkpoint's list; founder,
2026-10-08 and 2026-10-09; D-197).

Six counts are read when the run starts and again when it ends, and the
difference is printed in pytest's closing summary: tenants, founder alerts,
e-mails, scheduled jobs, platform-admin rows, and users with no tenant.
Counts only, never a row.

**A failure since 2026-10-11** (founder; D-198: printed only until a staging
run of record read `+0` on every count, which the run of 2026-10-09 at
`5a09632` did). A run fails unless every count reads `+0`: a count that moved
either way, or counts read at the start that can't be read again at the end.
The run exits 1, pytest's last line counts one error, and the line says which
counts moved.

A full run on a database nothing else writes to (RUNBOOK 1.4: one suite at a
time, with the Fly worker stopped) reads `+0` for each. A run that is
killed prints nothing, and cleans nothing: that is what the staging sweep is
for. In CI the line is also a GitHub annotation, because CI's job logs can't
be read here and annotations can (RUNBOOK 1.6).

**In a parallel run** (D-211: the three suites started together by
scripts/run_suites_parallel.py) another suite's rows come and go while this
one runs, so this suite counts nothing and says so. The runner reads the same
six counts before it starts the suites and after the last one ends, and fails
on anything but `+0`: the same rule, over the combined run.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

COUNTS: tuple[tuple[str, str], ...] = (
    ("tenants", "SELECT count(*) FROM tenants"),
    ("founder alerts", "SELECT count(*) FROM founder_alerts"),
    ("e-mails", "SELECT count(*) FROM email_outbox"),
    ("scheduled jobs", "SELECT count(*) FROM scheduled_jobs"),
    ("platform-admin rows", "SELECT count(*) FROM platform_admins"),
    ("users with no tenant", "SELECT count(*) FROM users WHERE tenant_id IS NULL"),
)

# What pytest's summary names the failure: there is no test to point at.
NODE_ID = "tests/leftover_count.py::left_behind_by_this_run"

_before: dict[str, int] | None = None
_after: dict[str, int] | None = None
_by_the_runner = False


def take() -> dict[str, int] | None:
    """The six counts, or None when they can't be read (no database, or one
    without these tables yet). Never raises: a count must not stop a run."""
    try:
        from docflow_core.db import platform_session
        from sqlalchemy import text

        with platform_session() as session:
            return {label: int(session.execute(text(query)).scalar_one()) for label, query in COUNTS}
    except Exception:  # noqa: BLE001 -- reported as "not read", never raised
        return None


def session_start() -> None:
    global _before, _after, _by_the_runner
    # The flag tests/suite_lock.py reads (parallel_run); read here directly,
    # because test_leftover_count.py loads this file on its own.
    _by_the_runner = os.environ.get("DOCFLOW_TEST_PARALLEL_RUN") == "1"
    _before, _after = (None if _by_the_runner else take()), None


def moved() -> list[str]:
    """The counts that differ between the start and the end of the run."""
    if _before is None or _after is None:
        return []
    return [label for label, _query in COUNTS if _after[label] != _before[label]]


def failed() -> bool:
    """True unless the run can show `+0` on every count. A run whose counts
    were never read (no database) has nothing to show and does not fail."""
    if _before is None:
        return False
    return _after is None or bool(moved())


def session_finish(session: Any) -> None:
    """pytest_sessionfinish: read the counts again, and fail a run that
    passed its tests but left something behind."""
    global _after
    if _before is not None:
        _after = take()
    if failed() and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def line() -> str | None:
    """`left behind by this run (before -> after): tenants 34 -> 34 (+0), ...`"""
    if _by_the_runner:
        return "left behind by this run: counted by the parallel runner, before and after all three suites"
    if _before is None:
        return None
    if _after is None:
        return "left behind by this run: not read at the end of the run"
    parts = [
        f"{label} {_before[label]} -> {_after[label]} ({_after[label] - _before[label]:+d})"
        for label, _query in COUNTS
    ]
    return "left behind by this run (before -> after): " + ", ".join(parts)


def verdict() -> str | None:
    """Why the run failed on its counts, or None when it did not."""
    if not failed():
        return None
    if _after is None:
        return "FAILED: the counts read at the start could not be read again, so this run can't show +0"
    return "FAILED: this run did not leave the database as it found it: " + ", ".join(moved())


def report(terminalreporter: Any) -> None:
    text = line()
    if text is None:
        return
    terminalreporter.write_line(text)
    why = verdict()
    if why is not None:
        terminalreporter.write_line(why, red=True, bold=True)
        # Counted in pytest's last line ("... 1 error"), which is the line a
        # run is reported by.
        terminalreporter.stats.setdefault("error", []).append(
            pytest.TestReport(
                nodeid=NODE_ID,
                location=("tests/leftover_count.py", None, "left_behind_by_this_run"),
                keywords={},
                outcome="failed",
                longrepr=f"{text}\n{why}",
                when="teardown",
            )
        )
    if os.environ.get("GITHUB_ACTIONS") == "true":
        level = "notice" if why is None else "error"
        body = "%0A".join(part.replace("%", "%25") for part in (text, why) if part is not None)
        print(f"::{level} title=api leftover count::{body}", flush=True)
