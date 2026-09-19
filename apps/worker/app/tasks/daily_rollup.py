"""
The nightly metrics rollup (CLAUDE.md Section 7.15.3; D-121). Celery beat
sends this once a day; the work is `docflow_core.metrics.run_rollup`, so its
tests need no queue.

It recomputes the last two days for every tenant -- yesterday has to be
re-closed in every timezone, and today is topped up -- and records the run in
`rollup_runs`. Re-running a day overwrites it, so a missed night is caught up
by the next one, and the founder's "recompute" button is the same code.
"""

from __future__ import annotations

import logging

from docflow_core.metrics import run_rollup

from app.celery_app import celery_app

logger = logging.getLogger(__name__)

# Two days, not one: see the module docstring.
NIGHTLY_DAYS = 2


@celery_app.task(name="docflow.run_daily_rollup")
def run_daily_rollup(days: int = NIGHTLY_DAYS, trigger: str = "nightly") -> int:
    result = run_rollup(days=days, trigger=trigger)
    # Counts only -- never a tenant's numbers (Section 7.10).
    logger.info(
        "daily_rollup_complete tenants=%d rows=%d", result["tenants"], result["rows_written"]
    )
    return int(result["rows_written"])
