"""
The nightly rollup's three alerts without a database (Phase 5.5 Stage 5, M7;
DECISIONS.md D-214): the drift rule's edges, the types' registration, and
that one failing check never stops the others. The worker suite proves the
SQL, the policies and the e-mails on the real database
(apps/worker/tests/test_rollup_alerts_db.py).

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from uuid import uuid4

import pytest

from docflow_core import db, founder_alerts, metrics, rollup_alerts
from docflow_core.constants import CONFIDENCE_DRIFT_MARGIN, CONFIDENCE_DRIFT_MIN_DOCUMENTS

N = CONFIDENCE_DRIFT_MIN_DOCUMENTS


def _windows(recent_mean: str, recent_n: int, baseline_mean: str = "0.90", baseline_n: int = N) -> dict:
    return {
        "recent_sum": Decimal(recent_mean) * recent_n,
        "recent_count": recent_n,
        "baseline_sum": Decimal(baseline_mean) * baseline_n,
        "baseline_count": baseline_n,
    }


def test_the_minimum_is_twenty_documents_and_the_margin_five_points():
    assert (CONFIDENCE_DRIFT_MIN_DOCUMENTS, CONFIDENCE_DRIFT_MARGIN) == (20, 0.05)
    assert (rollup_alerts.RECENT_DAYS, rollup_alerts.BASELINE_DAYS) == (7, 30)


def test_a_drop_of_more_than_the_margin_is_a_drift():
    assert rollup_alerts.drifted(_windows("0.84", N)) == {
        "mean_7d": "0.8400",
        "mean_30d": "0.9000",
        "margin": "0.05",
        "documents_7d": N,
        "documents_30d": N,
        "min_documents": N,
    }
    assert rollup_alerts.drifted(_windows("0.8499", N)) is not None


def test_a_drop_of_exactly_the_margin_or_less_is_not():
    assert rollup_alerts.drifted(_windows("0.85", N)) is None
    assert rollup_alerts.drifted(_windows("0.90", N)) is None
    assert rollup_alerts.drifted(_windows("0.95", N)) is None  # a rise


@pytest.mark.parametrize(("recent_n", "baseline_n", "raised"), [
    (N, N, True),
    (N - 1, N, False),
    (N, N - 1, False),
    (N - 1, N - 1, False),
    (0, N, False),
])
def test_both_windows_need_the_minimum_number_of_documents(recent_n, baseline_n, raised):
    assert (rollup_alerts.drifted(_windows("0.50", recent_n, baseline_n=baseline_n)) is not None) is raised


def test_the_three_types_are_registered_with_titles():
    for alert_type in ("review_backlog", "confidence_drift", "staging_ttl_elapsed"):
        assert founder_alerts.ALERT_TYPES[alert_type]


def test_one_failing_check_is_logged_by_type_and_the_others_still_run(monkeypatch, caplog):
    tenants = [uuid4(), uuid4()]
    ran: list[tuple[str, object]] = []

    @contextmanager
    def fake_session(*args):
        yield object()

    def broken(session, tenant_id):
        ran.append(("backlog", tenant_id))
        raise RuntimeError("Acme Test secret 7731")

    def drift(session, tenant_id):
        ran.append(("drift", tenant_id))
        return tenant_id == tenants[1]

    def staging(session):
        ran.append(("staging", None))
        return 2

    monkeypatch.setattr(db, "tenant_session", fake_session)
    monkeypatch.setattr(db, "rollup_session", fake_session)
    monkeypatch.setattr(rollup_alerts, "review_backlog", broken)
    monkeypatch.setattr(rollup_alerts, "confidence_drift", drift)
    monkeypatch.setattr(rollup_alerts, "staging_ttl", staging)

    with caplog.at_level("ERROR"):
        raised = metrics.raise_attention_alerts(tenants)

    assert raised == {"review_backlog": 0, "confidence_drift": 1, "staging_ttl_elapsed": 2}
    assert ran == [
        ("backlog", tenants[0]), ("drift", tenants[0]),
        ("backlog", tenants[1]), ("drift", tenants[1]),
        ("staging", None),
    ]
    assert caplog.text.count("rollup_alert_check_failed check=review_backlog") == 2
    assert "error=RuntimeError" in caplog.text and "Acme Test secret" not in caplog.text


def test_a_staging_check_that_fails_is_logged_too(monkeypatch, caplog):
    @contextmanager
    def no_function(*args):
        raise RuntimeError("function staging_intakes_past_ttl does not exist")
        yield  # pragma: no cover

    monkeypatch.setattr(db, "rollup_session", no_function)
    with caplog.at_level("ERROR"):
        raised = metrics.raise_attention_alerts([])
    assert raised["staging_ttl_elapsed"] == 0
    assert "rollup_alert_check_failed check=staging_ttl_elapsed error=RuntimeError" in caplog.text
