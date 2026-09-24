"""
Document upload (Phase 1 slice of CLAUDE.md Section 7.11 / 7.5). This router
only validates bytes and enqueues -- all real parsing/extraction happens in
the isolated apps/worker process (Section 7.11: "parsing never runs in the
web process"), via app/celery_client.py.

tenant_id is never read from the request; it comes only from the
authenticated identity (Section 7.5 / Section 10).
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID, uuid4

from docflow_core import allowance, file_types, intake_gate, quarantine
from docflow_core.db import tenant_session
from docflow_core.duplicates import find_content_duplicate_at_ingest
from docflow_core.errors import get_error
from docflow_core.storage import save_file
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import text

from app.celery_client import celery_client
from app.deps import AuthenticatedIdentity, get_current_identity, require_reviewer

router = APIRouter(prefix="/documents", tags=["documents"])


def _require_tenant(identity: AuthenticatedIdentity) -> UUID:
    """
    The tenant this upload belongs to, or a refusal.

    Uploading is changing the account's data, so it takes the same role as
    reviewing (`require_reviewer`: owner, admin, reviewer) -- a viewer reads
    (D-128). Until this slice the endpoint checked only that the caller had a
    tenant at all, so a viewer could add documents; no screen offered it, but
    the API allowed it.

    A platform-admin-only account (D-004) has no tenant to upload into; the
    Console's own staging upload is a separate path (Section 7.15.2), not this.
    """
    # `require_reviewer` refuses an account with no tenant, or a removed
    # person (AUTH-004), before it checks the role.
    return require_reviewer(identity)


@router.post("/upload")
async def upload_document(
    file: UploadFile,
    identity: AuthenticatedIdentity = Depends(get_current_identity),
) -> dict:
    tenant_id = _require_tenant(identity)
    content = await file.read()
    return ingest_upload(tenant_id, file.filename or "upload", content)


def ingest_upload(
    tenant_id: UUID,
    original_filename: str,
    content: bytes,
    *,
    is_test_batch: bool = False,
) -> dict:
    """
    The one upload path (Section 10: no second upload handler for the
    Console). Validates, stores, records, and -- for a normal upload --
    enqueues. A test-batch file (Section 7.15.2 Step 6) is stored as
    'staged' and NOT enqueued: it waits for the founder's "Run extraction"
    (Step 7, D-112). `tenant_id` comes from the caller's authenticated
    context -- the tenant's own session, or the Console's audited route.
    """
    validation = file_types.validate_upload(content, original_filename)
    if not validation.ok:
        with tenant_session(tenant_id) as session:
            session.execute(
                text(
                    """
                    INSERT INTO intake_rejections
                        (id, tenant_id, source, original_filename, detected_type, error_code, created_at)
                    VALUES
                        (:id, :tenant_id, 'upload', :original_filename, :detected_type, :error_code, now())
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "original_filename": original_filename,
                    "detected_type": validation.file_type.name.value if validation.file_type else None,
                    "error_code": validation.error_code,
                },
            )
        error = get_error(validation.error_code)
        raise HTTPException(
            status_code=422,
            detail={
                "code": error.code,
                "title": error.title,
                "message": error.message,
                "action": error.action,
            },
        )

    content_sha256 = hashlib.sha256(content).hexdigest()
    status = "staged" if is_test_batch else "pending"
    hold: str | None = None

    with tenant_session(tenant_id) as session:
        # The same abuse ceilings email intake applies (Section 7.16.2, D-126):
        # one rule for every way a document can arrive. A held document is
        # stored, never sent to the model, and the founder is alerted. The
        # setup test batch is exempt (Section 7.15.2).
        if not is_test_batch:
            hold = intake_gate.hold_reason(session, tenant_id)
            if hold:
                status = "quarantined"
        # CLAUDE.md Section 7.8: the same content for the same tenant "is
        # linked to the existing document and surfaced as a possible
        # duplicate". The link is written into the INSERT below rather than
        # returned and forgotten -- see DECISIONS.md D-076, which closes D-020.
        # The upload is never rejected: both documents exist and both process.
        existing = find_content_duplicate_at_ingest(session, tenant_id, content_sha256)

        storage_path = save_file(tenant_id, original_filename, content)
        document_id = uuid4()

        session.execute(
            text(
                """
                INSERT INTO documents
                    (id, tenant_id, original_filename, storage_path,
                     source, status, content_sha256, is_test_batch, is_possible_duplicate,
                     duplicate_of_document_id, created_at)
                VALUES
                    (:id, :tenant_id, :original_filename, :storage_path,
                     'upload', :status, :content_sha256, :is_test_batch, :is_possible_duplicate,
                     :duplicate_of_document_id, now())
                """
            ),
            {
                "id": str(document_id),
                "tenant_id": str(tenant_id),
                "original_filename": original_filename,
                "storage_path": storage_path,
                "status": status,
                "content_sha256": content_sha256,
                "is_test_batch": is_test_batch,
                "is_possible_duplicate": existing is not None,
                "duplicate_of_document_id": str(existing) if existing else None,
            },
        )
        if hold:
            session.execute(
                text(
                    "UPDATE documents SET quarantine_reason = :reason, quarantined_at = now() "
                    "WHERE id = :id AND tenant_id = :t"
                ),
                {"reason": hold, "id": str(document_id), "t": str(tenant_id)},
            )
        elif not is_test_batch:
            # It counts now, so the 80% / 100% notices may fire (7.16.1).
            allowance.record_thresholds(session, tenant_id)

    if not is_test_batch and not hold:
        # Enqueued after the transaction commits, so the task never races a
        # document row that isn't visible yet. Interactive priority (Section
        # 5.1): a single upload a user is waiting on, never the bulk queue.
        celery_client.send_task(
            "docflow.parse_and_extract",
            args=[str(tenant_id), str(document_id)],
            queue="interactive",
        )

    response: dict[str, Any] = {"document_id": str(document_id), "status": status}
    if hold:
        # What the person sees: the catalog's own wording, never a made-up string.
        entry = quarantine.reason_entry(hold)
        response["held"] = {
            "code": entry.code,
            "title": entry.title,
            "message": entry.message,
            "action": entry.action,
        }
    if existing is not None:
        response["possible_duplicate_of"] = str(existing)
    return response
