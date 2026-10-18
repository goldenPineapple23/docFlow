"""
What an email unwrap couldn't read reaches the reviewer, against the real
database (Phase 5.5 Stage 4, PR 1b-ii; DECISIONS.md D-207).

The parse service names each loss on its answer (`lost`); the worker writes
VAL-017 (an attachment) and VAL-018 (the body) in the transaction that
saves the extraction, and validation, which runs next, leaves them alone:
- the document reaches review with both open, high, VAL-017 naming the
  attachment;
- re-running validation keeps them, acknowledged or not;
- writing them again (a save retried after a lost one) adds nothing.

The parse service's answer is replaced here; the service itself is tested
in apps/parse. All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import UUID

from docflow_core import parse_client
from docflow_core.db import platform_session, tenant_session
from docflow_core.validation import record_parse_warnings, validate_document
from sqlalchemy import text

from tests.conftest import requires_documents_schema
from tests.db_helpers import PO_TEXT, WorkerTestTenant, model_payload, run_extraction

CLEAN = model_payload(
    header={"order_total": "570.00"},
    lines=[{"quantity": "12", "unit_price": "47.50", "line_total": "570.00"}],
)
LOST = [{"code": "VAL-017", "filename": "old_orders.zip"}, {"code": "VAL-018", "filename": None}]


def _answer_with(monkeypatch, lost: list[dict]) -> None:
    import app.tasks.parse_and_extract as task_module

    answer = parse_client.ParseAnswer(
        "ok", file_type="eml", parts=[{"type": "text", "text": PO_TEXT.decode("utf-8")}], lost=lost
    )
    monkeypatch.setattr(task_module.parse_client, "parse_document", lambda content, filename: answer)


def _parse_warnings(document_id: UUID) -> list[dict]:
    with platform_session() as session:
        rows = session.execute(
            text(
                "SELECT id, code, severity, detail, status FROM document_warnings "
                "WHERE document_id = :id AND deleted_at IS NULL AND code IN ('VAL-017', 'VAL-018') "
                "ORDER BY code"
            ),
            {"id": str(document_id)},
        ).mappings().all()
    return [dict(row) for row in rows]


def _status(document_id: UUID) -> str:
    with platform_session() as session:
        return session.execute(
            text("SELECT status FROM documents WHERE id = :id"), {"id": str(document_id)}
        ).scalar_one()


@requires_documents_schema
def test_the_losses_reach_review_as_open_high_warnings(monkeypatch):
    _answer_with(monkeypatch, LOST)
    with WorkerTestTenant("Acme Test Unwrap Losses") as tenant:
        document_id = tenant.create_pending_document(filename="po.eml")
        run_extraction(monkeypatch, tenant, document_id, CLEAN)

        assert _status(document_id) == "needs_review"
        warnings = _parse_warnings(document_id)
        assert [(w["code"], w["severity"], w["status"]) for w in warnings] == [
            ("VAL-017", "high", "open"),
            ("VAL-018", "high", "open"),
        ]
        assert warnings[0]["detail"] == {"filename": "old_orders.zip", "position": "1"}


@requires_documents_schema
def test_a_validation_re_run_keeps_them_acknowledged_or_not(monkeypatch):
    _answer_with(monkeypatch, LOST)
    with WorkerTestTenant("Acme Test Unwrap Revalidate") as tenant:
        document_id = tenant.create_pending_document(filename="po.eml")
        run_extraction(monkeypatch, tenant, document_id, CLEAN)
        before = _parse_warnings(document_id)
        # A reviewer acknowledged the body's warning (the approval path's own
        # write, reduced to its effect on the row; the table requires who).
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE document_warnings SET status = 'acknowledged', acknowledged_at = now(), "
                    "acknowledged_by = :user WHERE id = :id"
                ),
                {"id": str(before[1]["id"]), "user": str(tenant.user_id)},
            )

        with tenant_session(tenant.tenant_id) as session:
            summary = validate_document(session, tenant.tenant_id, document_id)

        after = _parse_warnings(document_id)
        assert [(w["id"], w["code"]) for w in after] == [(w["id"], w["code"]) for w in before]
        assert [w["status"] for w in after] == ["open", "acknowledged"]
        assert summary.resolved == 0


@requires_documents_schema
def test_writing_them_again_adds_nothing(monkeypatch):
    _answer_with(monkeypatch, LOST)
    with WorkerTestTenant("Acme Test Unwrap Rewrite") as tenant:
        document_id = tenant.create_pending_document(filename="po.eml")
        run_extraction(monkeypatch, tenant, document_id, CLEAN)
        with tenant_session(tenant.tenant_id) as session:
            assert record_parse_warnings(session, tenant.tenant_id, document_id, LOST) == 0
        assert len(_parse_warnings(document_id)) == 2


@requires_documents_schema
def test_an_answer_that_lost_nothing_raises_neither(monkeypatch):
    _answer_with(monkeypatch, [])
    with WorkerTestTenant("Acme Test Unwrap Nothing Lost") as tenant:
        document_id = tenant.create_pending_document(filename="po.eml")
        run_extraction(monkeypatch, tenant, document_id, CLEAN)
        assert _status(document_id) == "needs_review"
        assert _parse_warnings(document_id) == []
