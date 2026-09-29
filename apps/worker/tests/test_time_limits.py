"""
Phase 5.5 Stage 3a (review H5): worker time limits, and the fixes for what a
kill could leave behind. Agreed with the founder before building
(docs/BUILD-STATUS.md, "Stage 3 -- agreed with the founder before building").

The first group needs no database and runs everywhere, Windows included. The
real kill -- a prefork worker, a task that overruns, the worker carrying on
-- is `test_time_limits_prefork.py`, which only Linux can run (CI).
"""

from __future__ import annotations

import contextlib
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from docflow_core import constants, document_status, lifecycle, stuck_documents
from docflow_core.db import platform_session, tenant_session
from docflow_core.external_services import ExternalServiceError
from docflow_core.extraction import EXTRACTION_DEADLINE_SECONDS
from docflow_core.matching import normalize_description
from docflow_core.scheduled_jobs import RUNNING_TIMEOUT_MINUTES
from sqlalchemy import text

from app.celery_app import (
    LIFECYCLE_SWEEP_SECONDS,
    STUCK_SWEEP_SECONDS,
    celery_app,
)
from tests.conftest import requires_timeout_schema
from tests.db_helpers import WorkerTestTenant, model_payload, run_extraction

EXPECTED_LIMITS = {
    "docflow.parse_and_extract": constants.DOCUMENT_TASK_TIME_LIMIT_SECONDS,
    "docflow.generate_export": constants.EXPORT_TASK_TIME_LIMIT_SECONDS,
    "docflow.parse_import": constants.IMPORT_TASK_TIME_LIMIT_SECONDS,
    "docflow.run_daily_rollup": constants.ROLLUP_TASK_TIME_LIMIT_SECONDS,
    "docflow.run_scheduled_jobs": constants.SCHEDULED_JOBS_TASK_TIME_LIMIT_SECONDS,
    "docflow.run_lifecycle_sweep": constants.LIFECYCLE_SWEEP_TASK_TIME_LIMIT_SECONDS,
    "docflow.sweep_stuck_documents": constants.STUCK_SWEEP_TASK_TIME_LIMIT_SECONDS,
}


def _finalized_tasks():
    celery_app.loader.import_default_modules()
    celery_app.finalize()
    return celery_app.tasks


# ── The limits, as Celery will apply them ───────────────────────────────────


def test_the_document_task_has_no_soft_limit_and_a_27_minute_hard_limit():
    """Founder, 2026-09-29. Read from the finalized app, which is what the
    worker uses: Celery treats a task's None as "not set", so a global
    task_soft_time_limit added later would override it -- and this fails."""
    task = _finalized_tasks()["docflow.parse_and_extract"]
    assert task.soft_time_limit is None
    assert task.time_limit == 27 * 60
    assert task.Request == "app.timeouts:DocumentTaskRequest"


def test_the_prefork_test_app_is_never_imported_into_the_test_process():
    """
    tests/prefork_app patches the real document task for the separate worker
    it runs in. Collection has imported every test module by now, so if any of
    them imported it, the limits asserted here would be the test's 3 s.
    """
    assert "tests.prefork_app" not in sys.modules


def test_every_task_has_its_hard_limit_and_none_has_a_soft_limit():
    tasks = _finalized_tasks()
    ours = {name for name in tasks if name.startswith("docflow.")}
    assert ours == set(EXPECTED_LIMITS), "a new task needs a time limit here and in constants.py"
    for name, limit in EXPECTED_LIMITS.items():
        assert (tasks[name].time_limit, tasks[name].soft_time_limit) == (limit, None), name
    assert celery_app.conf.task_soft_time_limit is None
    assert celery_app.conf.task_time_limit is None
    assert celery_app.conf.worker_max_memory_per_child == constants.WORKER_MAX_MEMORY_PER_CHILD_KIB


def test_the_document_task_request_class_resolves():
    from celery.utils.imports import symbol_by_name

    from app.timeouts import DocumentTaskRequest

    assert symbol_by_name("app.timeouts:DocumentTaskRequest") is DocumentTaskRequest


def test_the_limits_are_in_the_order_the_claims_need():
    # The read ends before the kill, and the kill before the claim goes stale.
    assert EXTRACTION_DEADLINE_SECONDS < constants.DOCUMENT_TASK_TIME_LIMIT_SECONDS
    assert constants.DOCUMENT_TASK_TIME_LIMIT_SECONDS < constants.STUCK_PROCESSING_TIMEOUT_MIN * 60
    # A live scheduled-jobs sweep's jobs are never released to another.
    assert constants.SCHEDULED_JOBS_TASK_TIME_LIMIT_SECONDS < RUNNING_TIMEOUT_MINUTES * 60
    # The five-minute sweeps finish before the next one is sent.
    assert constants.STUCK_SWEEP_TASK_TIME_LIMIT_SECONDS < STUCK_SWEEP_SECONDS
    assert constants.LIFECYCLE_SWEEP_TIME_BOX_SECONDS < LIFECYCLE_SWEEP_SECONDS
    # After the box closes, one tenant's Stripe calls (3 x 15 s) still fit.
    from docflow_core.external_services import TIMEOUT_SECONDS

    assert (
        constants.LIFECYCLE_SWEEP_TIME_BOX_SECONDS + 3 * TIMEOUT_SECONDS
        < constants.LIFECYCLE_SWEEP_TASK_TIME_LIMIT_SECONDS
    )


# ── The stuck sweep's decision (pure) ───────────────────────────────────────


@pytest.mark.parametrize(
    ("attempts", "timeouts", "expected"),
    [
        (1, [], ("retry", None)),
        (2, [], ("retry", None)),
        (3, [], ("fail", "worker_stopped")),
        (1, [1], ("retry", None)),  # first timeout on the latest attempt: one retry
        (2, [1], ("fail", "timeout")),  # the retry has run
        (2, [1, 2], ("fail", "timeout")),  # ...and timed out too
        (2, [2], ("retry", None)),  # a crash, then a first timeout
        (3, [3], ("retry", None)),  # the timeout's one retry beats the attempt count
        (4, [3], ("fail", "timeout")),
    ],
)
def test_the_stuck_sweep_decides_by_the_agreed_table(attempts, timeouts, expected):
    assert stuck_documents.decide(attempts, timeouts, max_attempts=3) == expected


def test_the_request_finds_the_ids_positional_or_by_keyword():
    from app.timeouts import DocumentTaskRequest

    tid, did = uuid4(), uuid4()
    ids = DocumentTaskRequest._ids
    assert ids(SimpleNamespace(args=[str(tid), str(did)], kwargs={})) == (tid, did)
    assert ids(SimpleNamespace(args=[], kwargs={"tenant_id": str(tid), "document_id": str(did)})) == (
        tid,
        did,
    )
    assert ids(SimpleNamespace(args=["not-a-uuid"], kwargs={})) == (None, None)


# ── The lifecycle sweep's time box (no database) ────────────────────────────


def test_a_sweep_over_the_time_box_stops_taking_tenants_and_the_next_tick_takes_the_rest(monkeypatch):
    """Founder, 2026-09-29."""
    import app.tasks.lifecycle_sweep as sweep

    due = [uuid4() for _ in range(4)]
    claimed: list[UUID] = []
    clock = {"now": 0.0}

    def claim(_session, tenant_id):
        claimed.append(tenant_id)
        clock["now"] += 100.0  # each tenant takes 100 s of the 240 s box
        return None

    monkeypatch.setattr(sweep, "lifecycle_session", lambda: contextlib.nullcontext())
    monkeypatch.setattr(sweep, "tenant_session", lambda _tid: contextlib.nullcontext())
    monkeypatch.setattr(sweep.lifecycle, "pending_cancels", lambda _s: [])
    monkeypatch.setattr(sweep.lifecycle, "due_for_suspend", lambda _s: [t for t in due if t not in claimed])
    monkeypatch.setattr(sweep.lifecycle, "claim_for_suspend", claim)
    monkeypatch.setattr(sweep.lifecycle, "due_for_ready_to_delete_alert", lambda _s: [])
    monkeypatch.setattr(sweep.lifecycle, "tenants_for_intake_housekeeping", lambda _s: [])
    monkeypatch.setattr(sweep, "_monotonic", lambda: clock["now"])

    first = sweep.run_lifecycle_sweep()
    assert first["stopped_at_time_box"] is True
    assert claimed == due[:3]  # 0 s, 100 s, 200 s taken; at 300 s the box is closed

    clock["now"] = 10_000.0  # the next tick starts its own box
    second = sweep.run_lifecycle_sweep()
    assert second["stopped_at_time_box"] is False
    assert claimed == due


# ── Database: recording a timeout, and the sweep acting on it ───────────────


def _backdate(document_id: UUID, *, attempts: int, timeouts: list[int]) -> None:
    with platform_session() as session:
        session.execute(
            text(
                "UPDATE documents SET status = 'processing', processing_attempts = :a, "
                "timeout_attempts = CAST(:t AS integer[]), "
                "processing_started_at = now() - interval '2 hours', "
                "created_at = now() - interval '2 hours' WHERE id = :id"
            ),
            {"id": str(document_id), "a": attempts, "t": "{" + ",".join(str(n) for n in timeouts) + "}"},
        )


def _document(document_id: UUID) -> dict:
    with platform_session() as session:
        return dict(
            session.execute(
                text("SELECT status, failure_code, timeout_attempts FROM documents WHERE id = :id"),
                {"id": str(document_id)},
            )
            .mappings()
            .one()
        )


def _stuck_alerts(tenant: WorkerTestTenant) -> list[dict]:
    with platform_session() as session:
        return [
            dict(r)
            for r in session.execute(
                text(
                    "SELECT payload, dedupe_key FROM founder_alerts "
                    "WHERE tenant_id = :t AND type = 'document_stuck' ORDER BY created_at"
                ),
                {"t": str(tenant.tenant_id)},
            ).mappings()
        ]


def _record(tenant: WorkerTestTenant, document_id: UUID) -> bool:
    with tenant_session(tenant.tenant_id) as session:
        return document_status.record_timeout(session, document_id)


@requires_timeout_schema
def test_a_timeout_is_recorded_once_per_attempt_and_only_while_processing():
    with WorkerTestTenant("Acme Test Timeout Record") as tenant:
        document_id = tenant.create_pending_document()
        _backdate(document_id, attempts=1, timeouts=[])

        assert _record(tenant, document_id) is True
        # Celery may report a soft and a hard timeout for one attempt.
        assert _record(tenant, document_id) is False
        assert _document(document_id)["timeout_attempts"] == [1]

        with platform_session() as session:
            session.execute(
                text("UPDATE documents SET status = 'failed' WHERE id = :id"), {"id": str(document_id)}
            )
        assert _record(tenant, document_id) is False


@requires_timeout_schema
def test_a_timeout_gets_one_retry_then_doc_022_with_the_cause_and_a_separate_alert_per_cause():
    with WorkerTestTenant("Acme Test Timeout Sweep") as tenant:
        first_timeout = tenant.create_pending_document()
        retried_and_timed_out = tenant.create_pending_document()
        dead_worker = tenant.create_pending_document()
        _backdate(first_timeout, attempts=1, timeouts=[1])
        _backdate(retried_and_timed_out, attempts=2, timeouts=[1, 2])
        _backdate(dead_worker, attempts=3, timeouts=[])

        queued: list[UUID] = []
        result = stuck_documents.sweep_tenant(tenant.tenant_id, lambda _t, d: queued.append(d))

        assert queued == [first_timeout]
        assert set(result.failed) == {retried_and_timed_out, dead_worker}
        for failed in (retried_and_timed_out, dead_worker):
            assert (_document(failed)["status"], _document(failed)["failure_code"]) == ("failed", "DOC-022")
        alerts = _stuck_alerts(tenant)
        # One per cause on the same day: a timeout is never hidden in the other.
        assert sorted(a["payload"]["cause"] for a in alerts) == ["timeout", "worker_stopped"]
        timeout_alert = next(a for a in alerts if a["payload"]["cause"] == "timeout")
        assert timeout_alert["payload"]["timed_out_attempts"] == [1, 2]
        assert timeout_alert["payload"]["attempts"] == 2
        assert ":timeout:" in timeout_alert["dedupe_key"]


# ── Database: a rule's count and the digest, once per document ──────────────


class _Killed(BaseException):
    """Stands in for the worker being killed: not an Exception, so none of the
    task's handlers catches it, exactly like SIGKILL leaves no handler run."""


@requires_timeout_schema
def test_a_rule_that_fires_is_counted_once_even_when_the_first_attempt_is_killed(monkeypatch):
    import app.tasks.parse_and_extract as task_module

    with WorkerTestTenant("Acme Test Rule Count") as tenant:
        item_id, rule_id = uuid4(), uuid4()
        description = "Acme Test Rule Widget"
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO items (id, tenant_id, sku, description, created_at, updated_at) "
                    "VALUES (:id, :t, 'ATR-1', 'Rule widget', now(), now())"
                ),
                {"id": str(item_id), "t": str(tenant.tenant_id)},
            )
            session.execute(
                text(
                    "INSERT INTO learned_rules (id, tenant_id, rule_type, match_key, match_value, status, "
                    "confirmed_by, created_at, updated_at) "
                    "VALUES (:id, :t, 'sku_mapping', :key, :value, 'active', :u, now(), now())"
                ),
                {
                    "id": str(rule_id),
                    "t": str(tenant.tenant_id),
                    "key": normalize_description(description),
                    "value": {"item_id": str(item_id), "sku": "ATR-1"},
                    "u": str(tenant.user_id),
                },
            )
        document_id = tenant.create_pending_document()
        payload = model_payload(lines=[{"sku": "RAW-1", "description": description}])

        real_validate = task_module.validate_document

        def killed(*args, **kwargs):
            raise _Killed()

        monkeypatch.setattr(task_module, "validate_document", killed)
        with pytest.raises(_Killed):
            run_extraction(monkeypatch, tenant, document_id, payload)
        assert _times_applied(rule_id) == 0  # matching ran and reported; nothing counted

        monkeypatch.setattr(task_module, "validate_document", real_validate)
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE documents SET processing_started_at = now() - interval '2 hours' WHERE id = :id"
                ),
                {"id": str(document_id)},
            )
        run_extraction(monkeypatch, tenant, document_id, payload)
        assert _document(document_id)["status"] == "needs_review"
        assert _times_applied(rule_id) == 1  # counted once, not once per attempt


def _times_applied(rule_id: UUID) -> int:
    with platform_session() as session:
        return int(
            session.execute(
                text("SELECT times_applied FROM learned_rules WHERE id = :id"), {"id": str(rule_id)}
            ).scalar_one()
        )


@requires_timeout_schema
def test_the_digest_note_is_written_with_the_move_to_review_and_its_failure_costs_nothing(monkeypatch):
    import app.tasks.parse_and_extract as task_module

    with WorkerTestTenant("Acme Test Digest Note") as tenant:
        noted = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, noted, model_payload(lines=[{}]))
        assert _document(noted)["status"] == "needs_review"
        with platform_session() as session:
            jobs = session.execute(
                text(
                    "SELECT count(*) FROM scheduled_jobs WHERE tenant_id = :t AND job_type = 'review_digest'"
                ),
                {"t": str(tenant.tenant_id)},
            ).scalar_one()
        assert jobs == 1

        def broken(*args, **kwargs):
            raise RuntimeError("digest down")

        monkeypatch.setattr(task_module.review_digest, "note_needs_review", broken)
        unnoted = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, unnoted, model_payload(lines=[{}]))
        assert _document(unnoted)["status"] == "needs_review"


# ── Database: a Stripe cancel still owed after a suspension ─────────────────


def _cancelling_tenant(tenant: WorkerTestTenant) -> None:
    with platform_session() as session:
        tier_id = session.execute(text("SELECT id FROM tiers ORDER BY created_at LIMIT 1")).scalar_one()
        session.execute(
            text(
                "UPDATE tenants SET status = 'cancelling', cancellation_reason = 'customer_requested', "
                "cancellation_effective_at = now() - interval '1 minute', tier_id = :tier, "
                "stripe_subscription_id = 'sub_test_' || left(md5(id::text), 12), "
                "stripe_customer_id = 'cus_test_' || left(md5(id::text), 12) WHERE id = :id"
            ),
            {"id": str(tenant.tenant_id), "tier": str(tier_id)},
        )


def _cancel_state(tenant: WorkerTestTenant) -> dict:
    with platform_session() as session:
        return dict(
            session.execute(
                text(
                    "SELECT status, stripe_cancel_pending_at, stripe_subscription_id "
                    "FROM tenants WHERE id = :id"
                ),
                {"id": str(tenant.tenant_id)},
            )
            .mappings()
            .one()
        )


@requires_timeout_schema
def test_suspend_then_the_cancel_fails_then_reactivate_and_the_next_sweep_does_not_cancel(monkeypatch):
    """Founder, 2026-09-29."""
    import docflow_core.external_services as stripe_calls

    cancelled: list[str] = []

    def failing_cancel(subscription_id: str) -> None:
        raise ExternalServiceError("stripe", "test: cancel failed")

    monkeypatch.setattr(stripe_calls, "cancel_subscription", failing_cancel)
    monkeypatch.setattr(stripe_calls, "void_pending_setup_fee", lambda **_kw: None)

    with WorkerTestTenant("Acme Test Owed Cancel") as tenant:
        _cancelling_tenant(tenant)
        with tenant_session(tenant.tenant_id) as session:
            assert lifecycle.claim_for_suspend(session, tenant.tenant_id) is not None
        assert _cancel_state(tenant)["stripe_cancel_pending_at"] is not None

        assert lifecycle.process_pending_cancel(tenant.tenant_id) == "failed"
        assert _cancel_state(tenant)["stripe_cancel_pending_at"] is not None  # still owed

        with tenant_session(tenant.tenant_id) as session:
            plan = lifecycle.plan_reactivate(session, tenant.tenant_id)
        with tenant_session(tenant.tenant_id) as session:
            lifecycle.complete_reactivate(
                session,
                tenant.tenant_id,
                actor_user_id=tenant.user_id,
                plan=plan,
                billing=lifecycle.ReactivateBilling(
                    subscription_id="sub_test_reactivated",
                    subscription_status="active",
                    current_period_end=None,
                ),
                app_url="http://localhost:3000",
            )
        state = _cancel_state(tenant)
        assert (state["status"], state["stripe_cancel_pending_at"]) == ("active", None)

        monkeypatch.setattr(stripe_calls, "cancel_subscription", cancelled.append)
        assert lifecycle.process_pending_cancel(tenant.tenant_id) == "none"
        assert cancelled == []  # the reactivated subscription is never touched


@requires_timeout_schema
def test_an_owed_cancel_for_a_tenant_no_longer_suspended_is_skipped_and_cleared(monkeypatch):
    """The re-check under the lock: the mark is set but the tenant is active
    (a reactivation that raced the mark), so Stripe is not called."""
    import docflow_core.external_services as stripe_calls

    cancelled: list[str] = []
    monkeypatch.setattr(stripe_calls, "cancel_subscription", cancelled.append)
    monkeypatch.setattr(stripe_calls, "void_pending_setup_fee", lambda **_kw: None)

    with WorkerTestTenant("Acme Test Skipped Cancel") as tenant:
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE tenants SET stripe_subscription_id = 'sub_test_live', "
                    "stripe_cancel_pending_at = now() WHERE id = :id"
                ),
                {"id": str(tenant.tenant_id)},
            )
        assert lifecycle.process_pending_cancel(tenant.tenant_id) == "skipped"
        assert cancelled == []
        assert _cancel_state(tenant)["stripe_cancel_pending_at"] is None


@requires_timeout_schema
def test_an_owed_cancel_that_goes_through_clears_the_mark(monkeypatch):
    import docflow_core.external_services as stripe_calls

    cancelled: list[str] = []
    monkeypatch.setattr(stripe_calls, "cancel_subscription", cancelled.append)
    monkeypatch.setattr(stripe_calls, "void_pending_setup_fee", lambda **_kw: None)

    with WorkerTestTenant("Acme Test Cancel Done") as tenant:
        _cancelling_tenant(tenant)
        with tenant_session(tenant.tenant_id) as session:
            lifecycle.claim_for_suspend(session, tenant.tenant_id)
        subscription = _cancel_state(tenant)["stripe_subscription_id"]

        assert lifecycle.process_pending_cancel(tenant.tenant_id) == "cancelled"
        assert cancelled == [subscription]
        assert _cancel_state(tenant)["stripe_cancel_pending_at"] is None
        assert lifecycle.process_pending_cancel(tenant.tenant_id) == "none"
        assert cancelled == [subscription]
