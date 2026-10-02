"""
The two beat tasks that rested on their guard alone, now run twice at once
against the real database (founder, at the 3d merge; Stage 3e part D;
RUNBOOK 9.3's double-fire table).

Two threads, each with its own connection, released together by a barrier,
as two beats firing the same task would be:

- the scheduled-jobs sweep: each due job runs exactly once (SKIP LOCKED);
- the stuck sweep: each document is failed, returned to waiting, and alerted
  once (compare-and-set and dedupe). One thing it can do twice, and the test
  says so rather than hiding it: a document due a *retry* may be put on the
  queue by both sweeps, since a retry changes no status for a compare-and-set
  to guard. The document task's claim is what makes the second job a no-op,
  and that is proven here too.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from docflow_core import document_status, model_runs, scheduled_jobs, stuck_documents
from docflow_core.db import platform_session, tenant_session
from sqlalchemy import text

from tests.conftest import requires_documents_schema, requires_stage3d_schema
from tests.db_helpers import WorkerTestTenant

pytestmark = [requires_documents_schema, requires_stage3d_schema]


def _twice_at_once(fn: Callable[[], Any]) -> list[Any]:
    """Run fn in two threads released together; return both results, or
    raise the first error either one hit."""
    barrier = threading.Barrier(2)
    results: list[Any] = [None, None]
    errors: list[BaseException] = []

    def run(i: int) -> None:
        try:
            barrier.wait(timeout=30)
            results[i] = fn()
        except BaseException as exc:  # noqa: BLE001 -- reported below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "a sweep never finished"
    if errors:
        raise errors[0]
    return results


def _count(sql: str, **params: Any) -> int:
    with platform_session() as session:
        return int(session.execute(text(sql), params).scalar_one())


# ── the scheduled-jobs sweep ────────────────────────────────────────────────


def test_two_scheduled_job_sweeps_at_once_run_each_due_job_exactly_once(monkeypatch):
    ran: list[UUID] = []
    lock = threading.Lock()

    def handler(_session, job):
        with lock:
            ran.append(job.id)
        time.sleep(0.2)  # hold the job long enough for the other sweep to look

    monkeypatch.setitem(scheduled_jobs.HANDLERS, "first_week_checkin", handler)
    with WorkerTestTenant("Acme Test Two Sweeps Jobs") as tenant:
        job_ids = [uuid4() for _ in range(6)]
        try:
            with platform_session() as session:
                for job_id in job_ids:
                    session.execute(
                        text(
                            "INSERT INTO scheduled_jobs (id, tenant_id, job_type, run_at, dedupe_key) "
                            "VALUES (:id, :t, 'first_week_checkin', now() - interval '1 minute', :k)"
                        ),
                        {"id": str(job_id), "t": str(tenant.tenant_id), "k": f"test:{job_id}"},
                    )

            succeeded = _twice_at_once(lambda: scheduled_jobs.run_due_jobs(tenant_id=tenant.tenant_id))

            assert sorted(ran) == sorted(job_ids)  # every job, and none twice
            assert sum(succeeded) == len(job_ids)
            with platform_session() as session:
                rows = session.execute(
                    text("SELECT status, attempts FROM scheduled_jobs WHERE tenant_id = :t"),
                    {"t": str(tenant.tenant_id)},
                ).all()
            assert sorted(rows) == [("done", 1)] * len(job_ids)
        finally:
            with platform_session() as session:
                session.execute(
                    text("DELETE FROM scheduled_jobs WHERE tenant_id = :t"), {"t": str(tenant.tenant_id)}
                )


# ── the stuck sweep ─────────────────────────────────────────────────────────


def _backdate(document_id: UUID, *, status: str, attempts: int, dispatched: bool = False) -> None:
    with platform_session() as session:
        session.execute(
            text(
                "UPDATE documents SET status = :s, processing_attempts = :a, "
                "processing_started_at = now() - interval '2 hours', "
                "created_at = now() - interval '2 hours', "
                "dispatched_at = CASE WHEN :d THEN now() - interval '2 hours' ELSE dispatched_at END "
                "WHERE id = :id"
            ),
            {"id": str(document_id), "s": status, "a": attempts, "d": dispatched},
        )


def test_two_stuck_sweeps_at_once_change_and_alert_each_document_once():
    with WorkerTestTenant("Acme Test Two Sweeps Stuck") as tenant:
        retry = tenant.create_pending_document()
        give_up = tenant.create_pending_document()
        lost = tenant.create_pending_document()
        _backdate(retry, status="processing", attempts=1)
        _backdate(give_up, status="processing", attempts=3)
        _backdate(lost, status="pending", attempts=0, dispatched=True)
        # A call the dead attempt started and never finished (D-163): the
        # sweep closes it with one outcome row, whoever gets there first.
        with tenant_session(tenant.tenant_id) as session:
            model_runs.record_started(
                session,
                tenant.tenant_id,
                give_up,
                run_kind="extraction",
                model_id=model_runs.EXTRACTION_MODEL,
                counted_input_tokens=1000,
            )

        queued: list[UUID] = []
        lock = threading.Lock()

        def enqueue(_tenant_id: UUID, document_id: UUID) -> None:
            with lock:
                queued.append(document_id)

        first, second = _twice_at_once(lambda: stuck_documents.sweep_tenant(tenant.tenant_id, enqueue))

        # Failed once, returned to waiting once.
        assert first.failed + second.failed == [give_up]
        assert first.lost_jobs + second.lost_jobs == [lost]
        row = _count(
            "SELECT count(*) FROM documents "
            "WHERE id = :id AND status = 'failed' AND failure_code = 'DOC-022'",
            id=str(give_up),
        )
        assert row == 1
        # One outcome row for the one lost call.
        assert _count(
            "SELECT count(*) FROM extraction_runs WHERE document_id = :id AND started_run_id IS NOT NULL",
            id=str(give_up),
        ) == 1
        # One alert for the failure, one for the lost job (once a day).
        assert _count(
            "SELECT count(*) FROM founder_alerts WHERE tenant_id = :t AND type = 'document_stuck'",
            t=str(tenant.tenant_id),
        ) == 2

        # The retry: queued by one sweep or both (nothing changes status for
        # a compare-and-set to guard) -- and only one job ever runs it,
        # because the task claims the document first.
        assert set(queued) == {retry} and 1 <= len(queued) <= 2
        claims = []
        for _ in queued:
            with tenant_session(tenant.tenant_id) as session:
                claims.append(document_status.claim_for_processing(session, retry))
        assert claims.count(True) == 1
        assert _count(
            "SELECT processing_attempts FROM documents WHERE id = :id", id=str(retry)
        ) == 2  # 1 before, plus the one claim

