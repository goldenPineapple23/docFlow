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
import logging
from typing import Any
from uuid import UUID, uuid4

from docflow_core import allowance, file_types, founder_alerts, intake_gate, quarantine
from docflow_core.constants import INTERACTIVE_BATCH_MAX
from docflow_core.db import tenant_session
from docflow_core.duplicates import find_content_duplicate_at_ingest
from docflow_core.errors import get_error
from docflow_core.storage import StorageUnavailableError, save_file
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy import text

from app.celery_client import nudge_dispatcher
from app.deps import AuthenticatedIdentity, get_current_identity, require_reviewer
from app.errors import catalog_error

logger = logging.getLogger("docflow.api")

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
    # Stage 3d (Q3): how many files the person chose at once; the page sends
    # one request per file. Up to INTERACTIVE_BATCH_MAX is the tenant's
    # interactive lane, more is bulk. The lane only orders this tenant's own
    # documents, so a client that misstates it reorders only its own queue.
    batch_size: int = Form(1, ge=1),
    identity: AuthenticatedIdentity = Depends(get_current_identity),
) -> dict:
    tenant_id = _require_tenant(identity)
    content = await file.read()
    lane = "interactive" if batch_size <= INTERACTIVE_BATCH_MAX else "bulk"
    return ingest_upload(tenant_id, file.filename or "upload", content, lane=lane)


def ingest_upload(
    tenant_id: UUID,
    original_filename: str,
    content: bytes,
    *,
    is_test_batch: bool = False,
    lane: str = "interactive",
) -> dict:
    """
    The one upload path (Section 10: no second upload handler for the
    Console). Validates, stores, records, and -- for a normal upload --
    enqueues. A test-batch file (Section 7.15.2 Step 6) is stored as
    'staged' and NOT enqueued: it waits for the founder's "Run extraction"
    (Step 7, D-112). `tenant_id` comes from the caller's authenticated
    context -- the tenant's own session, or the Console's audited route.
    """
    # 7.14: a suspended or pending-deletion tenant gets a clear error here, not a
    # 404 and not silence (review finding H10). Checked before anything else --
    # before validation, before the file is stored, before the hash -- because a
    # cancelled account's upload should cost nothing at all, and because the
    # answer does not depend on the file.
    #
    # Read and export are untouched: 7.14 keeps those through the whole export
    # window on purpose.
    #
    # **The attempt is recorded, the file is not** (founder, 2026-09-27; D-172).
    # Without this the refusal left no trace anywhere -- the user saw INT-010 and
    # nothing was retained, so nobody could tell a customer who tried once from
    # one who tried forty times. `intake_rejections` is already the record of
    # "something arrived and we didn't process it", already carries
    # `source = 'upload'`, and is already visible to the tenant, so this needs no
    # new table and no migration. The file's bytes are still never stored and no
    # model call is ever made: what is kept is that an attempt happened, when,
    # and under what name -- a retention signal, since somebody still trying to
    # upload is somebody who wants their account back (7.14 calls reactivation a
    # retention feature).
    with tenant_session(tenant_id) as session:
        blocked = intake_gate.lifecycle_block(session, tenant_id)
        if blocked is not None:
            session.execute(
                text(
                    """
                    INSERT INTO intake_rejections
                        (id, tenant_id, source, original_filename, detected_type, error_code, created_at)
                    VALUES
                        (:id, :tenant_id, 'upload', :original_filename, NULL, :error_code, now())
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    # Metadata only, never a path or a shell argument (7.11).
                    "original_filename": original_filename,
                    "error_code": blocked,
                },
            )
    if blocked is not None:
        # IDs and codes only -- never the filename, never content (7.10).
        logger.info("upload_refused_lifecycle tenant=%s code=%s", tenant_id, blocked)
        error = get_error(blocked)
        raise HTTPException(
            status_code=403,
            detail={
                "code": error.code,
                "title": error.title,
                "message": error.message,
                "action": error.action,
            },
        )

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
            # DOC-015 tells the sender DocFlow has been alerted (D-145).
            founder_alerts.raise_for_failure(
                session, tenant_id=tenant_id, error_code=validation.error_code
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

    # The file is written inside the transaction, so a Storage failure rolls
    # everything back: nothing was received, nothing is processed (Stage 3b,
    # Q3). The reader gets DOC-025 and the founder one alert an hour.
    try:
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
                         duplicate_of_document_id, dispatch_lane, created_at)
                    VALUES
                        (:id, :tenant_id, :original_filename, :storage_path,
                         'upload', :status, :content_sha256, :is_test_batch, :is_possible_duplicate,
                         :duplicate_of_document_id, :dispatch_lane, now())
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
                    "dispatch_lane": lane,
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
    except StorageUnavailableError as exc:
        founder_alerts.alert_storage_unavailable(tenant_id, where="upload")
        raise catalog_error("DOC-025", status_code=503) from exc

    if not is_test_batch and not hold:
        # Stage 3d: the document waits as `pending` in its lane, and only the
        # dispatcher puts it on the queue, taking turns between tenants. The
        # nudge goes after the transaction commits, so the pass sees the row.
        nudge_dispatcher()

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
