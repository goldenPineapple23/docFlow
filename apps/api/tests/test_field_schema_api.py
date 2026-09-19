# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
Per-tenant field settings through the Console (D-120), against the real
database and RLS (migration 0016).

What this proves:
  * a tenant with no saved version is checked exactly as before (version 0,
    the built-in defaults);
  * saving writes a NEW version, logged with the founder acting-as, and the
    history lists it;
  * a locked field can't be switched off (FLD-002) and an unknown field is
    refused (FLD-001), with nothing saved;
  * making a field optional or hidden changes what the order is checked on:
    the missing-required finding goes, a hidden field raises nothing, and the
    order's confidence is recomputed from the REQUIRED fields only -- the
    0%-on-an-empty-optional-field problem from D-115;
  * approved orders are never re-checked or re-scored (Section 7.3);
  * the review screen is told which fields are required and which are hidden,
    as of the version that read the document;
  * the routes are 404 to anyone but a platform admin.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest
from docflow_core.db import platform_session, tenant_session
from docflow_core.validation import validate_document
from sqlalchemy import text

from tests.conftest import requires_console_schema, requires_review_schema
from tests.test_console_api import _Console, _environment  # noqa: F401 -- fixtures
from tests.test_review_api import CLEAN_LINES, _ReviewTenant, _secrets  # noqa: F401 -- fixture


def _schema_available() -> bool:
    try:
        with platform_session() as session:
            session.execute(text("SELECT id FROM tenant_field_schemas LIMIT 0"))
        return True
    except Exception:
        return False


requires_field_schema = pytest.mark.skipif(
    not _schema_available(),
    reason="supabase/migrations/0016_field_schema.sql has not been applied yet -- see D-120.",
)

# An order with no payment terms -- the shape that showed 0% -- and a ship-to
# that was read badly, which is what raises a low-confidence check (an EMPTY
# optional field raises none, by D-115).
THIN_HEADER = {
    "payment_terms": None,
    "ship_to_address": "12 Test Way, Testville",
    "order_total": Decimal("570.00"),
}
THIN_CONFIDENCE = {
    "po_number": 0.97,
    "buyer_name": 0.95,
    "order_total": 0.93,
    "currency": 0.91,
    "payment_terms": 0.0,
    "ship_to_address": 0.20,
}


def _base(tenant) -> str:
    return f"/admin/tenants/{tenant.tenant_id}/field-schema"


def _cleanup(tenant_id) -> None:
    with platform_session() as session:
        session.execute(text("DELETE FROM tenant_field_schemas WHERE tenant_id = :t"), {"t": str(tenant_id)})
        session.execute(text("DELETE FROM admin_actions WHERE target_tenant_id = :t"), {"t": str(tenant_id)})


def _warning_codes(document_id: UUID) -> set[str]:
    with platform_session() as session:
        return {
            row[0]
            for row in session.execute(
                text("SELECT code FROM document_warnings WHERE document_id = :d AND deleted_at IS NULL"),
                {"d": str(document_id)},
            )
        }


def _document(document_id: UUID) -> dict:
    with platform_session() as session:
        return dict(
            session.execute(
                text("SELECT status, overall_confidence, field_schema_version FROM documents WHERE id = :d"),
                {"d": str(document_id)},
            )
            .mappings()
            .one()
        )


@requires_field_schema
@requires_console_schema
@requires_review_schema
def test_a_tenant_starts_on_the_built_in_defaults(client):
    with _Console() as console, _ReviewTenant("Acme Test Distributor -- fields default") as tenant:
        try:
            body = client.get(_base(tenant), headers=console.headers()).json()
            assert body["schema"]["version"] == 0 and body["history"] == []
            states = {f["name"]: f["state"] for f in body["schema"]["fields"] if f["level"] == "header"}
            assert states["po_number"] == "required" and states["currency"] == "required"
            assert states["payment_terms"] == "optional"
            assert [f for f in body["schema"]["fields"] if f["locked"]][0]["name"] == "po_number"
        finally:
            _cleanup(tenant.tenant_id)


@requires_field_schema
@requires_console_schema
@requires_review_schema
def test_saving_writes_a_version_and_changes_what_an_open_order_is_checked_on(client):
    with _Console() as console, _ReviewTenant("Acme Test Distributor -- fields save") as tenant:
        try:
            document = tenant.create_document(
                header=THIN_HEADER, lines=CLEAN_LINES, header_confidence=THIN_CONFIDENCE
            )
            with tenant_session(tenant.tenant_id) as session:
                validate_document(session, tenant.tenant_id, document)
            assert "VAL-010" in _warning_codes(document)  # low confidence on ship-to/payment terms

            response = client.put(
                _base(tenant),
                headers=console.headers(),
                json={
                    "fields": {"header": {"payment_terms": "hidden", "ship_to_address": "hidden"}},
                    "note": "They never put terms on a PO",
                    "apply_to_open_documents": True,
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["schema"]["version"] == 1
            assert response.json()["rechecked_documents"] == 1

            # Confidence is now the lowest REQUIRED field, not the empty ones.
            after = _document(document)
            assert Decimal(after["overall_confidence"]) == Decimal("0.910")
            assert after["field_schema_version"] == 1
            # Nothing is raised about a field this tenant never sees.
            assert "VAL-010" not in _warning_codes(document)

            listed = client.get(_base(tenant), headers=console.headers()).json()
            assert listed["schema"]["version"] == 1
            assert listed["history"][0]["note"] == "They never put terms on a PO"
            assert listed["history"][0]["acting_as_tenant_id"] == str(tenant.tenant_id)
            with platform_session() as session:
                saved = session.execute(
                    text(
                        "SELECT count(*) FROM admin_actions WHERE target_tenant_id = :t "
                        "AND action = 'field_schema_save'"
                    ),
                    {"t": str(tenant.tenant_id)},
                ).scalar_one()
            assert saved == 1
        finally:
            _cleanup(tenant.tenant_id)


@requires_field_schema
@requires_console_schema
@requires_review_schema
def test_a_field_made_required_is_then_missing_on_an_order_that_lacks_it(client):
    with _Console() as console, _ReviewTenant("Acme Test Distributor -- fields required") as tenant:
        try:
            document = tenant.create_document(
                header=THIN_HEADER, lines=CLEAN_LINES, header_confidence=THIN_CONFIDENCE
            )
            with tenant_session(tenant.tenant_id) as session:
                validate_document(session, tenant.tenant_id, document)
            assert "VAL-006" not in _warning_codes(document)

            assert (
                client.put(
                    _base(tenant),
                    headers=console.headers(),
                    json={"fields": {"header": {"payment_terms": "required"}}, "note": None},
                ).status_code
                == 200
            )
            assert "VAL-006" in _warning_codes(document)  # now a missing required field
            assert Decimal(_document(document)["overall_confidence"]) == Decimal("0")
        finally:
            _cleanup(tenant.tenant_id)


@requires_field_schema
@requires_console_schema
@requires_review_schema
def test_an_approved_order_is_never_rescored_or_rechecked(client):
    with _Console() as console, _ReviewTenant("Acme Test Distributor -- fields approved") as tenant:
        try:
            document = tenant.create_document(
                header=THIN_HEADER, lines=CLEAN_LINES, header_confidence=THIN_CONFIDENCE
            )
            with platform_session() as session:
                session.execute(
                    text(
                        "UPDATE documents SET status = 'approved', approved_at = now(), "
                        "approved_by = :u, approved_json = '{}'::jsonb, "
                        "approved_snapshot_hash = 'test-snapshot', overall_confidence = 0.5 "
                        "WHERE id = :d"
                    ),
                    {"d": str(document), "u": str(tenant.user_id)},
                )
            response = client.put(
                _base(tenant),
                headers=console.headers(),
                json={"fields": {"header": {"payment_terms": "hidden"}}, "note": None},
            )
            assert response.json()["rechecked_documents"] == 0
            after = _document(document)
            assert Decimal(after["overall_confidence"]) == Decimal("0.500")
            assert after["field_schema_version"] is None
        finally:
            _cleanup(tenant.tenant_id)


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"header": {"po_number": "optional"}}, "FLD-002"),
        ({"line": {"quantity": "hidden"}}, "FLD-002"),
        ({"header": {"not_a_field": "required"}}, "FLD-001"),
        ({"header": {"notes": "sometimes"}}, "FLD-001"),
    ],
)
@requires_field_schema
@requires_console_schema
@requires_review_schema
def test_a_refused_change_saves_nothing(client, fields, code):
    with _Console() as console, _ReviewTenant("Acme Test Distributor -- fields refused") as tenant:
        try:
            response = client.put(
                _base(tenant), headers=console.headers(), json={"fields": fields, "note": None}
            )
            assert response.status_code == 422, response.text
            assert response.json()["detail"]["code"] == code
            assert client.get(_base(tenant), headers=console.headers()).json()["schema"]["version"] == 0
        finally:
            _cleanup(tenant.tenant_id)


@requires_field_schema
@requires_console_schema
@requires_review_schema
def test_the_review_screen_is_told_what_is_required_and_what_is_hidden(client):
    with _Console() as console, _ReviewTenant("Acme Test Distributor -- fields review") as tenant:
        try:
            document = tenant.create_document(
                header=THIN_HEADER, lines=CLEAN_LINES, header_confidence=THIN_CONFIDENCE
            )
            assert (
                client.put(
                    _base(tenant),
                    headers=console.headers(),
                    json={
                        "fields": {"header": {"payment_terms": "hidden", "order_date": "required"}},
                        "note": None,
                    },
                ).status_code
                == 200
            )

            detail = client.get(f"/review/documents/{document}", headers=tenant.headers()).json()[
                "field_schema"
            ]
            states = {f["name"]: f["state"] for f in detail["fields"] if f["level"] == "header"}
            assert detail["version"] == 1
            assert states["payment_terms"] == "hidden" and states["order_date"] == "required"
            # The value is still stored -- hiding changes what is shown, not
            # what is read.
            assert (
                "payment_terms"
                in client.get(f"/review/documents/{document}", headers=tenant.headers()).json()["header"]
            )
        finally:
            _cleanup(tenant.tenant_id)


@requires_field_schema
@requires_console_schema
@requires_review_schema
def test_a_document_keeps_the_version_it_was_read_under(client):
    with _Console() as console, _ReviewTenant("Acme Test Distributor -- fields version") as tenant:
        try:
            document = tenant.create_document(
                header=THIN_HEADER, lines=CLEAN_LINES, header_confidence=THIN_CONFIDENCE
            )
            client.put(
                _base(tenant),
                headers=console.headers(),
                json={"fields": {"header": {"payment_terms": "hidden"}}, "note": None},
            )
            # A second version the document was never read under.
            client.put(
                _base(tenant),
                headers=console.headers(),
                json={
                    "fields": {"header": {"notes": "hidden"}},
                    "note": None,
                    "apply_to_open_documents": False,
                },
            )
            detail = client.get(f"/review/documents/{document}", headers=tenant.headers()).json()[
                "field_schema"
            ]
            assert detail["version"] == 1
            states = {f["name"]: f["state"] for f in detail["fields"] if f["level"] == "header"}
            assert states["payment_terms"] == "hidden" and states["notes"] == "optional"
        finally:
            _cleanup(tenant.tenant_id)


def test_the_field_schema_routes_are_404_to_everyone_else(client):
    path = f"/admin/tenants/{UUID(int=1)}/field-schema"
    assert client.get(path).status_code == 404
    assert client.put(path, json={"fields": {}, "note": None}).status_code == 404


@requires_field_schema
@requires_console_schema
@requires_review_schema
def test_one_tenants_schema_is_not_another_tenants(client):
    with (
        _Console() as console,
        _ReviewTenant("Acme Test Distributor -- fields A") as tenant_a,
        _ReviewTenant("Acme Test Distributor -- fields B") as tenant_b,
    ):
        try:
            assert (
                client.put(
                    _base(tenant_a),
                    headers=console.headers(),
                    json={"fields": {"header": {"notes": "hidden"}}, "note": None},
                ).status_code
                == 200
            )
            b = client.get(_base(tenant_b), headers=console.headers()).json()
            assert b["schema"]["version"] == 0
            assert {f["name"]: f["state"] for f in b["schema"]["fields"]}["notes"] == "optional"
        finally:
            _cleanup(tenant_a.tenant_id)
            _cleanup(tenant_b.tenant_id)


@requires_field_schema
@requires_console_schema
@requires_review_schema
def test_an_unsaved_tenant_still_validates_exactly_as_before(client):
    """No saved version must behave identically to the pre-D-120 code."""
    with _ReviewTenant("Acme Test Distributor -- fields none") as tenant:
        document = tenant.create_document(
            header={"po_number": None, "order_total": Decimal("570.00")},
            lines=CLEAN_LINES,
            header_confidence=THIN_CONFIDENCE,
        )
        with tenant_session(tenant.tenant_id) as session:
            validate_document(session, tenant.tenant_id, document)
        assert "VAL-006" in _warning_codes(document)  # PO number still required
