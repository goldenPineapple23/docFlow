"""
Every alert type the code raises is registered in founder_alerts.ALERT_TYPES
(founder's condition on routing_model_failure, 2026-10-01, "so M6 can't
recur"). Review M6 was exactly this: `rollup_stale` was raised by the rollup
but never registered, so raising it threw ValueError inside the rollup.

This half reads the source; the other half raises every registered type into
the real table (apps/worker/tests/test_dispatch_db.py,
test_every_registered_alert_type_can_be_raised_end_to_end).
"""

from __future__ import annotations

import re
from pathlib import Path

from docflow_core.founder_alerts import ALERT_TYPES

REPO = Path(__file__).resolve().parents[3]
SOURCES = [
    REPO / "packages" / "core" / "docflow_core",
    REPO / "apps" / "api" / "app",
    REPO / "apps" / "worker" / "app",
]
RAISED = re.compile(r"alert_type\s*=\s*\"([a-z_]+)\"")


def _raised_types() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for root in SOURCES:
        for path in root.rglob("*.py"):
            for match in RAISED.finditer(path.read_text(encoding="utf-8")):
                found.setdefault(match.group(1), []).append(str(path.relative_to(REPO)))
    return found


def test_every_alert_type_the_code_raises_is_registered():
    raised = _raised_types()
    assert raised, "the scan found no alert_type at all -- the pattern no longer matches the code"
    missing = {t: where for t, where in raised.items() if t not in ALERT_TYPES}
    assert not missing, f"raised but not in founder_alerts.ALERT_TYPES (review M6): {missing}"


def test_the_four_stage_3d_alerts_and_rollup_stale_are_registered():
    for alert_type in (
        "model_api_failure",
        "model_api_recovered",
        "dispatcher_stopped",
        "routing_model_failure",
        "rollup_stale",
    ):
        assert alert_type in ALERT_TYPES, alert_type
