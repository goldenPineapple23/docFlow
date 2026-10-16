"""
A fault in one of our own steps, against the real database (Phase 5.5
Stage 4, PR 1a; DECISIONS.md D-203; migration 0037).

Kind 3 of the broad-`except` audit: saving the model's answer, buyer
identification, matching, duplicate detection and validation each catch
anything, so a document is never stranded. What each must now also do:

- give the document the outcome it gave before (DOC-021 or VAL-016);
- raise the founder alert with the exception's TYPE and OUR code location,
  never its message;
- log the same, followed by the traceback's frames, never the message;
- raise it ONCE per state -- the same fault on a second document raises
  nothing, and neither does a repeat after the founder acknowledged it (the
  re-raised-alert defect, D-198);
- write the state's key on every document it hits, so the Console can count
  them.

And Q11: a model call's run row that can't be written raises its own alert.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import logging
from uuid import UUID

import pytest
from docflow_core.db import platform_session
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from tests.conftest import requires_documents_schema
from tests.db_helpers import WorkerTestTenant, model_payload, run_extraction

CLEAN = model_payload(
    header={"order_total": "570.00"},
    lines=[{"quantity": "12", "unit_price": "47.50", "line_total": "570.00"}],
)
# Stands in for what an exception's message can carry: a customer's value.
SECRET = "Acme Test Buyer SECRET-IN-THE-MESSAGE 47.50"


def _document(document_id: UUID) -> dict:
    with platform_session() as session:
        row = (
            session.execute(
                text("SELECT status, failure_code, fault_keys, raw_json FROM documents WHERE id = :id"),
                {"id": str(document_id)},
            )
            .mappings()
            .one()
        )
    return dict(row)


def _alerts(tenant: WorkerTestTenant, alert_type: str) -> list[dict]:
    with platform_session() as session:
        rows = (
            session.execute(
                text(
                    "SELECT id, payload, state_key, dedupe_key, acknowledged_at FROM founder_alerts "
                    "WHERE tenant_id = :t AND type = :k ORDER BY created_at"
                ),
                {"t": str(tenant.tenant_id), "k": alert_type},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def _acknowledge(alert_id: UUID) -> None:
    with platform_session() as session:
        session.execute(
            text("UPDATE founder_alerts SET acknowledged_at = now() WHERE id = :id"), {"id": str(alert_id)}
        )


def _database_utc_date() -> str:
    with platform_session() as session:
        return str(
            session.execute(text("SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD')")).scalar_one()
        )


def _break(monkeypatch, name: str, exc: Exception | None = None) -> None:
    """Make one of the task's steps raise, as a bug of ours would."""
    import app.tasks.parse_and_extract as task_module

    def broken(*args, **kwargs):
        raise exc or RuntimeError(f"{name} blew up: {SECRET}")

    monkeypatch.setattr(task_module, name, broken)


# ── the alert says where, and never what ─────────────────────────────────────


@requires_documents_schema
def test_a_failed_step_alerts_with_the_exception_type_and_our_code_location_only(monkeypatch):
    _break(monkeypatch, "match_document_lines")
    with WorkerTestTenant("Acme Test Fault Matching") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, document_id, CLEAN)

        document = _document(document_id)
        assert document["status"] == "needs_review", "the outcome is what it was: VAL-016, still reviewable"
        (alert,) = _alerts(tenant, "pipeline_step_failed")
        payload = alert["payload"]
        assert payload["step"] == "matching"
        assert payload["exception_type"] == "RuntimeError"
        assert payload["location"].startswith("app.tasks.parse_and_extract:_finish:")
        assert payload["first_document_id"] == str(document_id)
        assert "release" in payload
        assert SECRET not in str(payload) and "blew up" not in str(payload)
        # Raised under a state key, not the open-row dedupe key.
        assert alert["dedupe_key"] is None
        assert alert["state_key"].startswith(
            f"fault:pipeline_step_failed:{tenant.tenant_id}:matching:RuntimeError:"
        )
        assert document["fault_keys"] == [alert["state_key"]]


@requires_documents_schema
@pytest.mark.parametrize(
    ("target", "event"),
    [
        ("_save_extraction", "extraction_save_failed"),
        ("identify_and_link_buyer", "buyer_identification_failed"),
        ("match_document_lines", "matching_failed"),
        ("detect_document_relationships", "duplicate_detection_failed"),
        ("validate_document", "validation_failed"),
    ],
)
def test_a_failed_steps_log_line_carries_the_frames_and_never_the_message(monkeypatch, caplog, target, event):
    """The five kind 3 lines log as the sixteen former `logger.exception`
    calls do (D-203 item 8): type, our location, the frames. The stub's own
    source line is in the frames -- our code, not data -- but the message it
    raised with, and the value in it, are not."""
    _break(monkeypatch, target)
    with WorkerTestTenant(f"Acme Test Fault Log {event}") as tenant:
        document_id = tenant.create_pending_document()
        with caplog.at_level(logging.ERROR, logger="app.tasks.parse_and_extract"):
            run_extraction(monkeypatch, tenant, document_id, CLEAN)

    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith(f"{event} ")]
    assert line.startswith(
        f"{event} document_id={document_id} error_type=RuntimeError location=app.tasks.parse_and_extract:"
    )
    assert 'File "' in line and ", in broken\n" in line, "the frames reach the function that raised"
    assert f"{target} blew up: {SECRET}" not in line, "the message as raised is not logged"
    assert SECRET not in line


@requires_documents_schema
def test_each_failed_step_is_its_own_state(monkeypatch):
    _break(monkeypatch, "identify_and_link_buyer")
    _break(monkeypatch, "match_document_lines")
    with WorkerTestTenant("Acme Test Fault Two Steps") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, document_id, CLEAN)

        alerts = _alerts(tenant, "pipeline_step_failed")
        assert sorted(a["payload"]["step"] for a in alerts) == ["buyer_identification", "matching"]
        assert sorted(_document(document_id)["fault_keys"]) == sorted(a["state_key"] for a in alerts)


# ── once per state ───────────────────────────────────────────────────────────


@requires_documents_schema
def test_the_same_fault_on_a_second_document_raises_nothing_and_both_carry_the_key(monkeypatch):
    _break(monkeypatch, "match_document_lines")
    with WorkerTestTenant("Acme Test Fault Repeat") as tenant:
        first = tenant.create_pending_document()
        second = tenant.create_pending_document(
            content=b"PURCHASE ORDER\nPO Number: BCH-2292\n", filename="po2.txt"
        )
        run_extraction(monkeypatch, tenant, first, CLEAN)
        run_extraction(monkeypatch, tenant, second, CLEAN)

        (alert,) = _alerts(tenant, "pipeline_step_failed")
        assert alert["payload"]["first_document_id"] == str(first)
        assert _document(first)["fault_keys"] == [alert["state_key"]]
        assert _document(second)["fault_keys"] == [alert["state_key"]]
        assert _document(second)["status"] == "needs_review"


@requires_documents_schema
def test_an_acknowledged_alert_is_not_raised_again_by_the_same_state(monkeypatch):
    """The re-raised-alert defect (D-198): with the open-row dedupe an
    acknowledged alert was raised again by the next occurrence. Under a state
    key it stays quiet, and the document still carries the key."""
    _break(monkeypatch, "match_document_lines")
    with WorkerTestTenant("Acme Test Fault Acknowledged") as tenant:
        first = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, first, CLEAN)
        (alert,) = _alerts(tenant, "pipeline_step_failed")
        _acknowledge(alert["id"])

        later = tenant.create_pending_document(
            content=b"PURCHASE ORDER\nPO Number: BCH-2293\n", filename="po3.txt"
        )
        run_extraction(monkeypatch, tenant, later, CLEAN)

        alerts = _alerts(tenant, "pipeline_step_failed")
        assert [a["id"] for a in alerts] == [alert["id"]], "an acknowledged alert was raised again"
        assert alerts[0]["acknowledged_at"] is not None
        assert _document(later)["status"] == "needs_review"
        assert _document(later)["fault_keys"] == [alert["state_key"]]


@requires_documents_schema
def test_a_different_exception_type_at_the_same_step_is_a_different_state(monkeypatch):
    with WorkerTestTenant("Acme Test Fault Changed") as tenant:
        _break(monkeypatch, "match_document_lines")
        first = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, first, CLEAN)

        _break(monkeypatch, "match_document_lines", KeyError("another bug"))
        second = tenant.create_pending_document(
            content=b"PURCHASE ORDER\nPO Number: BCH-2294\n", filename="po4.txt"
        )
        run_extraction(monkeypatch, tenant, second, CLEAN)

        alerts = _alerts(tenant, "pipeline_step_failed")
        assert [a["payload"]["exception_type"] for a in alerts] == ["RuntimeError", "KeyError"]
        assert alerts[0]["state_key"] != alerts[1]["state_key"]


@requires_documents_schema
def test_another_tenant_hit_by_the_same_fault_is_raised_for_that_tenant(monkeypatch):
    """The tenant is in the key (founder, 2026-10-13): each affected tenant
    is news, and one tenant's alert never stands in for another's."""
    _break(monkeypatch, "match_document_lines")
    with WorkerTestTenant("Acme Test Fault Tenant A") as a, WorkerTestTenant("Acme Test Fault Tenant B") as b:
        run_extraction(monkeypatch, a, a.create_pending_document(), CLEAN)
        run_extraction(monkeypatch, b, b.create_pending_document(), CLEAN)

        (alert_a,) = _alerts(a, "pipeline_step_failed")
        (alert_b,) = _alerts(b, "pipeline_step_failed")
        assert alert_a["state_key"] != alert_b["state_key"]
        assert str(a.tenant_id) in alert_a["state_key"] and str(b.tenant_id) in alert_b["state_key"]


@requires_documents_schema
def test_a_database_error_that_is_not_an_outage_is_keyed_by_its_sqlstate(monkeypatch):
    """Q14 (D-206): a database error that is not an outage (here a deadlock,
    40P01) fails the step as before, and its SQLSTATE ends the key, so a
    deadlock and a timeout at the same place are separate alerts. The UTC
    date connection errors carried until the database retry existed is gone:
    an outage now waits instead (test_database_wait_db.py)."""
    import psycopg

    deadlock = OperationalError("SELECT 1", {}, psycopg.errors.DeadlockDetected())
    _break(monkeypatch, "match_document_lines", deadlock)
    with WorkerTestTenant("Acme Test Fault Deadlock") as tenant:
        run_extraction(monkeypatch, tenant, tenant.create_pending_document(), CLEAN)

        (alert,) = _alerts(tenant, "pipeline_step_failed")
        assert alert["payload"]["exception_type"] == "OperationalError"
        assert alert["state_key"].endswith(":40P01")
        assert _database_utc_date() not in alert["state_key"]


# ── DOC-021: saving and validation ───────────────────────────────────────────


@requires_documents_schema
def test_a_validation_fault_fails_doc_021_and_alerts_once_per_state(monkeypatch):
    _break(monkeypatch, "validate_document")
    with WorkerTestTenant("Acme Test Fault Validation") as tenant:
        first = tenant.create_pending_document()
        second = tenant.create_pending_document(
            content=b"PURCHASE ORDER\nPO Number: BCH-2295\n", filename="po5.txt"
        )
        run_extraction(monkeypatch, tenant, first, CLEAN)
        run_extraction(monkeypatch, tenant, second, CLEAN)

        for document_id in (first, second):
            document = _document(document_id)
            assert (document["status"], document["failure_code"]) == ("failed", "DOC-021")
        (alert,) = _alerts(tenant, "document_failed")
        payload = alert["payload"]
        assert payload["error_code"] == "DOC-021" and payload["step"] == "validation"
        assert payload["exception_type"] == "RuntimeError"
        assert payload["location"].startswith("app.tasks.parse_and_extract:_finish:")
        assert SECRET not in str(payload)
        assert _document(first)["fault_keys"] == _document(second)["fault_keys"] == [alert["state_key"]]


@requires_documents_schema
def test_a_saving_fault_fails_doc_021_keeps_the_paid_answer_and_names_the_step(monkeypatch):
    _break(monkeypatch, "_save_extraction")
    with WorkerTestTenant("Acme Test Fault Saving") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, document_id, CLEAN)

        document = _document(document_id)
        assert (document["status"], document["failure_code"]) == ("failed", "DOC-021")
        assert document["raw_json"], "the paid answer stays on the failed document (M3)"
        (alert,) = _alerts(tenant, "document_failed")
        assert alert["payload"]["step"] == "saving"
        assert alert["payload"]["location"].startswith("app.tasks.parse_and_extract:_parse_and_extract:")
        assert document["fault_keys"] == [alert["state_key"]]


# ── Q11: a cost record that could not be written ─────────────────────────────


@requires_documents_schema
def test_an_outcome_run_row_that_cant_be_written_raises_cost_record_lost(monkeypatch):
    """The save failed, and writing the run record again on its own failed
    too: that paid call is on no cost record. A data loss, so the founder is
    told (Q11) -- and the document still ends failed with DOC-021."""
    _break(monkeypatch, "_save_extraction")
    _break(monkeypatch, "_record_runs")
    with WorkerTestTenant("Acme Test Fault Cost Outcome") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, document_id, CLEAN)

        document = _document(document_id)
        assert (document["status"], document["failure_code"]) == ("failed", "DOC-021")
        (lost,) = _alerts(tenant, "cost_record_lost")
        assert lost["payload"]["step"] == "outcome" and lost["payload"]["exception_type"] == "RuntimeError"
        assert SECRET not in str(lost["payload"])
        (failed,) = _alerts(tenant, "document_failed")
        assert sorted(document["fault_keys"]) == sorted([lost["state_key"], failed["state_key"]])


@requires_documents_schema
def test_a_started_run_row_that_cant_be_written_raises_cost_record_lost_and_the_call_goes_ahead(monkeypatch):
    """Q11 covers the row written BEFORE the model call too (founder,
    2026-10-13). Without it a worker killed mid-call leaves no record of the
    call; the call itself still goes ahead and the document is read."""
    import app.tasks.parse_and_extract as task_module

    def broken(*args, **kwargs):
        raise RuntimeError(f"started row: {SECRET}")

    monkeypatch.setattr(task_module.model_runs, "record_started", broken)
    with WorkerTestTenant("Acme Test Fault Cost Started") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, document_id, CLEAN)

        document = _document(document_id)
        assert document["status"] == "needs_review", "the call went ahead and the document was read"
        (lost,) = _alerts(tenant, "cost_record_lost")
        assert lost["payload"]["step"] == "started"
        assert lost["payload"]["location"].startswith("app.tasks.parse_and_extract:_StartedRuns.__call__:")
        assert SECRET not in str(lost["payload"])
        assert document["fault_keys"] == [lost["state_key"]]
