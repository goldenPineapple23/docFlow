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
from uuid import UUID, uuid4

from docflow_core import file_types
from docflow_core.db import tenant_session
from docflow_core.duplicates import find_content_duplicate_at_ingest
from docflow_core.errors import get_error
from docflow_core.storage import save_file
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import text

from app.celery_client import celery_client
from app.deps import AuthenticatedIdentity, get_current_identity

router = APIRouter(prefix="/documents", tags=["documents"])


def _require_tenant(identity: AuthenticatedIdentity) -> None:
    if identity.tenant_id is None:
        # A platform-admin-only account (D-004) has no tenant to upload
        # into; the Console's own staging upload is a separate, later
        # feature (Section 7.15.2 Step 1/6), not this endpoint.
        raise HTTPException(status_code=403, detail="This account is not associated with a tenant.")


@router.post("/upload")
async def upload_document(
    file: UploadFile,
    identity: AuthenticatedIdentity = Depends(get_current_identity),
) -> dict:
    _require_tenant(identity)
    content = await file.read()
    return ingest_upload(identity.tenant_id, file.filename or "upload", content)


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

    with tenant_session(tenant_id) as session:
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

    if not is_test_batch:
        # Enqueued after the transaction commits, so the task never races a
        # document row that isn't visible yet. Interactive priority (Section
        # 5.1): a single upload a user is waiting on, never the bulk queue.
        celery_client.send_task(
            "docflow.parse_and_extract",
            args=[str(tenant_id), str(document_id)],
            queue="interactive",
        )

    response = {"document_id": str(document_id), "status": status}
    if existing is not None:
        response["possible_duplicate_of"] = str(existing)
    return response
