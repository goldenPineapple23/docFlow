"""
Every catalog message that tells its reader "DocFlow has been alerted" must
have an alert behind it (D-145).

The catalog is what a customer reads (Section 7.16.5). Until D-145, four
entries -- DOC-008, DOC-009, DOC-017 and INT-004 among them -- made that
promise and nothing raised an alert, which is a false statement to a
customer about what DocFlow has done. This test keeps the promise honest:
a new entry that makes it must be wired to an alert before it can ship.
"""

from __future__ import annotations

import re

from docflow_core.errors import CATALOG
from docflow_core.founder_alerts import ALERT_TYPES, FAILURE_ALERTS

# Promises kept by an alert raised somewhere other than `raise_for_failure`,
# with the alert type that keeps each one.
ALERTED_ELSEWHERE = {
    "EXP-004": "export_integrity_failure",  # export_jobs
    "INT-007": "abuse_ceiling_tripped",  # intake_gate (also cost_breaker_tripped)
    # Shown when a customer tries to release a founder-only hold; each such
    # hold raised its own alert when it happened: abuse_ceiling_tripped,
    # cost_breaker_tripped (intake_gate) or unverified_sender_held (D-145).
    "QUA-001": "abuse_ceiling_tripped",
    # The worker raises it when buyer identification, matching or duplicate
    # detection didn't finish on a document (Phase 5.5, D-158).
    "VAL-016": "pipeline_step_failed",
}

PROMISE = re.compile(r"(has|have) (already )?been alerted", re.IGNORECASE)


def _promising_codes() -> set[str]:
    return {
        code
        for code, entry in CATALOG.items()
        if PROMISE.search(entry.message) or PROMISE.search(entry.action)
    }


def test_every_catalog_promise_of_an_alert_is_kept():
    unkept = _promising_codes() - set(FAILURE_ALERTS) - set(ALERTED_ELSEWHERE)
    assert not unkept, (
        f"{sorted(unkept)} tell the reader DocFlow has been alerted, but nothing raises an "
        "alert for them. Add each to founder_alerts.FAILURE_ALERTS (and call "
        "raise_for_failure where it happens), or change the wording."
    )


def test_every_mapped_code_and_alert_type_exists():
    for code, alert_type in {**FAILURE_ALERTS, **ALERTED_ELSEWHERE}.items():
        assert code in CATALOG, code
        assert alert_type in ALERT_TYPES, alert_type


def test_the_mapping_only_holds_codes_that_make_the_promise():
    # A code in FAILURE_ALERTS whose wording no longer promises anything
    # would page the founder for a failure the customer was told nothing about.
    assert set(FAILURE_ALERTS) <= _promising_codes()
