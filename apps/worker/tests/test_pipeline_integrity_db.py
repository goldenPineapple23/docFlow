"""
H1, H3, M1, M3 and the document state machine, against the real database
(Phase 5.5 Stage 1b; DECISIONS.md D-158).

H1: an order reached `needs_review` before it had been matched, de-duplicated
or validated, and a failure in those steps was only logged -- so it could sit
in the queue with zero warnings and be approved.
H3: the extraction task could knock an approved order back to `processing`
when a job was redelivered, and paid for a second extraction on the way.
M1: a long order was truncated at 4096 output tokens and failed as DOC-009.
M3: a failure while saving the model's answer stranded the document in
`processing` with nothing recorded.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from docflow_core import db, document_status, stuck_documents
from docflow_core.db import platform_session, tenant_session
from docflow_core.review import WarningAcknowledgement, approve_document
from docflow_core.validation import open_warnings, validate_document
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from tests.conftest import requires_documents_schema
from tests.db_helpers import FakeAnthropic, WorkerTestTenant, model_payload, run_extraction

CLEAN = model_payload(
    header={"order_total": "570.00"},
    lines=[{"quantity": "12", "unit_price": "47.50", "line_total": "570.00"}],
)
WORKER_ROOT = Path(__file__).resolve().parents[1]


def _document(document_id: UUID) -> dict:
    with platform_session() as session:
        row = session.execute(
            text(
                "SELECT status, failure_code, pipeline_issues, processing_attempts, "
                "current_extraction_run_id FROM documents WHERE id = :id"
            ),
            {"id": str(document_id)},
        ).mappings().one()
    return dict(row)


def _count(sql: str, **params) -> int:
    with platform_session() as session:
        return int(session.execute(text(sql), params).scalar_one())


def _runs(document_id: UUID) -> int:
    return _count(
        "SELECT count(*) FROM extraction_runs WHERE document_id = :id AND run_kind = 'extraction'",
        id=str(document_id),
    )


def _lines(document_id: UUID) -> int:
    return _count("SELECT count(*) FROM document_lines WHERE document_id = :id", id=str(document_id))


def _alerts(tenant: WorkerTestTenant, alert_type: str) -> int:
    return _count(
        "SELECT count(*) FROM founder_alerts WHERE tenant_id = :t AND type = :k",
        t=str(tenant.tenant_id),
        k=alert_type,
    )


def _approve(tenant: WorkerTestTenant, document_id: UUID) -> None:
    with tenant_session(tenant.tenant_id) as session:
        validate_document(session, tenant.tenant_id, document_id)
        approve_document(
            session,
            tenant.tenant_id,
            document_id,
            user_id=tenant.user_id,
            acknowledgements=[
                WarningAcknowledgement(warning_id=w["id"], code=w["code"], text=w["code"])
                for w in open_warnings(session, document_id)
                if w["status"] == "open"
            ],
        )


# ── H1: into review only once checked, and never silently unchecked ───────


@requires_documents_schema
def test_H1_an_order_is_still_processing_while_its_checks_run(monkeypatch):
    import app.tasks.parse_and_extract as task_module

    seen: list[str] = []
    real_validate = task_module.validate_document

    def watching_validate(session, tenant_id, document_id, *args, **kwargs):
        seen.append(
            session.execute(
                text("SELECT status FROM documents WHERE id = :id"), {"id": str(document_id)}
            ).scalar_one()
        )
        return real_validate(session, tenant_id, document_id, *args, **kwargs)

    monkeypatch.setattr(task_module, "validate_document", watching_validate)
    with WorkerTestTenant("Acme Test H1 Order") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, document_id, CLEAN)
        assert seen == ["processing"]
        assert _document(document_id)["status"] == "needs_review"


@requires_documents_schema
def test_H1_a_validation_failure_fails_the_order_with_a_code_and_alerts(monkeypatch):
    import app.tasks.parse_and_extract as task_module

    def broken_validate(*args, **kwargs):
        raise RuntimeError("validation blew up")

    monkeypatch.setattr(task_module, "validate_document", broken_validate)
    with WorkerTestTenant("Acme Test H1 Validation") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, document_id, CLEAN)
        document = _document(document_id)
        assert document["status"] == "failed"
        assert document["failure_code"] == "DOC-021"
        assert _alerts(tenant, "document_failed") == 1


@requires_documents_schema
def test_H1_a_step_that_did_not_finish_is_an_explicit_warning_and_an_alert(monkeypatch):
    import app.tasks.parse_and_extract as task_module

    def broken_matching(*args, **kwargs):
        raise RuntimeError("matching blew up")

    monkeypatch.setattr(task_module, "match_document_lines", broken_matching)
    with WorkerTestTenant("Acme Test H1 Matching") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, document_id, CLEAN)
        assert _document(document_id)["status"] == "needs_review"
        with tenant_session(tenant.tenant_id) as session:
            warnings = [w for w in open_warnings(session, document_id) if w["code"] == "VAL-016"]
            # Re-validating (an edit, approval) keeps it: it is on the document.
            validate_document(session, tenant.tenant_id, document_id)
            still = [w for w in open_warnings(session, document_id) if w["code"] == "VAL-016"]
        assert len(warnings) == 1 and warnings[0]["detail"]["steps"] == "matching"
        assert len(still) == 1
        assert _alerts(tenant, "pipeline_step_failed") == 1


# ── H3: idempotent jobs; approved orders are never knocked back ────────────


@requires_documents_schema
def test_H3_a_redelivered_job_for_an_approved_order_changes_nothing(monkeypatch):
    with WorkerTestTenant("Acme Test H3 Approved") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, document_id, CLEAN)
        _approve(tenant, document_id)

        again = run_extraction(monkeypatch, tenant, document_id, CLEAN)
        assert again.calls == []
        assert _document(document_id)["status"] == "approved"
        assert _runs(document_id) == 1


@requires_documents_schema
def test_H3_a_duplicate_delivery_while_another_worker_is_on_it_is_a_no_op(monkeypatch):
    with WorkerTestTenant("Acme Test H3 Duplicate") as tenant:
        document_id = tenant.create_pending_document()
        with tenant_session(tenant.tenant_id) as session:
            assert document_status.claim_for_processing(session, document_id)
        fake = run_extraction(monkeypatch, tenant, document_id, CLEAN)
        assert fake.calls == []
        assert _document(document_id)["status"] == "processing"


@requires_documents_schema
def test_H3_a_worker_killed_mid_job_is_resumed_without_a_second_model_call(monkeypatch):
    with WorkerTestTenant("Acme Test H3 Killed") as tenant:
        document_id = tenant.create_pending_document()
        marker = Path(tempfile.mkdtemp()) / "reached"
        child = subprocess.Popen(
            [sys.executable, "-m", "tests.kill_runner", str(tenant.tenant_id), str(document_id), str(marker)],
            cwd=WORKER_ROOT,
        )
        try:
            deadline = time.monotonic() + 180
            while not marker.exists():
                assert child.poll() is None, "the child exited before reaching the kill point"
                assert time.monotonic() < deadline, "the child never reached the kill point"
                time.sleep(0.5)
        finally:
            child.kill()  # SIGKILL / TerminateProcess: no cleanup runs
            child.wait(timeout=30)

        killed = _document(document_id)
        assert killed["status"] == "processing"
        assert killed["current_extraction_run_id"] is not None
        assert _runs(document_id) == 1

        # The dead worker's claim goes stale; the redelivered job takes over.
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE documents SET processing_started_at = now() - interval '2 hours' "
                    "WHERE id = :id"
                ),
                {"id": str(document_id)},
            )
        fake = run_extraction(monkeypatch, tenant, document_id, CLEAN)
        assert fake.calls == []  # resumed from the saved answer
        assert _document(document_id)["status"] == "needs_review"
        assert _runs(document_id) == 1


@requires_documents_schema
def test_H3_the_database_refuses_to_move_an_approved_order_back_to_processing(monkeypatch):
    with WorkerTestTenant("Acme Test H3 Guard") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, document_id, CLEAN)
        _approve(tenant, document_id)
        with pytest.raises(Exception, match="not allowed"):
            with platform_session() as session:
                session.execute(
                    text("UPDATE documents SET status = 'processing' WHERE id = :id"),
                    {"id": str(document_id)},
                )
        assert _document(document_id)["status"] == "approved"


@requires_documents_schema
def test_H3_a_status_change_is_compare_and_set(monkeypatch):
    with WorkerTestTenant("Acme Test H3 CAS") as tenant:
        document_id = tenant.create_pending_document()
        with tenant_session(tenant.tenant_id) as session:
            # It is pending, not needs_review: nothing moves.
            assert not document_status.transition(
                session, document_id, from_statuses=["needs_review"], to="approved"
            )
        assert _document(document_id)["status"] == "pending"
        with pytest.raises(document_status.IllegalTransition):
            with tenant_session(tenant.tenant_id) as session:
                document_status.transition(
                    session, document_id, from_statuses=["approved"], to="processing"
                )


@requires_documents_schema
def test_H3_the_state_machine_in_code_is_the_one_in_the_database():
    pairs = [(a, b) for a in document_status.STATUSES for b in document_status.STATUSES]
    with platform_session() as session:
        database = {
            (a, b)
            for a, b in pairs
            if session.execute(
                text("SELECT document_status_transition_allowed(:a, :b)"), {"a": a, "b": b}
            ).scalar_one()
        }
    code = {(a, b) for a, b in pairs if document_status.is_allowed(a, b)}
    assert code == database


@requires_documents_schema
def test_H3_no_document_enters_review_without_the_model_answer():
    with WorkerTestTenant("Acme Test Raw Answer") as tenant:
        with pytest.raises(Exception, match="model''s answer|model's answer"):
            with platform_session() as session:
                session.execute(
                    text(
                        "INSERT INTO documents (id, tenant_id, original_filename, storage_path, "
                        "source, status, content_sha256, created_at) VALUES "
                        "(:id, :t, 'po.txt', 'tenants/x/po.txt', 'upload', 'needs_review', :sha, now())"
                    ),
                    {"id": str(uuid4()), "t": str(tenant.tenant_id), "sha": uuid4().hex},
                )
        document_id = tenant.create_pending_document()
        with tenant_session(tenant.tenant_id) as session:
            assert document_status.claim_for_processing(session, document_id)
        with pytest.raises(Exception, match="model''s answer|model's answer"):
            with tenant_session(tenant.tenant_id) as session:
                document_status.transition(
                    session, document_id, from_statuses=["processing"], to="needs_review"
                )


# ── Stuck documents (Section 7.9) ───────────────────────────────────────────


def _backdate(document_id: UUID, *, status: str, attempts: int) -> None:
    with platform_session() as session:
        session.execute(
            text(
                "UPDATE documents SET status = :s, processing_attempts = :a, "
                "processing_started_at = now() - interval '2 hours', "
                "created_at = now() - interval '2 hours' WHERE id = :id"
            ),
            {"id": str(document_id), "s": status, "a": attempts},
        )


@requires_documents_schema
def test_H3_a_document_left_processing_is_requeued_then_failed_with_a_code():
    with WorkerTestTenant("Acme Test Stuck") as tenant:
        retry = tenant.create_pending_document()
        give_up = tenant.create_pending_document()
        _backdate(retry, status="processing", attempts=1)
        _backdate(give_up, status="processing", attempts=3)

        queued: list[UUID] = []
        result = stuck_documents.sweep_tenant(tenant.tenant_id, lambda _t, d: queued.append(d))

        assert queued == [retry]
        assert result.failed == [give_up]
        assert _document(retry)["status"] == "processing"
        failed = _document(give_up)
        assert (failed["status"], failed["failure_code"]) == ("failed", "DOC-022")
        assert _alerts(tenant, "document_stuck") == 1


@requires_documents_schema
def test_H3_a_document_waiting_in_pending_is_requeued_and_reported_never_failed():
    with WorkerTestTenant("Acme Test Waiting") as tenant:
        waiting = tenant.create_pending_document()
        fresh = tenant.create_pending_document()
        _backdate(waiting, status="pending", attempts=0)

        queued: list[UUID] = []
        stuck_documents.sweep_tenant(tenant.tenant_id, lambda _t, d: queued.append(d))
        stuck_documents.sweep_tenant(tenant.tenant_id, lambda _t, d: queued.append(d))

        assert queued == [waiting, waiting]
        assert fresh not in queued
        assert _document(waiting)["status"] == "pending"
        assert _alerts(tenant, "document_stuck") == 1  # one per tenant per day


# ── pipeline_sweep_read (migration 0027): no tenant can list other tenants ──
# The code-side half (only db.py sets a flag; only stuck_documents uses the
# sweep session) is packages/core/tests/test_rls_flags.py.


def _tenant_ids(session) -> set[UUID]:
    return {UUID(str(row[0])) for row in session.execute(text("SELECT id FROM tenants"))}


@requires_documents_schema
def test_pipeline_sweep_read_a_tenant_session_sees_only_its_own_tenant():
    with WorkerTestTenant("Acme Test Sweep A") as a, WorkerTestTenant("Acme Test Sweep B") as b:
        with tenant_session(a.tenant_id) as session:
            assert _tenant_ids(session) == {a.tenant_id}
        # The policy does open `tenants` to the sweep, so the line above is a
        # real refusal, not an empty table.
        with db.pipeline_sweep_session() as session:
            assert {a.tenant_id, b.tenant_id} <= _tenant_ids(session)


@requires_documents_schema
def test_pipeline_sweep_read_a_flag_left_on_a_pooled_connection_never_reaches_a_tenant_request(monkeypatch):
    """The worst case: a connection handed out by the pool still carries the
    flag at session level. tenant_session() clears it before anything runs."""
    with WorkerTestTenant("Acme Test Sweep A") as a, WorkerTestTenant("Acme Test Sweep B") as b:
        connection = db.get_engine().connect()
        try:
            # One database transaction throughout, so the transaction-mode
            # pooler can't swap the backend under the test.
            connection.execute(text("SET app.pipeline_sweep = 'true'"))  # session level, not LOCAL
            leaked = {UUID(str(r[0])) for r in connection.execute(text("SELECT id FROM tenants"))}
            assert {a.tenant_id, b.tenant_id} <= leaked  # the flag really is on

            monkeypatch.setattr(db, "get_session_factory", lambda: sessionmaker(bind=connection))
            with tenant_session(a.tenant_id) as session:
                assert _tenant_ids(session) == {a.tenant_id}
        finally:
            connection.rollback()  # also undoes the SET
            connection.close()


# ── M1: long orders; M3: a failed save never strands a document ─────────────


@requires_documents_schema
def test_M1_a_truncated_answer_fails_with_DOC_020_and_keeps_nothing(monkeypatch):
    import app.tasks.parse_and_extract as task_module

    fake = FakeAnthropic(CLEAN, stop_reason="max_tokens")
    monkeypatch.setattr(task_module.anthropic, "Anthropic", fake)
    with WorkerTestTenant("Acme Test M1 Truncated") as tenant:
        document_id = tenant.create_pending_document()
        task_module.parse_and_extract(str(tenant.tenant_id), str(document_id))
        document = _document(document_id)
        assert (document["status"], document["failure_code"]) == ("failed", "DOC-020")
        assert _lines(document_id) == 0
        assert _alerts(tenant, "document_failed") == 1


@requires_documents_schema
def test_M1_a_100_line_order_is_stored_whole(monkeypatch):
    lines = [
        {"quantity": str(n), "unit_price": "1.25", "line_total": format(Decimal(n) * Decimal("1.25"), "f")}
        for n in range(1, 101)
    ]
    with WorkerTestTenant("Acme Test M1 Long") as tenant:
        document_id = tenant.create_pending_document()
        fake = run_extraction(monkeypatch, tenant, document_id, model_payload(lines=lines))
        assert fake.calls[0]["max_tokens"] >= 16000
        assert _lines(document_id) == 100
        assert _document(document_id)["status"] == "needs_review"


@requires_documents_schema
def test_M3_a_failure_saving_the_answer_ends_in_failed_not_stranded(monkeypatch):
    import app.tasks.parse_and_extract as task_module

    def broken_record(*args, **kwargs):
        raise RuntimeError("could not save")

    monkeypatch.setattr(task_module.model_runs, "record_extraction", broken_record)
    with WorkerTestTenant("Acme Test M3 Save") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, document_id, CLEAN)
        document = _document(document_id)
        assert (document["status"], document["failure_code"]) == ("failed", "DOC-021")
        assert _alerts(tenant, "document_failed") == 1
