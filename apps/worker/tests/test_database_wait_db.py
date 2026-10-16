"""
Q14 (D-206): a step that loses the database waits it out instead of failing
the document; a lost save re-runs the extraction at most once; anything that
is not an outage fails as before, keyed by its SQLSTATE. Against the real
database, through the real task (migration 0038 applied).

The outage is a driver error the task's steps raise, as the database's
would: psycopg's own exception classes, wrapped by SQLAlchemy as on the
real path.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
import pytest
from docflow_core import document_status, model_runs, stuck_documents
from docflow_core.constants import DATABASE_MAX_WAIT_HOURS
from docflow_core.db import platform_session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from tests.conftest import requires_documents_schema
from tests.db_helpers import FakeAnthropic, WorkerTestTenant, model_payload, run_extraction

CLEAN = model_payload(
    header={"order_total": "570.00"},
    lines=[{"quantity": "12", "unit_price": "47.50", "line_total": "570.00"}],
)


def _outage() -> OperationalError:
    """The server shutting down under us (57P01): an outage (D-206)."""
    return OperationalError("SELECT 1", {}, psycopg.errors.AdminShutdown())


def _deadlock() -> OperationalError:
    return OperationalError("SELECT 1", {}, psycopg.errors.DeadlockDetected())


def _statement_timeout() -> OperationalError:
    return OperationalError("SELECT 1", {}, psycopg.errors.QueryCanceled())


def _break(monkeypatch, target, name: str, exc_factory, *, times: int | None = None) -> list[int]:
    """Make `target.name` raise `exc_factory()`, `times` times (None: always),
    then behave as before. Returns the call counter."""
    real = getattr(target, name)
    calls = [0]

    def broken(*args, **kwargs):
        calls[0] += 1
        if times is None or calls[0] <= times:
            raise exc_factory()
        return real(*args, **kwargs)

    monkeypatch.setattr(target, name, broken)
    return calls


def _task():
    import app.tasks.parse_and_extract as task_module

    return task_module


def _row(document_id: UUID) -> dict:
    with platform_session() as session:
        return dict(
            session.execute(
                text(
                    "SELECT status, failure_code, wait_cause, last_wait_error, waiting_since, retry_at, "
                    "processing_attempts, current_extraction_run_id FROM documents WHERE id = :id"
                ),
                {"id": str(document_id)},
            )
            .mappings()
            .one()
        )


def _set(document_id: UUID, assignments: str) -> None:
    with platform_session() as session:
        session.execute(text(f"UPDATE documents SET {assignments} WHERE id = :id"), {"id": str(document_id)})


def _alerts(tenant: WorkerTestTenant, alert_type: str) -> list[dict]:
    with platform_session() as session:
        rows = (
            session.execute(
                text(
                    "SELECT payload, state_key FROM founder_alerts WHERE tenant_id = :t AND type = :k "
                    "ORDER BY created_at"
                ),
                {"t": str(tenant.tenant_id), "k": alert_type},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def _extraction_outcomes(document_id: UUID) -> list[dict]:
    with platform_session() as session:
        rows = (
            session.execute(
                text(
                    "SELECT succeeded, error_code FROM extraction_runs WHERE document_id = :id "
                    "AND run_kind = 'extraction' AND run_state = 'finished' ORDER BY created_at"
                ),
                {"id": str(document_id)},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


# ── the constraint and the code agree ───────────────────────────────────────


@requires_documents_schema
def test_the_wait_cause_check_allows_exactly_the_causes_the_code_writes():
    with platform_session() as session:
        definition = session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'documents_wait_cause_check' AND conrelid = 'public.documents'::regclass"
            )
        ).scalar_one()
    import re

    assert set(re.findall(r"'([a-z_]+)'::text", definition)) == set(document_status.WAIT_CAUSES)
    assert "database" in document_status.WAIT_CAUSES


# ── a step after the answer was saved waits, then resumes with no model call ─


@requires_documents_schema
@pytest.mark.parametrize(
    "name, step",
    [
        ("identify_and_link_buyer", "buyer_identification"),
        ("match_document_lines", "matching"),
        ("detect_document_relationships", "duplicate_detection"),
        ("validate_document", "validation"),
    ],
)
def test_a_step_that_loses_the_database_waits_then_resumes_without_a_model_call(monkeypatch, name, step):
    _break(monkeypatch, _task(), name, _outage, times=1)
    with WorkerTestTenant(f"Acme Test Database Wait {step}") as tenant:
        document_id = tenant.create_pending_document()
        first = run_extraction(monkeypatch, tenant, document_id, CLEAN)
        assert len(first.calls) == 1

        row = _row(document_id)
        assert (row["status"], row["wait_cause"], row["failure_code"]) == ("pending", "database", None)
        assert row["last_wait_error"] == f"database:{step}:57P01"
        assert row["processing_attempts"] == 0, "waiting gives the attempt back"
        assert row["retry_at"] is not None
        # Not a failure of ours: no step alert, no failed document.
        assert _alerts(tenant, "pipeline_step_failed") == [] and _alerts(tenant, "document_failed") == []

        _set(document_id, "retry_at = now()")  # the dispatcher's send, without the wait
        again = run_extraction(monkeypatch, tenant, document_id, CLEAN)
        assert again.calls == [], "resumed from the saved answer"
        row = _row(document_id)
        assert (row["status"], row["wait_cause"], row["last_wait_error"]) == ("needs_review", None, None)


# ── a lost save: one more paid extraction, and no more ──────────────────────


@requires_documents_schema
def test_a_save_lost_to_the_database_waits_and_extracts_once_more(monkeypatch):
    _break(monkeypatch, _task(), "_save_extraction", _outage, times=1)
    with WorkerTestTenant("Acme Test Database Save Once") as tenant:
        document_id = tenant.create_pending_document()
        first = run_extraction(monkeypatch, tenant, document_id, CLEAN)
        row = _row(document_id)
        assert (row["status"], row["wait_cause"], row["last_wait_error"]) == (
            "pending",
            "database",
            "database:saving:57P01",
        )

        _set(document_id, "retry_at = now()")
        second = run_extraction(monkeypatch, tenant, document_id, CLEAN)
        assert len(first.calls) + len(second.calls) == 2
        assert _row(document_id)["status"] == "needs_review"
        # Both paid calls are on the cost record (D-163), the lost one too.
        assert [o["succeeded"] for o in _extraction_outcomes(document_id)] == [True, True]


@requires_documents_schema
def test_a_second_lost_save_fails_doc_021_with_its_alert(monkeypatch):
    _break(monkeypatch, _task(), "_save_extraction", _outage)
    with WorkerTestTenant("Acme Test Database Save Twice") as tenant:
        document_id = tenant.create_pending_document()
        first = run_extraction(monkeypatch, tenant, document_id, CLEAN)
        _set(document_id, "retry_at = now()")
        second = run_extraction(monkeypatch, tenant, document_id, CLEAN)

        assert len(first.calls) + len(second.calls) == 2
        row = _row(document_id)
        assert (row["status"], row["failure_code"]) == ("failed", "DOC-021")
        (alert,) = _alerts(tenant, "document_failed")
        assert alert["payload"]["error_code"] == "DOC-021" and alert["payload"]["step"] == "saving"


@requires_documents_schema
def test_with_the_wait_and_cost_rows_lost_two_sweep_pickups_pay_for_one_more_call_at_most(monkeypatch):
    """
    The founder's case (2026-10-16): the save is lost to the database, and
    so are the outcome row and the wait itself, so the document stays in
    `processing` with no marker and reaches the stuck sweep. Each pickup
    must not pay again: over the first attempt and two pickups, one paid
    call more at most, then DOC-021 with its alert -- the limit is counted
    from the started rows on the cost record (D-206).
    """
    task = _task()
    _break(monkeypatch, model_runs, "record_extraction", _outage)  # the save and the outcome row
    _break(monkeypatch, document_status, "to_waiting", _outage)  # the wait
    # The count read after the lost save is lost as well; the check before
    # the next call is what holds the limit.
    monkeypatch.setattr(task, "_may_extract_again", lambda _tid, _did: True)
    fake = FakeAnthropic(CLEAN)
    monkeypatch.setattr(task.anthropic, "Anthropic", fake)

    def pickup(tenant_id: UUID, document_id: UUID) -> None:
        task.parse_and_extract(str(tenant_id), str(document_id))

    with WorkerTestTenant("Acme Test Database Lost Everything") as tenant:
        document_id = tenant.create_pending_document()
        pickup(tenant.tenant_id, document_id)
        assert len(fake.calls) == 1 and _row(document_id)["status"] == "processing"

        for _ in range(2):
            # The claim is past the stuck timeout: the sweep takes it over.
            _set(document_id, "processing_started_at = now() - interval '31 minutes'")
            result = stuck_documents.sweep_tenant(tenant.tenant_id, pickup)
            assert result.requeued == [document_id] or _row(document_id)["status"] == "failed"

        assert len(fake.calls) == 2, "the first call and one more, never a third"
        row = _row(document_id)
        assert (row["status"], row["failure_code"]) == ("failed", "DOC-021")
        (alert,) = _alerts(tenant, "document_failed")
        assert alert["payload"]["exception_type"] == "unsaved_answer_limit"
        assert alert["payload"]["step"] == "saving"


@requires_documents_schema
def test_a_worker_that_dies_mid_call_twice_is_capped_the_same_way(monkeypatch):
    """The record of a call the worker died in is the same as a lost save's
    (a started row, no outcome), so the same limit holds (D-206): today's
    third try on a stuck document does not pay a third time."""
    task = _task()
    with WorkerTestTenant("Acme Test Database Two Lost Calls") as tenant:
        document_id = tenant.create_pending_document()
        with platform_session() as session:
            for _ in range(2):
                session.execute(
                    text(
                        "INSERT INTO extraction_runs (id, tenant_id, document_id, run_kind, model_id, "
                        "run_state, succeeded, created_at) VALUES (:id, :t, :d, 'extraction', "
                        "'claude-sonnet-5', 'started', NULL, clock_timestamp())"
                    ),
                    {"id": str(uuid4()), "t": str(tenant.tenant_id), "d": str(document_id)},
                )
        fake = run_extraction(monkeypatch, tenant, document_id, CLEAN)
        assert fake.calls == []
        assert (_row(document_id)["status"], _row(document_id)["failure_code"]) == ("failed", "DOC-021")
        assert task.MAX_UNSAVED_EXTRACTION_CALLS == 2


# ── the maximum wait ────────────────────────────────────────────────────────


@requires_documents_schema
def test_after_six_hours_on_the_database_the_document_fails_doc_021_with_its_alert(monkeypatch):
    _break(monkeypatch, _task(), "match_document_lines", _outage)
    with WorkerTestTenant("Acme Test Database Wait Limit") as tenant:
        late = tenant.create_pending_document()
        early = tenant.create_pending_document(
            content=b"PURCHASE ORDER\nPO Number: BCH-3301\n", filename="po2.txt"
        )
        _set(
            late,
            "wait_cause = 'database', "
            f"waiting_since = now() - interval '{DATABASE_MAX_WAIT_HOURS} hours 1 minute'",
        )
        _set(early, "wait_cause = 'database', waiting_since = now() - interval '5 hours'")
        run_extraction(monkeypatch, tenant, late, CLEAN)
        run_extraction(monkeypatch, tenant, early, CLEAN)

        assert (_row(late)["status"], _row(late)["failure_code"]) == ("failed", "DOC-021")
        assert (_row(early)["status"], _row(early)["wait_cause"]) == ("pending", "database")
        (alert,) = _alerts(tenant, "document_failed")
        assert alert["payload"]["error_code"] == "DOC-021" and alert["payload"]["step"] == "matching"


# ── not an outage: fails as before, keyed by its SQLSTATE ───────────────────


@requires_documents_schema
def test_a_deadlock_and_a_statement_timeout_fail_as_before_as_two_separate_alerts(monkeypatch):
    task = _task()
    with WorkerTestTenant("Acme Test Database Not An Outage") as tenant:
        first = tenant.create_pending_document()
        second = tenant.create_pending_document(
            content=b"PURCHASE ORDER\nPO Number: BCH-3302\n", filename="po3.txt"
        )
        _break(monkeypatch, task, "match_document_lines", _deadlock)
        run_extraction(monkeypatch, tenant, first, CLEAN)
        _break(monkeypatch, task, "match_document_lines", _statement_timeout)
        run_extraction(monkeypatch, tenant, second, CLEAN)

        for document_id in (first, second):
            row = _row(document_id)
            assert (row["status"], row["wait_cause"]) == ("needs_review", None)  # VAL-016, as before
        keys = [a["state_key"] for a in _alerts(tenant, "pipeline_step_failed")]
        assert len(keys) == 2 and keys[0].endswith(":40P01") and keys[1].endswith(":57014")


# ── 0038's index, and the delete it serves (D-202 item 12) ──────────────────


def _item_and_matched_line(tenant: WorkerTestTenant) -> tuple[UUID, UUID]:
    document_id = tenant.create_pending_document()
    item_id, line_id = uuid4(), uuid4()
    with platform_session() as session:
        session.execute(
            text(
                "INSERT INTO items (id, tenant_id, sku, description, created_at, updated_at) "
                "VALUES (:id, :t, 'ACME-TEST-0038', 'Acme Test hex bolt', now(), now())"
            ),
            {"id": str(item_id), "t": str(tenant.tenant_id)},
        )
        session.execute(
            text(
                "INSERT INTO document_lines (id, document_id, tenant_id, line_number, sku, description, "
                "matched_item_id, match_method, created_at) "
                "VALUES (:id, :d, :t, 1, 'ACME-TEST-0038', 'Acme Test hex bolt', :item, 'exact_sku', now())"
            ),
            {"id": str(line_id), "d": str(document_id), "t": str(tenant.tenant_id), "item": str(item_id)},
        )
    return item_id, line_id


@requires_documents_schema
def test_deleting_an_item_a_matched_line_points_at_is_refused_by_the_check_today():
    """Pinned, not changed (founder, 2026-10-16; D-206): ON DELETE SET NULL
    clears matched_item_id alone, so document_lines_match_method_requires_item
    refuses the delete. The tenant hard delete never meets it (it removes the
    lines first)."""
    with WorkerTestTenant("Acme Test Item Delete Refused") as tenant:
        item_id, line_id = _item_and_matched_line(tenant)
        with pytest.raises(IntegrityError) as excinfo, platform_session() as session:
            session.execute(text("DELETE FROM items WHERE id = :id"), {"id": str(item_id)})
        assert getattr(excinfo.value.orig, "sqlstate", None) == "23514"
        assert "document_lines_match_method_requires_item" in str(excinfo.value.orig.diag.constraint_name)
        with platform_session() as session:
            line = session.execute(
                text("SELECT matched_item_id, match_method FROM document_lines WHERE id = :id"),
                {"id": str(line_id)},
            ).one()
            assert (
                session.execute(
                    text("SELECT count(*) FROM items WHERE id = :id"), {"id": str(item_id)}
                ).scalar_one()
                == 1
            )
        assert (str(line[0]), line[1]) == (str(item_id), "exact_sku")


@requires_documents_schema
def test_the_lookup_by_matched_item_alone_can_use_0038s_index():
    """The foreign key's action looks lines up by matched_item_id alone; with
    sequential scans off, the planner shows the index serves that lookup."""
    with platform_session() as session:
        session.execute(text("SET LOCAL enable_seqscan = off"))
        # A fixed, generated id: nothing from a document reaches this SQL.
        plan = "\n".join(
            row[0]
            for row in session.execute(
                text(f"EXPLAIN SELECT 1 FROM document_lines WHERE matched_item_id = '{uuid4()}'::uuid")
            )
        )
    assert "idx_document_lines_matched_item_id" in plan, plan
