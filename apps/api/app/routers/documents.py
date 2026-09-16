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
from uuid import uuid4

from docflow_core import file_types
from docflow_core.db import tenant_session
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
    tenant_id = identity.tenant_id

    content = await file.read()
    original_filename = file.filename or "upload"

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

    with tenant_session(tenant_id) as session:
        existing = session.execute(
            text(
                "SELECT id FROM documents WHERE tenant_id = :tenant_id AND content_sha256 = :sha256 "
                "ORDER BY created_at ASC LIMIT 1"
            ),
            {"tenant_id": str(tenant_id), "sha256": content_sha256},
        ).mappings().first()

        storage_path = save_file(tenant_id, original_filename, content)
        document_id = uuid4()

        session.execute(
            text(
                """
                INSERT INTO documents
                    (id, tenant_id, original_filename, storage_path,
                     source, status, content_sha256, created_at)
                VALUES
                    (:id, :tenant_id, :original_filename, :storage_path,
                     'upload', 'pending', :content_sha256, now())
                """
            ),
            {
                "id": str(document_id),
                "tenant_id": str(tenant_id),
                "original_filename": original_filename,
                "storage_path": storage_path,
                "content_sha256": content_sha256,
            },
        )

    # Enqueued after the transaction commits, so the task never races a
    # document row that isn't visible yet. Interactive priority (Section
    # 5.1): a single upload a user is waiting on, never the bulk queue.
    celery_client.send_task(
        "docflow.parse_and_extract",
        args=[str(tenant_id), str(document_id)],
        queue="interactive",
    )

    response = {"document_id": str(document_id), "status": "pending"}
    if existing is not None:
        # CLAUDE.md Section 7.8: same content isn't silently re-ingested --
        # surfaced here for now; full duplicate-linking (a stored
        # relationship, not just a response field) is a Phase 2 concern --
        # see DECISIONS.md.
        response["possible_duplicate_of"] = str(existing["id"])
    return response
