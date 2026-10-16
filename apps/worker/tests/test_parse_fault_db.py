"""
A parse job that failed on our side, against the real database (Phase 5.5
Stage 4, PR 1b-i; DECISIONS.md D-203 and D-204).

The parse service answers `error` when an exception no parser raises on a
malformed file reached our code, and `crashed` when the job died without an
answer. Either way the file is not to blame (founder, 2026-10-16):
- the document fails with DOC-030, never DOC-005;
- the `document_failed` alert is raised once per state, keyed by the parse
  app's release (where the fault is), with the exception's type and our code
  location -- or, for a crash, its cause in the location's place;
- every document the state hits carries its key, so the Console counts them.

The parse service's answer is replaced here; the service itself is tested
in apps/parse. All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import UUID

from docflow_core import parse_client
from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import requires_documents_schema
from tests.db_helpers import WorkerTestTenant, model_payload, run_extraction

CLEAN = model_payload(
    header={"order_total": "570.00"},
    lines=[{"quantity": "12", "unit_price": "47.50", "line_total": "570.00"}],
)
LOCATION = "parse_service.parsing.conversion:convert_xls:281"


def _error(release: str = "deployment-P7") -> parse_client.ParseAnswer:
    return parse_client.ParseAnswer("error", error_type="TypeError", location=LOCATION, release=release)


def _answer_with(monkeypatch, answer: parse_client.ParseAnswer) -> None:
    import app.tasks.parse_and_extract as task_module

    monkeypatch.setattr(task_module.parse_client, "parse_document", lambda content, filename: answer)


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


def _alerts(tenant: WorkerTestTenant) -> list[dict]:
    with platform_session() as session:
        rows = (
            session.execute(
                text(
                    "SELECT id, payload, state_key FROM founder_alerts "
                    "WHERE tenant_id = :t AND type = 'document_failed' ORDER BY created_at"
                ),
                {"t": str(tenant.tenant_id)},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


@requires_documents_schema
def test_an_error_answer_fails_doc_030_with_the_keyed_alert(monkeypatch):
    _answer_with(monkeypatch, _error())
    with WorkerTestTenant("Acme Test Parse Error") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, document_id, CLEAN)

        document = _document(document_id)
        assert (document["status"], document["failure_code"]) == ("failed", "DOC-030")
        (alert,) = _alerts(tenant)
        assert alert["state_key"] == (
            f"fault:document_failed:{tenant.tenant_id}:parsing:TypeError:"
            "parse_service.parsing.conversion:convert_xls:281:deployment-P7"
        )
        payload = alert["payload"]
        assert (payload["error_code"], payload["step"], payload["exception_type"]) == (
            "DOC-030", "parsing", "TypeError"
        )
        assert (payload["location"], payload["release"]) == (LOCATION, "deployment-P7")
        assert payload["first_document_id"] == str(document_id)
        assert document["fault_keys"] == [alert["state_key"]]


@requires_documents_schema
def test_the_same_parse_fault_on_a_second_document_raises_nothing_and_both_carry_the_key(monkeypatch):
    _answer_with(monkeypatch, _error())
    with WorkerTestTenant("Acme Test Parse Error Repeat") as tenant:
        first = tenant.create_pending_document()
        second = tenant.create_pending_document(
            content=b"PURCHASE ORDER\nPO Number: BCH-2292\n", filename="po2.txt"
        )
        run_extraction(monkeypatch, tenant, first, CLEAN)
        run_extraction(monkeypatch, tenant, second, CLEAN)

        (alert,) = _alerts(tenant)
        assert _document(first)["fault_keys"] == [alert["state_key"]]
        assert _document(second)["fault_keys"] == [alert["state_key"]]
        assert _document(second)["failure_code"] == "DOC-030"


@requires_documents_schema
def test_a_new_parse_release_is_a_new_state(monkeypatch):
    with WorkerTestTenant("Acme Test Parse Error Release") as tenant:
        _answer_with(monkeypatch, _error("deployment-P7"))
        run_extraction(monkeypatch, tenant, tenant.create_pending_document(), CLEAN)
        _answer_with(monkeypatch, _error("deployment-P8"))
        later = tenant.create_pending_document(
            content=b"PURCHASE ORDER\nPO Number: BCH-2293\n", filename="po3.txt"
        )
        run_extraction(monkeypatch, tenant, later, CLEAN)

        assert [a["payload"]["release"] for a in _alerts(tenant)] == ["deployment-P7", "deployment-P8"]


@requires_documents_schema
def test_a_crashed_job_is_doc_030_keyed_by_its_cause_not_doc_005(monkeypatch):
    _answer_with(monkeypatch, parse_client.ParseAnswer("crashed", cause="signal_11", release="deployment-P7"))
    with WorkerTestTenant("Acme Test Parse Crash") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, document_id, CLEAN)

        document = _document(document_id)
        assert (document["status"], document["failure_code"]) == ("failed", "DOC-030")
        assert document["raw_json"] == {"error_code": "DOC-030", "detail": "parse service: crashed:signal_11"}
        (alert,) = _alerts(tenant)
        assert alert["state_key"] == (
            f"fault:document_failed:{tenant.tenant_id}:parsing:crashed:parse_service.job:signal_11:0:deployment-P7"
        )
        assert document["fault_keys"] == [alert["state_key"]]
