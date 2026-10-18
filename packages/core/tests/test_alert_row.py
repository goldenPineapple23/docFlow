"""
How the Console's alert row says what an alert has hit (D-203; PR 1b-i).

A keyed alert counts the documents that carry its key. One raised at email
intake has no document at all -- an attachment was refused before a document
existed -- so the row says so instead of a count of 0 (founder, 2026-10-16).
"""

from __future__ import annotations

from docflow_core.admin_data_access import _alert_row

KEYED = {
    "has_state_key": True,
    "affected_total": 0,
    "affected_since_acknowledged": 0,
    "affected_latest_at": None,
}


def _row(payload: dict, **counts) -> dict:
    return _alert_row({"id": "a1", "type": "document_failed", "payload": payload, **{**KEYED, **counts}})


def test_an_intake_alert_has_no_document_and_no_count():
    alert = _row({"intake_rejection_id": "r1", "step": "email_intake", "error_code": "DOC-030"})
    assert alert["no_document"] == "email_intake"
    assert alert["affected_documents"] is None


def test_an_import_alert_has_no_document_and_no_count():
    """PR 1b-ii (D-207): an import never makes a document."""
    alert = _alert_row(
        {"id": "a1", "type": "import_failed", **KEYED,
         "payload": {"import_id": "i1", "step": "catalog_import", "error_code": "IMP-011"}}
    )
    assert alert["no_document"] == "catalog_import"
    assert alert["affected_documents"] is None


def test_a_document_alert_keeps_its_count():
    alert = _row({"first_document_id": "d1", "step": "parsing", "error_code": "DOC-030"}, affected_total=3)
    assert alert["no_document"] is None
    assert alert["affected_documents"] == {"total": 3, "since_acknowledged": 0, "latest_at": None}


def test_an_alert_not_raised_per_state_has_neither():
    alert = _row({"export_id": "e1"}, has_state_key=False)
    assert alert["no_document"] is None and alert["affected_documents"] is None
