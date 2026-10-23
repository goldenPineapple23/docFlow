"""
The nightly rollup's three alerts against the real database (Phase 5.5
Stage 5, M7; DECISIONS.md D-214; CLAUDE.md Section 7.15.3 and 7.15.2 Step 1).

review_backlog and confidence_drift run in the tenant's own session, as this
suite's login (docflow_worker), exactly as the rollup runs them. The staging
alert runs in the rollup's own session through migration 0039's function and
its widened rollup_raise policy; the founder asked for a test of each of its
dedup on a second run, its re-raise after an acknowledgement and its e-mail
row under the unchanged rollup_read_own and rollup_enqueue policies.

What a test leaves: nothing. Each test makes its own tenant and staged
intakes and removes them, and deletes only the alerts it raised, with their
e-mails. The whole-table staging check is never run here: on docflow-staging
it would raise alerts for the founder's real staged intakes. The tests read
the function's rows and keep only their own intakes, then raise each through
the same call the check makes (`rollup_alerts.raise_staging_alert`).

The founder-alert e-mail goes to a fake address for these tests, so the
outbox rows they write (and delete) can't reach anyone.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from docflow_core import metrics, rollup_alerts
from docflow_core.config import get_settings
from docflow_core.constants import (
    CONFIDENCE_DRIFT_MIN_DOCUMENTS,
    REVIEW_BACKLOG_ALERT_DAYS,
    STAGING_TTL_DAYS,
)
from docflow_core.db import platform_session, rollup_session, tenant_session
from sqlalchemy import text

from tests.conftest import requires_m7_schema
from tests.db_helpers import WorkerTestTenant

pytestmark = requires_m7_schema

FAKE_FOUNDER = "acme-test-founder-m7@example.test"


@pytest.fixture(autouse=True)
def fake_founder_address(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("FOUNDER_ALERT_EMAIL", FAKE_FOUNDER)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _alerts(dedupe_key: str) -> list[dict[str, Any]]:
    with platform_session() as session:
        rows = session.execute(
            text(
                "SELECT a.id, a.type, a.severity, a.tenant_id, a.payload, a.acknowledged_at, "
                "       e.to_address, e.tenant_id AS email_tenant_id "
                "FROM founder_alerts a LEFT JOIN email_outbox e ON e.id = a.email_outbox_id "
                "WHERE a.dedupe_key = :k ORDER BY a.created_at"
            ),
            {"k": dedupe_key},
        ).mappings().all()
    return [dict(r) for r in rows]


def _acknowledge(alert_id: UUID, user_id: UUID) -> None:
    with platform_session() as session:
        session.execute(
            text("UPDATE founder_alerts SET acknowledged_at = now(), acknowledged_by = :u WHERE id = :id"),
            {"id": str(alert_id), "u": str(user_id)},
        )


def _delete_alerts(dedupe_key: str) -> None:
    """The alerts first (they point at their e-mails), then their e-mails."""
    with platform_session() as session:
        outbox = [
            r[0]
            for r in session.execute(
                text("DELETE FROM founder_alerts WHERE dedupe_key = :k RETURNING email_outbox_id"),
                {"k": dedupe_key},
            )
            if r[0] is not None
        ]
        for outbox_id in outbox:
            session.execute(text("DELETE FROM email_outbox WHERE id = :id"), {"id": str(outbox_id)})


# ── Review backlog ──────────────────────────────────────────────────────────


def _review_document(
    tenant: WorkerTestTenant, *, waited_hours: int, test_batch: bool = False, deleted: bool = False
) -> UUID:
    """A document in needs_review that reached review `waited_hours` ago."""
    document_id = uuid4()
    with platform_session() as session:
        session.execute(
            text(
                "INSERT INTO documents (id, tenant_id, original_filename, storage_path, source, status, "
                "content_sha256, raw_json, created_at, processed_at, is_test_batch, deleted_at) "
                "VALUES (:id, :t, 'po.txt', :path, 'upload', 'needs_review', :sha, "
                "        CAST('{\"header\": {}}' AS jsonb), now() - make_interval(hours => :h + 1), "
                "        now() - make_interval(hours => :h), :tb, CASE WHEN :del THEN now() END)"
            ),
            {
                "id": str(document_id),
                "t": str(tenant.tenant_id),
                "path": f"tenants/{tenant.tenant_id}/acme-test/{document_id}.txt",
                "sha": document_id.hex * 2,
                "h": waited_hours,
                "tb": test_batch,
                "del": deleted,
            },
        )
    return document_id


def _backlog(tenant: WorkerTestTenant) -> bool:
    with tenant_session(tenant.tenant_id) as session:
        return rollup_alerts.review_backlog(session, tenant.tenant_id)


def _set_status(tenant: WorkerTestTenant, status: str) -> None:
    with platform_session() as session:
        session.execute(
            text("UPDATE tenants SET status = :s WHERE id = :t"), {"s": status, "t": str(tenant.tenant_id)}
        )


def test_a_document_waiting_longer_than_the_threshold_raises_the_backlog_alert():
    over, under = (REVIEW_BACKLOG_ALERT_DAYS + 1) * 24, (REVIEW_BACKLOG_ALERT_DAYS - 1) * 24
    with WorkerTestTenant("Acme Test M7 Backlog") as tenant:
        key = f"review_backlog:{tenant.tenant_id}"
        try:
            _review_document(tenant, waited_hours=under)
            assert _backlog(tenant) is False
            oldest = _review_document(tenant, waited_hours=over)
            _review_document(tenant, waited_hours=over - 1)
            assert _backlog(tenant) is True
            (alert,) = _alerts(key)
            assert (alert["type"], alert["severity"], str(alert["tenant_id"])) == (
                "review_backlog", "warning", str(tenant.tenant_id)
            )
            assert alert["payload"] == {
                "documents_waiting": 2,
                "oldest_waiting_days": REVIEW_BACKLOG_ALERT_DAYS + 1,
                "oldest_document_id": str(oldest),
                "threshold_days": REVIEW_BACKLOG_ALERT_DAYS,
            }
            assert alert["to_address"] == FAKE_FOUNDER
        finally:
            _delete_alerts(key)


def test_deleted_and_test_batch_documents_never_count_toward_the_backlog():
    over = (REVIEW_BACKLOG_ALERT_DAYS + 1) * 24
    with WorkerTestTenant("Acme Test M7 Backlog Excluded") as tenant:
        key = f"review_backlog:{tenant.tenant_id}"
        try:
            _review_document(tenant, waited_hours=over, deleted=True)
            _review_document(tenant, waited_hours=over, test_batch=True)
            assert _backlog(tenant) is False
            assert _alerts(key) == []
        finally:
            _delete_alerts(key)


@pytest.mark.parametrize(("status", "checked"), [("cancelling", True), ("suspended", False)])
def test_the_backlog_covers_active_and_cancelling_tenants_only(status, checked):
    with WorkerTestTenant("Acme Test M7 Backlog Status") as tenant:
        key = f"review_backlog:{tenant.tenant_id}"
        try:
            _review_document(tenant, waited_hours=(REVIEW_BACKLOG_ALERT_DAYS + 1) * 24)
            _set_status(tenant, status)
            assert _backlog(tenant) is checked
        finally:
            _delete_alerts(key)


def test_another_tenants_waiting_document_never_counts():
    with (
        WorkerTestTenant("Acme Test M7 Backlog A") as quiet,
        WorkerTestTenant("Acme Test M7 Backlog B") as busy,
    ):
        keys = [f"review_backlog:{quiet.tenant_id}", f"review_backlog:{busy.tenant_id}"]
        try:
            _review_document(busy, waited_hours=(REVIEW_BACKLOG_ALERT_DAYS + 1) * 24)
            assert _backlog(quiet) is False
            assert _backlog(busy) is True
            assert _alerts(keys[0]) == []
        finally:
            for key in keys:
                _delete_alerts(key)


def test_the_open_backlog_alert_collapses_a_second_run_and_comes_back_after_it_is_acknowledged():
    with WorkerTestTenant("Acme Test M7 Backlog Dedup") as tenant:
        key = f"review_backlog:{tenant.tenant_id}"
        try:
            _review_document(tenant, waited_hours=(REVIEW_BACKLOG_ALERT_DAYS + 1) * 24)
            assert _backlog(tenant) is True
            assert _backlog(tenant) is False
            (first,) = _alerts(key)
            _acknowledge(first["id"], tenant.user_id)
            assert _backlog(tenant) is True
            assert [a["acknowledged_at"] is None for a in _alerts(key)] == [False, True]
        finally:
            _delete_alerts(key)


# ── Confidence drift ────────────────────────────────────────────────────────


def _metric_days(tenant: WorkerTestTenant, days: dict[int, tuple[str, int]]) -> None:
    """Stored rollup days, keyed by how many days before the tenant's today
    (its own timezone, the rollup's day boundary), each (confidence_sum,
    confidence_count)."""
    with platform_session() as session:
        for days_ago, (total, count) in days.items():
            session.execute(
                text(
                    "INSERT INTO tenant_daily_metrics (tenant_id, day, confidence_sum, confidence_count) "
                    "SELECT t.id, (now() AT TIME ZONE coalesce(nullif(t.timezone, ''), 'UTC'))::date - :ago, "
                    "       :s, :c FROM tenants t WHERE t.id = :t"
                ),
                {"t": str(tenant.tenant_id), "ago": days_ago, "s": Decimal(total), "c": count},
            )


def _spread(first: int, last: int, mean: str, documents: int) -> dict[int, tuple[str, int]]:
    """`documents` documents at `mean` confidence, all on day `first`, plus
    empty days up to `last` -- the windows count documents, not days."""
    days: dict[int, tuple[str, int]] = {d: ("0", 0) for d in range(first, last + 1)}
    days[first] = (str(Decimal(mean) * documents), documents)
    return days


def _drift(tenant: WorkerTestTenant) -> bool:
    with tenant_session(tenant.tenant_id) as session:
        return rollup_alerts.confidence_drift(session, tenant.tenant_id)


@pytest.mark.parametrize(
    ("recent_mean", "recent_n", "baseline_n", "raised"),
    [
        ("0.84", CONFIDENCE_DRIFT_MIN_DOCUMENTS, CONFIDENCE_DRIFT_MIN_DOCUMENTS, True),
        # A drop of exactly the margin is not a drift.
        ("0.85", CONFIDENCE_DRIFT_MIN_DOCUMENTS, CONFIDENCE_DRIFT_MIN_DOCUMENTS, False),
        # One document short of the minimum in either window: nothing.
        ("0.50", CONFIDENCE_DRIFT_MIN_DOCUMENTS - 1, CONFIDENCE_DRIFT_MIN_DOCUMENTS, False),
        ("0.50", CONFIDENCE_DRIFT_MIN_DOCUMENTS, CONFIDENCE_DRIFT_MIN_DOCUMENTS - 1, False),
    ],
)
def test_drift_compares_the_last_seven_days_with_days_8_to_37(recent_mean, recent_n, baseline_n, raised):
    with WorkerTestTenant("Acme Test M7 Drift") as tenant:
        key = f"confidence_drift:{tenant.tenant_id}"
        try:
            days = {**_spread(8, 37, "0.90", baseline_n), **_spread(1, 7, recent_mean, recent_n)}
            _metric_days(tenant, days)
            assert _drift(tenant) is raised
            if raised:
                (alert,) = _alerts(key)
                assert alert["payload"] == {
                    "mean_7d": "0.8400",
                    "mean_30d": "0.9000",
                    "margin": "0.05",
                    "documents_7d": CONFIDENCE_DRIFT_MIN_DOCUMENTS,
                    "documents_30d": CONFIDENCE_DRIFT_MIN_DOCUMENTS,
                    "min_documents": CONFIDENCE_DRIFT_MIN_DOCUMENTS,
                }
            else:
                assert _alerts(key) == []
        finally:
            _delete_alerts(key)


def test_today_and_days_beyond_37_are_outside_both_windows():
    """Low-confidence documents today (not a complete day) and on day 38
    change nothing; day 7 belongs to the recent window and day 8 to the
    baseline, with no overlap."""
    n = CONFIDENCE_DRIFT_MIN_DOCUMENTS
    with WorkerTestTenant("Acme Test M7 Drift Edges") as tenant:
        key = f"confidence_drift:{tenant.tenant_id}"
        try:
            at = {"0.00": "0", "0.90": str(Decimal("0.90") * n), "0.99": str(Decimal("0.99") * n)}
            _metric_days(
                tenant, {0: (at["0.00"], n), 38: (at["0.99"], n), 7: (at["0.90"], n), 8: (at["0.90"], n)}
            )
            with tenant_session(tenant.tenant_id) as session:
                windows = rollup_alerts.drift_windows(session, tenant.tenant_id)
            assert (windows["recent_count"], windows["baseline_count"]) == (n, n)
            assert Decimal(windows["recent_sum"]) == Decimal(windows["baseline_sum"]) == Decimal("0.90") * n
            assert _drift(tenant) is False
        finally:
            _delete_alerts(key)


# ── Staging TTL ─────────────────────────────────────────────────────────────


def _intake(created_by: UUID, *, age_days: int, linked_to: UUID | None = None, deleted: bool = False,
            files: int = 1) -> UUID:
    intake_id = uuid4()
    with platform_session() as session:
        session.execute(
            text(
                "INSERT INTO onboarding_intakes (id, prospect_name, contact_email, created_by, created_at, "
                "linked_tenant_id, deleted_at) VALUES (:id, 'Acme Test Prospect M7', "
                "'acme-test-prospect@example.test', :by, now() - make_interval(days => :age), :linked, "
                "CASE WHEN :del THEN now() END)"
            ),
            {
                "id": str(intake_id),
                "by": str(created_by),
                "age": age_days,
                "linked": linked_to and str(linked_to),
                "del": deleted,
            },
        )
        for n in range(files):
            session.execute(
                text(
                    "INSERT INTO onboarding_intake_files (intake_id, original_filename, storage_path, "
                    "sha256, byte_size, detected_type) VALUES (:i, 'catalog.csv', :p, :h, 10, 'csv')"
                ),
                {"i": str(intake_id), "p": f"staging/{intake_id}/{n}.csv", "h": uuid4().hex * 2},
            )
    return intake_id


def _remove_intakes(ids: list[UUID]) -> None:
    with platform_session() as session:
        for intake_id in ids:
            params = {"i": str(intake_id)}
            session.execute(text("DELETE FROM onboarding_intake_files WHERE intake_id = :i"), params)
            session.execute(text("DELETE FROM onboarding_intakes WHERE id = :i"), params)


def _past_ttl(ids: list[UUID]) -> dict[str, dict[str, Any]]:
    """The function's rows for these intakes only, read in the rollup's session."""
    wanted = {str(i) for i in ids}
    with rollup_session() as session:
        return {
            str(row["intake_id"]): row
            for row in rollup_alerts.staged_past_ttl(session)
            if str(row["intake_id"]) in wanted
        }


def test_an_intake_unlinked_past_the_ttl_is_found_and_nothing_younger_linked_or_deleted():
    with WorkerTestTenant("Acme Test M7 Staging") as tenant:
        old = _intake(tenant.user_id, age_days=STAGING_TTL_DAYS + 1, files=2)
        young = _intake(tenant.user_id, age_days=STAGING_TTL_DAYS - 1)
        linked = _intake(tenant.user_id, age_days=STAGING_TTL_DAYS + 1, linked_to=tenant.tenant_id)
        gone = _intake(tenant.user_id, age_days=STAGING_TTL_DAYS + 1, deleted=True)
        try:
            found = _past_ttl([old, young, linked, gone])
            assert list(found) == [str(old)]
            assert found[str(old)]["files"] == 2
            assert int(found[str(old)]["days_unlinked"]) == STAGING_TTL_DAYS + 1
            assert set(found[str(old)]) == {"intake_id", "files", "staged_on", "days_unlinked"}
        finally:
            _remove_intakes([old, young, linked, gone])


def test_the_staging_alert_is_raised_deduped_re_raised_and_e_mailed_under_the_rollups_policies():
    """Through the rollup's own session (docflow_worker, app.rollup): the
    widened rollup_raise lets the insert through; the open-key index refuses
    a second one with no SELECT policy needed; after an acknowledgement the
    next run raises it again; and rollup_enqueue writes its tenant-less
    e-mail. The intake's rows are untouched, and the prospect is never named."""
    with WorkerTestTenant("Acme Test M7 Staging Alert") as tenant:
        intake = _intake(tenant.user_id, age_days=STAGING_TTL_DAYS + 3)
        key = f"staging_ttl:{intake}"
        try:
            (row,) = _past_ttl([intake]).values()
            with rollup_session() as session:
                assert rollup_alerts.raise_staging_alert(session, row) is True
            with rollup_session() as session:
                assert rollup_alerts.raise_staging_alert(session, row) is False  # the open one collapses it
            (alert,) = _alerts(key)
            assert (alert["type"], alert["severity"]) == ("staging_ttl_elapsed", "warning")
            assert alert["tenant_id"] is None
            assert alert["payload"] == {
                "intake_id": str(intake),
                "staged_on": row["staged_on"],
                "days_unlinked": STAGING_TTL_DAYS + 3,
                "files": 1,
                "ttl_days": STAGING_TTL_DAYS,
            }
            assert (alert["to_address"], alert["email_tenant_id"]) == (FAKE_FOUNDER, None)
            assert "Acme Test Prospect" not in str(alert) and "acme-test-prospect" not in str(alert)

            _acknowledge(alert["id"], tenant.user_id)
            with rollup_session() as session:
                assert rollup_alerts.raise_staging_alert(session, row) is True
            assert [a["acknowledged_at"] is None for a in _alerts(key)] == [False, True]

            with platform_session() as session:
                still = session.execute(
                    text(
                        "SELECT i.deleted_at IS NULL, i.linked_tenant_id IS NULL, "
                        "(SELECT count(*) FROM onboarding_intake_files f WHERE f.intake_id = i.id "
                        " AND f.deleted_at IS NULL) FROM onboarding_intakes i WHERE i.id = :i"
                    ),
                    {"i": str(intake)},
                ).one()
            assert tuple(still) == (True, True, 1)
        finally:
            _delete_alerts(key)
            _remove_intakes([intake])


# ── The rollup runs them ────────────────────────────────────────────────────


@pytest.fixture
def staging_check_recorded(monkeypatch) -> list[str]:
    """The whole-table staging check, replaced: never raised for real intakes
    on a shared database (module docstring). So is the nightly run's own
    staleness alert, which would otherwise raise a real `rollup_stale` on a
    database whose last run is old."""
    calls: list[str] = []

    def record(session) -> int:
        calls.append("staging")
        return 0

    monkeypatch.setattr(rollup_alerts, "staging_ttl", record)
    monkeypatch.setattr(metrics, "_raise_stale_alert", lambda session, previous: calls.append("stale"))
    return calls


def _delete_runs(*results: dict[str, Any]) -> None:
    """This test's rollup_runs rows: a run left behind would read as the real
    nightly job's on the dashboard."""
    with platform_session() as session:
        for result in results:
            session.execute(text("DELETE FROM rollup_runs WHERE id = :id"), {"id": str(result["run_id"])})


def test_a_nightly_run_raises_the_alerts_and_a_recompute_does_not(staging_check_recorded):
    with WorkerTestTenant("Acme Test M7 Rollup") as tenant:
        key = f"review_backlog:{tenant.tenant_id}"
        runs: list[dict[str, Any]] = []
        try:
            _review_document(tenant, waited_hours=(REVIEW_BACKLOG_ALERT_DAYS + 1) * 24)
            runs.append(metrics.run_rollup(trigger="manual", tenant_id=tenant.tenant_id))
            assert runs[0]["alerts_raised"] == {} and _alerts(key) == []
            assert "staging" not in staging_check_recorded
            runs.append(metrics.run_rollup(trigger="nightly", tenant_id=tenant.tenant_id))
            assert runs[1]["alerts_raised"] == {
                "review_backlog": 1, "confidence_drift": 0, "staging_ttl_elapsed": 0
            }
            assert len(_alerts(key)) == 1 and "staging" in staging_check_recorded
        finally:
            _delete_alerts(key)
            _delete_runs(*runs)


def test_one_check_that_fails_leaves_the_rollup_ok_and_the_others_running(
    monkeypatch, staging_check_recorded, caplog
):
    def broken(session, tenant_id):
        raise RuntimeError("Acme Test broken check")

    monkeypatch.setattr(rollup_alerts, "review_backlog", broken)
    with WorkerTestTenant("Acme Test M7 Rollup Broken") as tenant:
        with caplog.at_level("ERROR"):
            result = metrics.run_rollup(trigger="nightly", tenant_id=tenant.tenant_id)
        with platform_session() as session:
            ok = session.execute(
                text("SELECT ok FROM rollup_runs WHERE id = :id"), {"id": str(result["run_id"])}
            ).scalar_one()
        assert ok is True
        _delete_runs(result)
    assert result["alerts_raised"] == {"review_backlog": 0, "confidence_drift": 0, "staging_ttl_elapsed": 0}
    assert "staging" in staging_check_recorded
    assert "rollup_alert_check_failed check=review_backlog" in caplog.text
    assert "error=RuntimeError" in caplog.text
    assert "Acme Test broken check" not in caplog.text
