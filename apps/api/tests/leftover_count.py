"""
What a test run left behind (the Stage 3 checkpoint's list; founder,
2026-10-08 and 2026-10-09; D-197).

Six counts are read when the run starts and again when it ends, and the
difference is printed in pytest's closing summary: tenants, founder alerts,
e-mails, scheduled jobs, platform-admin rows, and users with no tenant.
Counts only, never a row.

**Printed, not a failure, in this PR** (founder, 2026-10-09). It becomes a
failure in the first PR after a staging run of record shows zero.

A full run on a database nothing else writes to (RUNBOOK 1.4: one suite at a
time, with the Fly worker stopped) should read `+0` for each. A run that is
killed prints nothing, and cleans nothing: that is what the staging sweep is
for. In CI the line is also a GitHub annotation, because CI's job logs can't
be read here and annotations can (RUNBOOK 1.6).
"""

from __future__ import annotations

import os
from typing import Any

COUNTS: tuple[tuple[str, str], ...] = (
    ("tenants", "SELECT count(*) FROM tenants"),
    ("founder alerts", "SELECT count(*) FROM founder_alerts"),
    ("e-mails", "SELECT count(*) FROM email_outbox"),
    ("scheduled jobs", "SELECT count(*) FROM scheduled_jobs"),
    ("platform-admin rows", "SELECT count(*) FROM platform_admins"),
    ("users with no tenant", "SELECT count(*) FROM users WHERE tenant_id IS NULL"),
)

_before: dict[str, int] | None = None
_after: dict[str, int] | None = None


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
    global _before, _after
    _before, _after = take(), None


def session_finish() -> None:
    global _after
    if _before is not None:
        _after = take()


def line() -> str | None:
    """`left behind by this run (before -> after): tenants 34 -> 34 (+0), ...`"""
    if _before is None:
        return None
    if _after is None:
        return "left behind by this run: not read at the end of the run"
    parts = [
        f"{label} {_before[label]} -> {_after[label]} ({_after[label] - _before[label]:+d})"
        for label, _query in COUNTS
    ]
    return "left behind by this run (before -> after): " + ", ".join(parts)


def report(terminalreporter: Any) -> None:
    text = line()
    if text is None:
        return
    terminalreporter.write_line(text)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::notice title=api leftover count::{text.replace('%', '%25')}", flush=True)
