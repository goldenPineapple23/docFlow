"""
The export API and `docflow_core.export_jobs` against the real database and
the real RLS policies (CLAUDE.md Section 7.4 / 7.5, Phase 4).

What this file proves, end to end through HTTP:
  * an approved order exports in all four formats, the downloaded file parses
    back to exactly the approved snapshot, and its SHA-256 is the one recorded;
  * exporting the same snapshot twice records the same SHA-256;
  * Section 7.5's required tests for downloads: another tenant can neither see
    nor fetch an export, and a download link cannot be reused for another
    export, edited to another tenant, or swapped for a viewer link;
  * a re-approval after the click cannot change what the file contains.

The worker is not running here: the POST's enqueue is captured, and
`run_export` -- the whole of the worker task -- is called directly.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from docflow_core import exports
from docflow_core.db import platform_session, tenant_session
from docflow_core.export_jobs import run_export
from docflow_core.review import EditRequest, apply_edits, approve_document
from docflow_core.signed_urls import mint_document_token
from sqlalchemy import text

from tests.conftest import requires_exports_schema
from tests.test_review_api import CLEAN_LINES, _ReviewTenant, _secrets  # noqa: F401 -- fixture

HEADER = {
    "po_number": "ACME-4410",
    "order_date": "2025-05-28",
    "buyer_name": "Acme Test Buyer",
    "currency": "USD",
    "order_total": Decimal("570.00"),
}


@pytest.fixture(autouse=True)
def _capture_enqueue(monkeypatch):
    sent: list[tuple[str, list]] = []

    class _FakeCelery:
        def send_task(self, name, args=None, queue=None):
            sent.append((name, args))

    monkeypatch.setattr("app.routers.exports.celery_client", _FakeCelery())
    return sent


def _approved_document(tenant: _ReviewTenant, *, lines=None) -> UUID:
    document = tenant.create_document(header=HEADER, lines=lines or CLEAN_LINES)
    with tenant_session(tenant.tenant_id) as session:
        approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)
    return document


def _current_snapshot(document_id: UUID) -> dict:
    with platform_session() as session:
        return session.execute(
            text(
                "SELECT snapshot FROM document_snapshots WHERE document_id = :id "
                "AND superseded_at IS NULL AND deleted_at IS NULL"
            ),
            {"id": str(document_id)},
        ).scalar_one()


def _export(client, tenant: _ReviewTenant, document: UUID, fmt: str) -> dict:
    response = client.post(
        f"/review/documents/{document}/exports", headers=tenant.headers(), json={"format": fmt}
    )
    assert response.status_code == 202, response.text
    body = response.json()["export"]
    assert body["status"] == "pending"
    run_export(tenant.tenant_id, UUID(body["id"]))
    return client.get(f"/review/exports/{body['id']}", headers=tenant.headers()).json()


def _download(client, status: dict):
    return client.get(status["download"]["url"])


# ── The Phase 4 exit criteria, through the real stack ───────────────────────


@requires_exports_schema
@pytest.mark.parametrize("fmt", exports.FORMATS)
def test_an_approved_order_downloads_as_exactly_the_approved_data(client, fmt, _capture_enqueue):
    with _ReviewTenant(f"Acme Test Distributor -- export {fmt}") as tenant:
        document = _approved_document(tenant)

        status = _export(client, tenant, document, fmt)

        assert _capture_enqueue[-1][0] == "docflow.generate_export"
        assert status["export"]["status"] == "ready"
        response = _download(client, status)
        assert response.status_code == 200
        assert response.headers["content-disposition"] == f'attachment; filename="PO-ACME-4410.{fmt}"'
        assert response.headers["x-content-type-options"] == "nosniff"

        snapshot = _current_snapshot(document)
        parsed = exports.PARSERS[fmt](response.content)
        assert parsed == exports.comparable_view(exports.order_view(snapshot), fmt)
        assert hashlib.sha256(response.content).hexdigest() == status["export"]["sha256"]
        assert status["export"]["byte_size"] == len(response.content)


@requires_exports_schema
def test_exporting_twice_records_the_same_checksum(client):
    with _ReviewTenant("Acme Test Distributor -- deterministic") as tenant:
        document = _approved_document(tenant)
        first = _export(client, tenant, document, "xlsx")["export"]
        second = _export(client, tenant, document, "xlsx")["export"]
        assert first["id"] != second["id"]
        assert first["sha256"] == second["sha256"]
        assert first["snapshot_hash"] == second["snapshot_hash"]


@requires_exports_schema
def test_the_first_export_marks_the_document_exported_and_the_history_lists_it(client):
    with _ReviewTenant("Acme Test Distributor -- history") as tenant:
        document = _approved_document(tenant)
        _export(client, tenant, document, "csv")

        detail = client.get(f"/review/documents/{document}", headers=tenant.headers()).json()
        assert detail["document"]["status"] == "exported"

        history = client.get(
            f"/review/documents/{document}/exports", headers=tenant.headers()
        ).json()["exports"]
        assert [row["format"] for row in history] == ["csv"]
        assert history[0]["is_current_snapshot"] is True
        assert history[0]["generated_by"].endswith("@example.test")


@requires_exports_schema
def test_the_matched_catalog_sku_is_frozen_into_the_export(client):
    """D-099: the tenant's own SKU leads the line; the buyer's follows."""
    with _ReviewTenant("Acme Test Distributor -- catalog sku") as tenant:
        item = tenant.seed_item("TEST-CAT-1001", "Test Beans 5lb")
        lines = [{**CLEAN_LINES[0], "sku": "BUYER-77", "matched_item_id": item}]
        document = _approved_document(tenant, lines=lines)

        # Renaming the catalog item after approval must not change the file.
        with platform_session() as session:
            session.execute(
                text("UPDATE items SET sku = 'TEST-CAT-RENAMED' WHERE id = :id"), {"id": str(item)}
            )

        response = _download(client, _export(client, tenant, document, "csv"))
        parsed = exports.parse_csv(response.content)
        assert parsed["lines"][0]["catalog_sku"] == "TEST-CAT-1001"
        assert parsed["lines"][0]["sku"] == "BUYER-77"


# ── Only approved data, only the approval that was clicked ──────────────────


@requires_exports_schema
def test_an_unapproved_order_cannot_be_exported(client):
    with _ReviewTenant("Acme Test Distributor -- unapproved") as tenant:
        document = tenant.create_document(header=HEADER, lines=CLEAN_LINES)
        response = client.post(
            f"/review/documents/{document}/exports", headers=tenant.headers(), json={"format": "csv"}
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "EXP-001"


@requires_exports_schema
def test_an_unknown_format_is_a_catalog_error(client):
    with _ReviewTenant("Acme Test Distributor -- format") as tenant:
        document = _approved_document(tenant)
        response = client.post(
            f"/review/documents/{document}/exports", headers=tenant.headers(), json={"format": "pdf"}
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "EXP-002"


@requires_exports_schema
def test_an_order_reopened_after_the_click_still_gets_the_approval_that_was_clicked(client):
    with _ReviewTenant("Acme Test Distributor -- reopened") as tenant:
        document = _approved_document(tenant)
        approved = _current_snapshot(document)
        export_id = client.post(
            f"/review/documents/{document}/exports", headers=tenant.headers(), json={"format": "json"}
        ).json()["export"]["id"]

        # Someone edits the order before the worker gets to it (Section 7.3:
        # an edit after approval reopens it).
        with tenant_session(tenant.tenant_id) as session:
            apply_edits(
                session,
                tenant.tenant_id,
                document,
                user_id=tenant.user_id,
                request=EditRequest(header={"po_number": "ACME-4411"}),
            )
        run_export(tenant.tenant_id, UUID(export_id))

        status = client.get(f"/review/exports/{export_id}", headers=tenant.headers()).json()
        parsed = exports.parse_json(_download(client, status).content)
        assert parsed["header"]["po_number"] == approved["header"]["po_number"] == "ACME-4410"
        assert status["export"]["is_current_snapshot"] is False
        detail = client.get(f"/review/documents/{document}", headers=tenant.headers()).json()
        assert detail["document"]["status"] == "needs_review"


@requires_exports_schema
def test_a_finished_export_row_cannot_be_rewritten(client):
    with _ReviewTenant("Acme Test Distributor -- immutable") as tenant:
        document = _approved_document(tenant)
        export_id = _export(client, tenant, document, "csv")["export"]["id"]
        with pytest.raises(Exception, match="finished and cannot be changed"):
            with platform_session() as session:
                session.execute(
                    text("UPDATE exports SET sha256 = 'forged' WHERE id = :id"), {"id": export_id}
                )


@requires_exports_schema
def test_a_file_quickbooks_would_reject_fails_with_a_reason_not_a_file(client):
    with _ReviewTenant("Acme Test Distributor -- iif refused") as tenant:
        # The total includes freight the lines don't show.
        document = tenant.create_document(
            header={**HEADER, "order_total": Decimal("600.00")}, lines=CLEAN_LINES
        )
        with tenant_session(tenant.tenant_id) as session:
            approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)
        status = _export(client, tenant, document, "iif")
        assert status["export"]["status"] == "failed"
        assert status["export"]["error"]["code"] == "EXP-006"
        assert "download" not in status
        # CSV of the same order is fine.
        assert _export(client, tenant, document, "csv")["export"]["status"] == "ready"


# ── Roles ───────────────────────────────────────────────────────────────────


@requires_exports_schema
def test_a_viewer_can_export(client):
    """D-100: exporting reads approved data; AUTH-002 promises viewers it."""
    with _ReviewTenant("Acme Test Distributor -- viewer export", role="viewer") as tenant:
        document = _approved_document(tenant)
        assert _export(client, tenant, document, "csv")["export"]["status"] == "ready"


# ── Section 7.5: tenant isolation for exports and their links ───────────────


@requires_exports_schema
def test_another_tenant_can_neither_see_nor_request_an_export(client):
    with _ReviewTenant("Acme Test Distributor -- owner A") as a, _ReviewTenant(
        "Beacon Test Supply -- intruder B"
    ) as b:
        document = _approved_document(a)
        export_id = _export(client, a, document, "csv")["export"]["id"]

        assert client.get(f"/review/exports/{export_id}", headers=b.headers()).status_code == 404
        assert (
            client.get(f"/review/documents/{document}/exports", headers=b.headers()).status_code
            == 404
        )
        response = client.post(
            f"/review/documents/{document}/exports", headers=b.headers(), json={"format": "csv"}
        )
        assert response.status_code == 404


@requires_exports_schema
def test_a_download_link_works_only_for_its_own_export_and_tenant(client):
    with _ReviewTenant("Acme Test Distributor -- link A") as a, _ReviewTenant(
        "Beacon Test Supply -- link B"
    ) as b:
        first = _export(client, a, _approved_document(a), "csv")
        second = _export(client, a, _approved_document(a), "csv")
        token = first["download"]["url"].split("token=")[1]
        other_id = second["export"]["id"]

        # The genuine link works.
        assert _download(client, first).status_code == 200

        # Reused for a different export of the same tenant.
        reused = client.get(f"/review/exports/{other_id}/download?token={token}")
        assert reused.status_code == 404
        assert reused.json()["detail"]["code"] == "EXP-003"

        # Edited to name another tenant: the signature covers the tenant.
        forged = token.replace(str(a.tenant_id), str(b.tenant_id))
        first_id = first["export"]["id"]
        assert client.get(f"/review/exports/{first_id}/download?token={forged}").status_code == 404

        # A viewer (original-document) token for the same id is not an export token.
        viewer_token, _ = mint_document_token(UUID(first_id), a.tenant_id)
        assert (
            client.get(f"/review/exports/{first_id}/download?token={viewer_token}").status_code
            == 404
        )


@requires_exports_schema
def test_the_storage_path_never_reaches_the_client(client):
    with _ReviewTenant("Acme Test Distributor -- no paths") as tenant:
        status = _export(client, tenant, _approved_document(tenant), "csv")
        assert "tenants/" not in str(status)
        assert "storage_path" not in status["export"]


@requires_exports_schema
def test_nonexistent_export_is_404_not_500(client):
    with _ReviewTenant("Acme Test Distributor -- missing") as tenant:
        response = client.get(f"/review/exports/{uuid4()}", headers=tenant.headers())
        assert response.status_code == 404
