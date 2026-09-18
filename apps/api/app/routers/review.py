"""
The review API (CLAUDE.md Section 7.3, Phase 3 slice 2).

Every route here is a thin HTTP shell over `docflow_core.review`. The rules
about what a human may change, what approval requires, and what gets recorded
live in that module, so the Console's acting-as path (Section 7.15.1) reaches
exactly the same logic with a different actor rather than through a second
implementation (Section 10: "write a second ... review component for the
Console" is forbidden).

Three things this layer is responsible for, and nothing else:

  * **`tenant_id` comes from the session, once.** It is never read from a
    path, a body or a query string (Section 7.5 / Section 10). Every handler
    gets it from `require_tenant_member` / `require_reviewer` and passes it
    to a tenant-scoped session.
  * **Permissions are enforced here, not in the UI.** A `viewer` reads; a
    reviewer, admin or owner writes (Section 3).
  * **Every failure is a catalog code.** Nothing in this file builds a
    user-facing sentence; `app.errors` renders them from the catalog
    (Section 7.16.5).

The original-document route is the one place bytes leave the system, and it
is deliberately two endpoints: one that mints a short-lived signed token and
one that serves the file against it, with a strict CSP and no storage path
ever visible to the client (Section 7.4 / 7.12).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from docflow_core import file_types
from docflow_core.config import get_settings
from docflow_core.db import tenant_session
from docflow_core.errors import get_error
from docflow_core.matching import confirm_sku_mapping
from docflow_core.review import (
    EditRequest,
    ReviewError,
    WarningAcknowledgement,
    apply_edits,
    approve_document,
    document_version,
    reject_document,
    review_trail,
    start_review,
    unacknowledged_warnings,
)
from docflow_core.signed_urls import (
    InvalidSignedUrl,
    mint_document_token,
    verify_document_token,
)
from docflow_core.storage import read_file
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.deps import AuthenticatedIdentity, get_current_identity, require_reviewer, require_tenant_member
from app.errors import catalog_error

router = APIRouter(prefix="/review", tags=["review"])

# The statuses a reviewer's queue can show. `quarantined` is deliberately
# absent -- held documents have their own screen (7.16.4) and must not appear
# in the review queue until released.
QUEUE_STATUSES = ("needs_review", "approved", "rejected", "exported", "failed")

# Section 7.12: the viewer renders inside a sandboxed iframe with a strict
# CSP. These headers are what make "sandboxed" true of the response itself,
# so a document that somehow contained active content still could not run it
# or call home.
#
# `frame-ancestors` names the web app's origins rather than `'self'`. The API
# and the web app are always separate origins, so `'self'` means "only a page
# served by the API may frame this" -- which is no page at all. An earlier
# version used `'self'` and the browser refused to render the document, so
# the viewer panel was blank for every document while every request returned
# 200 (DECISIONS.md D-089).
def _viewer_headers() -> dict[str, str]:
    settings = get_settings()
    origins = " ".join(
        origin.strip().rstrip("/")
        for origin in settings.cors_allowed_origins.split(",")
        if origin.strip()
    ) or "'self'"
    return {
        "Content-Security-Policy": (
            "default-src 'none'; img-src 'self' data:; object-src 'none'; "
            "script-src 'none'; style-src 'unsafe-inline'; "
            f"frame-ancestors {origins}; base-uri 'none'; form-action 'none'"
        ),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Referrer-Policy": "no-referrer",
    }


def _review_error(exc: ReviewError) -> HTTPException:
    """
    A `ReviewError` as an HTTP response.

    409 rather than 400 for every one of them: each means "the document is
    not in the state your request assumed", which is a conflict, not a
    malformed request. The UI distinguishes them by the catalog code.
    """
    return catalog_error(exc.code, status_code=409, extra=exc.detail)


# ── Request bodies ──────────────────────────────────────────────────────────


class LineEdit(BaseModel):
    line_id: UUID
    fields: dict[str, str | None]


class EditBody(BaseModel):
    header: dict[str, str | None] = Field(default_factory=dict)
    lines: list[LineEdit] = Field(default_factory=list)
    # The version the reviewer's screen was showing. Optional so a scripted
    # caller can opt out, required in practice by the UI -- see REV-005.
    expected_version: str | None = None


class AcknowledgementBody(BaseModel):
    warning_id: UUID
    code: str
    text: str
    note: str | None = None


class ApproveBody(BaseModel):
    acknowledgements: list[AcknowledgementBody] = Field(default_factory=list)


class RejectBody(BaseModel):
    note: str = Field(min_length=1)


class MappingBody(BaseModel):
    line_id: UUID
    item_id: UUID
    tenant_wide: bool = False


# ── The queue ───────────────────────────────────────────────────────────────


@router.get("/documents")
def list_documents(
    identity: AuthenticatedIdentity = Depends(get_current_identity),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """
    The review queue: oldest first, because the oldest unreviewed document is
    the one a buyer is waiting on.
    """
    tenant_id = require_tenant_member(identity)
    if status is not None and status not in QUEUE_STATUSES:
        raise HTTPException(status_code=422, detail="Unknown status filter.")

    with tenant_session(tenant_id) as session:
        rows = session.execute(
            text(
                """
                SELECT d.id, d.original_filename, d.status, d.source, d.created_at,
                       d.overall_confidence, d.is_test_batch, d.injection_suspected,
                       d.is_possible_duplicate, d.is_possible_change_order,
                       d.review_started_at, d.approved_at,
                       h.po_number, h.buyer_name, h.order_total, h.currency,
                       (SELECT count(*) FROM document_warnings w
                         WHERE w.document_id = d.id AND w.status = 'open'
                           AND w.deleted_at IS NULL) AS open_warnings
                FROM documents d
                LEFT JOIN document_headers h
                       ON h.document_id = d.id AND h.deleted_at IS NULL
                WHERE d.deleted_at IS NULL
                  AND d.status <> 'quarantined'
                  AND (CAST(:status AS text) IS NULL OR d.status = :status)
                ORDER BY d.created_at ASC, d.id ASC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"status": status, "limit": limit, "offset": offset},
        ).mappings().all()
        # The total is what lets the queue page instead of silently showing
        # only the first `limit` orders (DECISIONS.md D-097). Same filter as
        # above, kept in step by the test that pages through a full queue.
        total = session.execute(
            text(
                """
                SELECT count(*) FROM documents d
                WHERE d.deleted_at IS NULL
                  AND d.status <> 'quarantined'
                  AND (CAST(:status AS text) IS NULL OR d.status = :status)
                """
            ),
            {"status": status},
        ).scalar_one()

    return {
        "documents": [_queue_row(row) for row in rows],
        "limit": limit,
        "offset": offset,
        "total": total,
    }


def _queue_row(row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "original_filename": row["original_filename"],
        "status": row["status"],
        "source": row["source"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "approved_at": row["approved_at"].isoformat() if row["approved_at"] else None,
        "review_started": row["review_started_at"] is not None,
        "po_number": row["po_number"],
        "buyer_name": row["buyer_name"],
        # Money as a string, all the way to the client (Section 7.1).
        "order_total": _money(row["order_total"]),
        "currency": row["currency"],
        "overall_confidence": _money(row["overall_confidence"]),
        "open_warnings": row["open_warnings"],
        "is_test_batch": row["is_test_batch"],
        "injection_suspected": row["injection_suspected"],
        "is_possible_duplicate": row["is_possible_duplicate"],
        "is_possible_change_order": row["is_possible_change_order"],
    }


def _money(value) -> str | None:
    return str(value) if value is not None else None


# ── One document ────────────────────────────────────────────────────────────


@router.get("/documents/{document_id}")
def get_document(
    document_id: UUID,
    identity: AuthenticatedIdentity = Depends(get_current_identity),
) -> dict:
    """
    Everything the review screen renders, in one request.

    Opening a document stamps `review_started_at` the first time (7.15.3's
    review-time KPI), which is why a read has a side effect. It is idempotent
    and never resets, so a reviewer reopening a document does not restart the
    clock.
    """
    tenant_id = require_tenant_member(identity)

    with tenant_session(tenant_id) as session:
        document = session.execute(
            text(
                "SELECT id, original_filename, status, source, created_at, processed_at, "
                "overall_confidence, injection_suspected, is_test_batch, "
                "is_possible_duplicate, duplicate_of_document_id, "
                "is_possible_change_order, change_order_of_document_id, "
                "approved_at, approved_by, approved_snapshot_hash, review_started_at "
                "FROM documents WHERE id = :id AND deleted_at IS NULL"
            ),
            {"id": str(document_id)},
        ).mappings().first()
        if document is None:
            # RLS already hid another tenant's document, so this is a 404 for
            # "not yours" and "not there" alike -- a tenant cannot probe for
            # the existence of another tenant's document (Section 7.5).
            raise HTTPException(status_code=404)

        # A viewer opening a document should not start the review clock on
        # behalf of a reviewer who has not looked at it yet.
        if identity.role in ("owner", "admin", "reviewer"):
            start_review(session, document_id)

        header = session.execute(
            text(
                "SELECT po_number, order_date, requested_delivery_date, buyer_name, "
                "buyer_contact_email, ship_to_address, payment_terms, order_total, currency, "
                "notes, buyer_id, header_confidence, currency_inferred, field_provenance "
                "FROM document_headers WHERE document_id = :id AND deleted_at IS NULL"
            ),
            {"id": str(document_id)},
        ).mappings().first()

        lines = session.execute(
            text(
                "SELECT id, line_number, sku, description, unit, quantity, unit_price, "
                "line_total, confidence, matched_item_id, match_method, match_score, "
                "matched_uom, uom_mismatch, match_candidates, field_provenance "
                "FROM document_lines WHERE document_id = :id AND deleted_at IS NULL "
                "ORDER BY line_number"
            ),
            {"id": str(document_id)},
        ).mappings().all()

        warnings = session.execute(
            text(
                "SELECT id, code, severity, field_name, line_number, document_line_id, "
                "detail, status, acknowledged_at FROM document_warnings "
                "WHERE document_id = :id AND deleted_at IS NULL "
                "ORDER BY severity, code, line_number NULLS FIRST"
            ),
            {"id": str(document_id)},
        ).mappings().all()

        trail = review_trail(session, document_id)
        version = document_version(session, document_id)

    return {
        "document": {
            "id": str(document["id"]),
            "original_filename": document["original_filename"],
            "status": document["status"],
            "source": document["source"],
            "created_at": document["created_at"].isoformat() if document["created_at"] else None,
            "approved_at": document["approved_at"].isoformat() if document["approved_at"] else None,
            "approved_by": str(document["approved_by"]) if document["approved_by"] else None,
            "approved_snapshot_hash": document["approved_snapshot_hash"],
            "overall_confidence": _money(document["overall_confidence"]),
            "injection_suspected": document["injection_suspected"],
            "is_test_batch": document["is_test_batch"],
            "is_possible_duplicate": document["is_possible_duplicate"],
            "duplicate_of_document_id": _uuid(document["duplicate_of_document_id"]),
            "is_possible_change_order": document["is_possible_change_order"],
            "change_order_of_document_id": _uuid(document["change_order_of_document_id"]),
        },
        "header": _header_payload(header),
        "lines": [_line_payload(line) for line in lines],
        "warnings": [_warning_payload(w) for w in warnings],
        "trail": [_trail_payload(row) for row in trail],
        "version": version,
        "can_edit": identity.role in ("owner", "admin", "reviewer"),
    }


def _uuid(value) -> str | None:
    return str(value) if value else None


def _header_payload(header) -> dict[str, Any]:
    if header is None:
        return {}
    return {
        "po_number": header["po_number"],
        "order_date": header["order_date"],
        "requested_delivery_date": header["requested_delivery_date"],
        "buyer_name": header["buyer_name"],
        "buyer_contact_email": header["buyer_contact_email"],
        "ship_to_address": header["ship_to_address"],
        "payment_terms": header["payment_terms"],
        "order_total": _money(header["order_total"]),
        "currency": header["currency"],
        "notes": header["notes"],
        "currency_inferred": header["currency_inferred"],
        # Per-field confidence and provenance, so the UI can flag a low
        # value and explain why another was pre-filled (7.1 / 7.13).
        "confidence": header["header_confidence"] or {},
        "provenance": header["field_provenance"] or {},
    }


def _line_payload(line) -> dict[str, Any]:
    return {
        "id": str(line["id"]),
        "line_number": line["line_number"],
        "sku": line["sku"],
        "description": line["description"],
        "unit": line["unit"],
        "quantity": _money(line["quantity"]),
        "unit_price": _money(line["unit_price"]),
        "line_total": _money(line["line_total"]),
        "confidence": _money(line["confidence"]),
        "matched_item_id": _uuid(line["matched_item_id"]),
        "match_method": line["match_method"],
        "match_score": _money(line["match_score"]),
        "matched_uom": line["matched_uom"],
        "uom_mismatch": line["uom_mismatch"],
        "match_candidates": line["match_candidates"] or [],
        "provenance": line["field_provenance"] or {},
    }


def _warning_payload(w) -> dict[str, Any]:
    # Section 7.16.5: "UI, email, API responses, and intake auto-replies all
    # render from the catalog." The UI was being sent a bare code and a
    # key/value payload, so a reviewer read "VAL-002" and a row of numbers
    # and had to infer the rest. The prose belongs to the catalog, so the
    # catalog is what ships with the warning.
    entry = get_error(w["code"])
    return {
        "id": str(w["id"]),
        "code": w["code"],
        "title": entry.title,
        "message": entry.message,
        "action": entry.action,
        "severity": w["severity"],
        "field_name": w["field_name"],
        "line_number": w["line_number"],
        "document_line_id": _uuid(w["document_line_id"]),
        "detail": w["detail"] or {},
        "status": w["status"],
        "acknowledged_at": w["acknowledged_at"].isoformat() if w["acknowledged_at"] else None,
    }


def _trail_payload(row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "action": row["action"],
        "user_id": str(row["user_id"]),
        # Section 7.15.1: the tenant surface labels these "DocFlow support".
        "by_docflow_support": row["acting_as_tenant_id"] is not None,
        "changes": row["changes"] or [],
        "warning_acknowledgements": row["warning_acknowledgements"] or [],
        "note": row["note"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


# ── Editing, approving, rejecting ───────────────────────────────────────────


@router.patch("/documents/{document_id}")
def edit_document(
    document_id: UUID,
    body: EditBody,
    identity: AuthenticatedIdentity = Depends(get_current_identity),
) -> dict:
    tenant_id = require_reviewer(identity)
    request = EditRequest(
        header=body.header,
        lines={edit.line_id: edit.fields for edit in body.lines},
    )

    with tenant_session(tenant_id) as session:
        try:
            action_id = apply_edits(
                session,
                tenant_id,
                document_id,
                user_id=_user_id(identity),
                request=request,
                expected_version=body.expected_version,
            )
        except ReviewError as exc:
            raise _review_error(exc) from exc
        version = document_version(session, document_id)

    return {"review_action_id": str(action_id) if action_id else None, "version": version}


@router.post("/documents/{document_id}/approve")
def approve(
    document_id: UUID,
    body: ApproveBody,
    identity: AuthenticatedIdentity = Depends(get_current_identity),
) -> dict:
    tenant_id = require_reviewer(identity)
    acknowledgements = [
        WarningAcknowledgement(
            warning_id=a.warning_id, code=a.code, text=a.text, note=a.note
        )
        for a in body.acknowledgements
    ]

    with tenant_session(tenant_id) as session:
        try:
            action_id = approve_document(
                session,
                tenant_id,
                document_id,
                user_id=_user_id(identity),
                acknowledgements=acknowledgements,
            )
        except ReviewError as exc:
            raise _review_error(exc) from exc

    return {"review_action_id": str(action_id), "status": "approved"}


@router.post("/documents/{document_id}/reject")
def reject(
    document_id: UUID,
    body: RejectBody,
    identity: AuthenticatedIdentity = Depends(get_current_identity),
) -> dict:
    tenant_id = require_reviewer(identity)
    with tenant_session(tenant_id) as session:
        try:
            action_id = reject_document(
                session,
                tenant_id,
                document_id,
                user_id=_user_id(identity),
                note=body.note,
            )
        except ReviewError as exc:
            raise _review_error(exc) from exc

    return {"review_action_id": str(action_id), "status": "rejected"}


@router.get("/documents/{document_id}/warnings/open")
def open_document_warnings(
    document_id: UUID,
    identity: AuthenticatedIdentity = Depends(get_current_identity),
) -> dict:
    """What approval would currently refuse on. The UI asks before offering
    the approve button, so a reviewer sees the list rather than a rejection."""
    tenant_id = require_tenant_member(identity)
    with tenant_session(tenant_id) as session:
        warnings = unacknowledged_warnings(session, document_id)
    return {
        "warnings": [
            {
                "id": str(w["id"]),
                "code": w["code"],
                "severity": w["severity"],
                "field_name": w["field_name"],
                "line_number": w["line_number"],
                "detail": w["detail"] or {},
            }
            for w in warnings
        ]
    }


# ── Catalog search and the learning step ────────────────────────────────────


@router.get("/items")
def search_items(
    identity: AuthenticatedIdentity = Depends(get_current_identity),
    q: str = Query(min_length=1, max_length=120),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    """
    SKU search for the "this line means that item" control.

    `q` is a bind parameter, never interpolated, and it only ever reaches an
    ILIKE against this tenant's own catalog.
    """
    tenant_id = require_tenant_member(identity)
    with tenant_session(tenant_id) as session:
        rows = session.execute(
            text(
                """
                SELECT id, sku, description, unit_of_measure
                FROM items
                -- `retired_at` arrives with catalog imports in Phase 5
                -- (Section 9); until then a retired SKU is a soft-deleted
                -- one and `deleted_at` is the whole filter.
                WHERE deleted_at IS NULL
                  AND (sku ILIKE :pattern OR description ILIKE :pattern)
                ORDER BY
                  CASE WHEN upper(sku) = upper(:exact) THEN 0 ELSE 1 END,
                  sku
                LIMIT :limit
                """
            ),
            {"pattern": f"%{q}%", "exact": q, "limit": limit},
        ).mappings().all()

    return {
        "items": [
            {
                "id": str(row["id"]),
                "sku": row["sku"],
                "description": row["description"],
                "unit_of_measure": row["unit_of_measure"],
            }
            for row in rows
        ]
    }


@router.post("/documents/{document_id}/mapping")
def create_mapping(
    document_id: UUID,
    body: MappingBody,
    identity: AuthenticatedIdentity = Depends(get_current_identity),
) -> dict:
    """
    "This line means that catalog item" -- the Section 7.6 learning step.

    Calls the same `confirm_sku_mapping` the Console will, so a learned rule
    has exactly one way of coming into existence (Section 10: "no rule
    activates without a human confirmation").
    """
    tenant_id = require_reviewer(identity)
    with tenant_session(tenant_id) as session:
        owns_line = session.execute(
            text(
                "SELECT 1 FROM document_lines "
                "WHERE id = :line_id AND document_id = :document_id AND deleted_at IS NULL"
            ),
            {"line_id": str(body.line_id), "document_id": str(document_id)},
        ).first()
        if not owns_line:
            raise HTTPException(status_code=404)

        rule_id = confirm_sku_mapping(
            session,
            tenant_id,
            document_line_id=body.line_id,
            item_id=body.item_id,
            confirmed_by=_user_id(identity),
            tenant_wide=body.tenant_wide,
        )

    return {"learned_rule_id": str(rule_id) if rule_id else None}


# ── The original document, behind a short-lived signed URL ──────────────────


@router.get("/documents/{document_id}/original")
def original_document_url(
    document_id: UUID,
    identity: AuthenticatedIdentity = Depends(get_current_identity),
) -> dict:
    """
    Mint a short-lived URL for the viewer.

    The storage path is never returned -- the client gets an opaque token and
    the serving route resolves the path itself (Section 7.4: "storage paths
    are never user-controlled or user-visible").
    """
    tenant_id = require_tenant_member(identity)
    with tenant_session(tenant_id) as session:
        row = session.execute(
            text(
                "SELECT storage_path, original_filename, preview_storage_path, preview_kind "
                "FROM documents WHERE id = :id AND deleted_at IS NULL"
            ),
            {"id": str(document_id)},
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404)

    # A stored preview means the worker (or a seeding script) already turned
    # this into something a browser shows. The API never decodes a document
    # itself -- Section 7.11 keeps parsing out of the web process -- so all
    # it does here is notice that a viewable rendering exists.
    has_preview = bool(row["preview_storage_path"])
    detected = None
    if not has_preview:
        try:
            detected = file_types.detect_file_type(
                read_file(row["storage_path"]), _extension(row["original_filename"])
            )
        except OSError:
            # The stored object is gone or unreadable. That is a storage
            # problem, not a reason to fail the whole review screen -- the
            # extracted values are what the reviewer mainly needs, and the
            # viewer says plainly that the original cannot be shown.
            detected = None

    previewable = has_preview or (detected is not None and detected.name not in _NOT_PREVIEWABLE)

    token, expires_at = mint_document_token(document_id, tenant_id)
    return {
        "url": f"/review/documents/{document_id}/original/content?token={token}",
        "expires_at": expires_at,
        "previewable": previewable,
        "format": (
            _FORMAT_NAMES.get(detected.name)
            if detected
            # With a stored preview the bytes are never examined here, so the
            # format is named from the extension. That is string handling,
            # not parsing -- nothing decodes the file (Section 7.11).
            else _FORMAT_BY_EXTENSION.get(_extension(row["original_filename"]))
        ),
        "filename": row["original_filename"],
        # Told honestly: a converted image is the page as it was sent;
        # extracted text is DocFlow's rendering, not the original layout.
        "preview_kind": row["preview_kind"],
    }


# Naming a format from its extension, for the one message that needs a word
# for it. Never used to decide how anything is handled -- Section 7.11 is
# emphatic that the extension is not trusted for that ("magic-byte type
# detection (never trust the extension or the Content-Type header)").
_FORMAT_BY_EXTENSION = {
    ".tif": "a TIFF scan",
    ".tiff": "a TIFF scan",
    ".docx": "a Word document",
    ".doc": "a Word document",
    ".xlsx": "an Excel spreadsheet",
    ".xls": "an Excel spreadsheet",
    ".eml": "an email",
    ".msg": "an Outlook message",
    ".rtf": "a rich-text document",
    ".odt": "an OpenDocument file",
    ".ods": "an OpenDocument spreadsheet",
    ".heic": "an iPhone photo",
}


def _extension(filename: str | None) -> str:
    if not filename or "." not in filename:
        return ""
    return filename[filename.rindex(".") :].lower()


@router.get("/documents/{document_id}/original/content")
def original_document_content(
    document_id: UUID,
    token: str = Query(min_length=1),
) -> Response:
    """
    Serve the original file for the sandboxed viewer.

    **Authenticated by the signed token alone, deliberately.** The viewer is
    an `<iframe>`, and an iframe's request is a plain browser GET: it carries
    no `Authorization` header, so a route that also required the session
    answered 401 to the only client that ever calls it, and the reviewer saw
    an empty panel where the document should be (DECISIONS.md D-089).

    What the token proves is what matters: it was minted by this server, for
    this document, for one tenant, within the last few minutes. Minting it
    required a session that had already passed `require_tenant_member`. The
    tenant then comes out of the token and opens an ordinary tenant-scoped
    session, so RLS still decides what can be read.
    """
    try:
        verified = verify_document_token(token, document_id)
    except InvalidSignedUrl as exc:
        raise HTTPException(status_code=404) from exc
    tenant_id = verified.tenant_id

    with tenant_session(tenant_id) as session:
        row = session.execute(
            text(
                "SELECT storage_path, original_filename, preview_storage_path, "
                "preview_media_type FROM documents WHERE id = :id AND deleted_at IS NULL"
            ),
            {"id": str(document_id)},
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404)

    # A stored preview is served as it was produced, under the media type
    # recorded with it. 0009 constrains that column to a short allowlist, so
    # this cannot become a way to serve document-derived markup. Producing it
    # required decoding the document, which happened in the worker -- never
    # here (Section 7.11).
    if row["preview_storage_path"] and row["preview_media_type"]:
        return Response(
            content=read_file(row["preview_storage_path"]),
            media_type=row["preview_media_type"],
            headers=_viewer_headers(),
        )

    try:
        content = read_file(row["storage_path"])
    except OSError as exc:
        # Same reasoning as the mint route: a missing storage object is a 404
        # for this one file, never a 500 for the review screen.
        raise HTTPException(status_code=404) from exc

    return Response(
        content=content,
        media_type=_viewable_media_type(content),
        headers=_viewer_headers(),
    )


# What the browser is allowed to render in the viewer, keyed by the type
# detected from the file's own bytes -- never from its extension and never
# from anything the sender claimed (Section 7.11: "magic-byte type detection
# (never trust the extension or the Content-Type header)").
#
# The allowlist is what makes this safe. `text/html` and `image/svg+xml` are
# deliberately absent: both can carry script, and a document that renders as
# HTML inside the viewer would be exactly the injection surface Section 7.12
# exists to close. Anything not listed is served as an opaque stream, which
# a browser will not execute.
_VIEWABLE_MEDIA_TYPES: dict[file_types.FileTypeName, str] = {
    file_types.FileTypeName.PDF: "application/pdf",
    file_types.FileTypeName.PNG: "image/png",
    file_types.FileTypeName.JPEG: "image/jpeg",
    file_types.FileTypeName.GIF: "image/gif",
    file_types.FileTypeName.WEBP: "image/webp",
    file_types.FileTypeName.TXT: "text/plain; charset=utf-8",
    file_types.FileTypeName.CSV: "text/plain; charset=utf-8",
    file_types.FileTypeName.MD: "text/plain; charset=utf-8",
}

# Formats a browser will not display, however correctly they are served.
# TIFF is the surprise on this list: it is a first-class intake format --
# fax and scanner output, which this industry still runs on -- and no major
# browser renders it. A Word or Excel file is the same story. Serving these
# into the iframe produces a blank panel that looks like a bug, so the
# viewer is told up front that there is nothing to show and offers the file
# instead (DECISIONS.md D-091).
_NOT_PREVIEWABLE = {
    file_types.FileTypeName.TIFF,
    file_types.FileTypeName.DOCX,
    file_types.FileTypeName.XLSX,
    file_types.FileTypeName.DOC,
    file_types.FileTypeName.XLS,
    file_types.FileTypeName.ODT,
    file_types.FileTypeName.ODS,
    file_types.FileTypeName.EML,
    file_types.FileTypeName.MSG,
    file_types.FileTypeName.RTF,
    file_types.FileTypeName.HEIC,
    # HTML is excluded on purpose and permanently: rendering document-derived
    # markup is precisely what Section 7.12 forbids.
    file_types.FileTypeName.HTML,
}

# What a person calls the format, for the "we can't show this one" message.
_FORMAT_NAMES: dict[file_types.FileTypeName, str] = {
    file_types.FileTypeName.TIFF: "a TIFF scan",
    file_types.FileTypeName.DOCX: "a Word document",
    file_types.FileTypeName.DOC: "a Word document",
    file_types.FileTypeName.XLSX: "an Excel spreadsheet",
    file_types.FileTypeName.XLS: "an Excel spreadsheet",
    file_types.FileTypeName.ODT: "an OpenDocument file",
    file_types.FileTypeName.ODS: "an OpenDocument spreadsheet",
    file_types.FileTypeName.EML: "an email",
    file_types.FileTypeName.MSG: "an Outlook message",
    file_types.FileTypeName.RTF: "a rich-text document",
    file_types.FileTypeName.HEIC: "an iPhone photo",
    file_types.FileTypeName.HTML: "a web page",
}


def _viewable_media_type(content: bytes) -> str:
    """
    The media type to serve the original under.

    An earlier version served everything as `application/octet-stream`, on
    the reasoning that an opaque stream cannot be rendered as anything
    active. That was true and useless: the browser rendered nothing at all,
    so the review screen's left-hand panel was blank for every document and
    the side-by-side comparison the whole phase is built around did not
    happen (DECISIONS.md D-089).
    """
    detected = file_types.detect_file_type(content)
    if detected is None:
        return "application/octet-stream"
    return _VIEWABLE_MEDIA_TYPES.get(detected.name, "application/octet-stream")


def _user_id(identity: AuthenticatedIdentity) -> UUID:
    if identity.local_user_id is None:
        # Section 7.3: no review action is ever anonymous. An authenticated
        # identity with no local user row cannot act on a document.
        raise HTTPException(status_code=403, detail="This account cannot act on documents.")
    return identity.local_user_id
