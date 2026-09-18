"""
The export API (CLAUDE.md Section 7.4, Phase 4; DECISIONS.md D-098).

    POST /review/documents/{id}/exports      ask for a file of the approved order
    GET  /review/documents/{id}/exports      the document's export history
    GET  /review/exports/{export_id}          one export; a download link once ready
    GET  /review/exports/{export_id}/download the file, against that link's token

A thin HTTP shell over `docflow_core.export_jobs`, like the review router is
over `docflow_core.review`. It never builds a file: that loads openpyxl, and
the web process never does (Section 7.11). The worker builds and verifies it,
from the snapshot this request pinned.

**Who may export.** Every member of the tenant, viewers included. Exporting
reads data a person has already approved and changes nothing about it, and
the catalog already promises viewers they can "download exports" (AUTH-002).
Recorded as DECISIONS.md D-100.

**The download is two steps**, as the original-document viewer is: a
session-authenticated call that mints a short-lived token, then a plain GET
that serves the file against it. A download is a browser navigation and
carries no Authorization header, so the token has to stand on its own. The
storage path never reaches the client (Section 7.4).
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from docflow_core.db import tenant_session
from docflow_core.errors import get_error
from docflow_core.export_jobs import (
    FORMAT_LABELS,
    ExportRequestError,
    get_export,
    list_exports,
    request_export,
)
from docflow_core.signed_urls import InvalidSignedUrl, mint_export_token, verify_export_token
from docflow_core.storage import read_file
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import text

from app.celery_client import celery_client
from app.deps import AuthenticatedIdentity, get_current_identity, require_tenant_member
from app.errors import catalog_error

router = APIRouter(prefix="/review", tags=["exports"])

# The file's media type and extension, per format. Duplicated from
# docflow_core.exports deliberately: that module loads openpyxl and is never
# imported here (apps/api/tests/test_parsing_boundary.py).
_MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
    "iif": "application/octet-stream",
}


class ExportBody(BaseModel):
    # A plain string, not a Literal: an unknown format is answered with the
    # catalog's EXP-002, not FastAPI's generic validation body (7.16.5).
    format: str


def _user_id(identity: AuthenticatedIdentity) -> UUID:
    if identity.local_user_id is None:
        raise HTTPException(status_code=403, detail="This account cannot act on documents.")
    return identity.local_user_id


def _export_json(row: dict[str, Any]) -> dict[str, Any]:
    error = get_error(row["error_code"]) if row.get("error_code") else None
    return {
        "id": str(row["id"]),
        "document_id": str(row["document_id"]),
        "format": row["format"],
        "format_label": FORMAT_LABELS.get(row["format"], row["format"]),
        "status": row["status"],
        "error": (
            {"code": error.code, "title": error.title, "message": error.message, "action": error.action}
            if error
            else None
        ),
        "sha256": row["sha256"],
        "byte_size": row["byte_size"],
        "snapshot_hash": row["snapshot_hash"],
        # False once the order has been re-approved: this file is of an
        # earlier approval, and the screen says so.
        "is_current_snapshot": bool(row["is_current_snapshot"]),
        "requested_at": row["requested_at"].isoformat() if row["requested_at"] else None,
        "generated_at": row["generated_at"].isoformat() if row["generated_at"] else None,
        "generated_by": row["generated_by_email"],
        "by_docflow_support": row["acting_as_tenant_id"] is not None,
    }


@router.post("/documents/{document_id}/exports", status_code=202)
def create_export(
    document_id: UUID,
    body: ExportBody,
    identity: AuthenticatedIdentity = Depends(get_current_identity),
) -> dict:
    tenant_id = require_tenant_member(identity)
    user_id = _user_id(identity)
    with tenant_session(tenant_id) as session:
        try:
            export_id = request_export(session, tenant_id, document_id, body.format, user_id=user_id)
        except LookupError as exc:
            raise HTTPException(status_code=404) from exc
        except ExportRequestError as exc:
            status = 422 if exc.code == "EXP-002" else 409
            raise catalog_error(exc.code, status_code=status) from exc

    # After the commit, so the worker never looks for a row that isn't
    # visible yet -- the same ordering the upload endpoint uses. Interactive
    # queue: a person is waiting for this file.
    celery_client.send_task(
        "docflow.generate_export", args=[str(tenant_id), str(export_id)], queue="interactive"
    )

    with tenant_session(tenant_id) as session:
        row = get_export(session, export_id)
    assert row is not None
    return {"export": _export_json(row)}


@router.get("/documents/{document_id}/exports")
def document_exports(
    document_id: UUID,
    identity: AuthenticatedIdentity = Depends(get_current_identity),
) -> dict:
    tenant_id = require_tenant_member(identity)
    with tenant_session(tenant_id) as session:
        exists = session.execute(
            text("SELECT 1 FROM documents WHERE id = :id AND deleted_at IS NULL"),
            {"id": str(document_id)},
        ).first()
        if exists is None:
            raise HTTPException(status_code=404)
        rows = list_exports(session, document_id)
    return {"exports": [_export_json(row) for row in rows]}


@router.get("/exports/{export_id}")
def export_status(
    export_id: UUID,
    identity: AuthenticatedIdentity = Depends(get_current_identity),
) -> dict:
    """
    One export. Once it is ready, also a download URL that works for a few
    minutes -- minted only after RLS has let this tenant read the row.
    """
    tenant_id = require_tenant_member(identity)
    with tenant_session(tenant_id) as session:
        row = get_export(session, export_id)
    if row is None:
        raise HTTPException(status_code=404)

    response: dict[str, Any] = {"export": _export_json(row)}
    if row["status"] == "ready":
        token, expires_at = mint_export_token(export_id, tenant_id)
        response["download"] = {
            "url": f"/review/exports/{export_id}/download?token={token}",
            "expires_at": expires_at,
        }
    return response


_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _download_filename(snapshot: dict[str, Any] | None, fmt: str) -> str:
    """
    `PO-<number>.<ext>`, from the approved PO number with everything but
    letters, digits, dot, dash and underscore removed -- the number came from
    a stranger's document (Section 7.11: filenames are untrusted).
    """
    po_number = ((snapshot or {}).get("header") or {}).get("po_number") or ""
    safe = _UNSAFE_FILENAME_CHARS.sub("-", po_number).strip(".-")[:40]
    return f"PO-{safe}.{fmt}" if safe else f"purchase-order.{fmt}"


@router.get("/exports/{export_id}/download")
def download_export(export_id: UUID, token: str = Query(min_length=1)) -> Response:
    """
    Serve the file against a token from `export_status`. The tenant comes out
    of the token, whose signature covers it and the export id and the
    purpose (so a viewer token cannot be used here), and it opens an ordinary
    tenant-scoped session -- RLS still decides what can be read.
    """
    try:
        verified = verify_export_token(token, export_id)
    except InvalidSignedUrl as exc:
        raise catalog_error("EXP-003", status_code=404) from exc

    with tenant_session(verified.tenant_id) as session:
        row = session.execute(
            text(
                """
                SELECT e.format, e.status, e.storage_path, s.snapshot
                FROM exports e JOIN document_snapshots s ON s.id = e.snapshot_id
                WHERE e.id = :id AND e.deleted_at IS NULL
                """
            ),
            {"id": str(export_id)},
        ).mappings().first()
    if row is None or row["status"] != "ready":
        raise HTTPException(status_code=404)

    filename = _download_filename(row["snapshot"], row["format"])
    return Response(
        content=read_file(row["storage_path"]),
        media_type=_MEDIA_TYPES[row["format"]],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            # Never rendered, whatever a browser thinks of the type.
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
