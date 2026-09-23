"""
The tenant's view of allowances and held documents (CLAUDE.md Section 7.16.1,
7.16.3, 7.16.4; D-126). Everything here is the tenant's own data through the
tenant-scoped layer: `tenant_id` comes from the authenticated identity, never
from the request (Section 7.5).

  GET  /allowance       this month's use against the tier, and the 80% / 100%
                        banner (catalog wording) when one applies
  GET  /held            documents held for review, grouped by reason in plain
                        English, and whether this person may release each group
  POST /held/release    release held documents (owner / admin, and only the
                        reasons that are the tenant's own call)
  GET  /ignored-mail    mail that produced no document, with why

Who may release what is decided in docflow_core.quarantine, not here.
"""

from __future__ import annotations

from uuid import UUID

from docflow_core import allowance, quarantine, usage
from docflow_core.db import tenant_session
from docflow_core.errors import get_error
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.celery_client import celery_client
from app.deps import AuthenticatedIdentity, get_current_identity, require_tenant_member
from app.errors import catalog_error

router = APIRouter(tags=["held"])

# A big release goes to the bulk queue so it can't crowd interactive work.
_INTERACTIVE_RELEASE_LIMIT = 10


def _entry(entry) -> dict[str, str]:
    return {"code": entry.code, "title": entry.title, "message": entry.message, "action": entry.action}


@router.get("/allowance")
def get_allowance(identity: AuthenticatedIdentity = Depends(get_current_identity)) -> dict:
    tenant_id = require_tenant_member(identity)
    with tenant_session(tenant_id) as session:
        a = usage.allowance_for(session, tenant_id)
        banner = allowance.current_banner(session, tenant_id)
    return {
        "used": a.used,
        "allowance": a.allowance,
        "tier": a.tier_name,
        "month": a.month,
        "banner": ({"threshold_pct": banner.threshold_pct, **_entry(banner.entry)} if banner else None),
    }


@router.get("/held")
def get_held(identity: AuthenticatedIdentity = Depends(get_current_identity)) -> dict:
    tenant_id = require_tenant_member(identity)
    with tenant_session(tenant_id) as session:
        groups = quarantine.held_summary(session, tenant_id, role=identity.role)
        rows = quarantine.list_held(session, tenant_id)
    return {
        "total": sum(g.count for g in groups),
        "groups": [
            {
                "reason": g.reason,
                "count": g.count,
                "can_release": g.can_release,
                **_entry(g.entry),
            }
            for g in groups
        ],
        "documents": [
            {
                "id": str(r["id"]),
                "filename": r["original_filename"],
                "sender_email": r["sender_email"],
                "reason": r["quarantine_reason"],
                "received_at": r["created_at"].isoformat(),
                "can_release": quarantine.can_release(identity.role, r["quarantine_reason"]),
            }
            for r in rows
        ],
    }


class ReleaseBody(BaseModel):
    document_ids: list[UUID] = Field(min_length=1, max_length=500)


@router.post("/held/release")
def release_held(body: ReleaseBody, identity: AuthenticatedIdentity = Depends(get_current_identity)) -> dict:
    tenant_id = require_tenant_member(identity)
    if identity.role not in quarantine.TENANT_RELEASING_ROLES or identity.local_user_id is None:
        # A reviewer or viewer: not theirs to decide. Refused at the API, not
        # merely hidden in the UI (Section 3).
        raise catalog_error("AUTH-002", status_code=403, extra={"role": identity.role})
    with tenant_session(tenant_id) as session:
        try:
            result = quarantine.release(
                session,
                tenant_id,
                body.document_ids,
                role=identity.role,
                actor_user_id=identity.local_user_id,
            )
        except quarantine.QuarantineError as exc:
            raise catalog_error(exc.code, status_code=403 if exc.code == "QUA-001" else 409) from exc
    # Enqueued only after the transaction commits, oldest first: received order.
    queue = "interactive" if len(result.released) <= _INTERACTIVE_RELEASE_LIMIT else "bulk"
    for document_id in result.released:
        celery_client.send_task(
            "docflow.parse_and_extract", args=[str(tenant_id), str(document_id)], queue=queue
        )
    return {
        "released": [str(i) for i in result.released],
        "skipped": [str(i) for i in result.skipped],
    }


@router.get("/ignored-mail")
def get_ignored_mail(identity: AuthenticatedIdentity = Depends(get_current_identity)) -> dict:
    """Mail that made no document, so nothing is silently lost (7.16.3). The
    reason is the catalog entry's own words."""
    tenant_id = require_tenant_member(identity)
    with tenant_session(tenant_id) as session:
        rows = (
            session.execute(
                text(
                    """
                SELECT id, sender_email, subject, original_filename, error_code, created_at
                FROM intake_rejections
                WHERE tenant_id = :t AND source = 'email'
                ORDER BY created_at DESC LIMIT 100
                """
                ),
                {"t": str(tenant_id)},
            )
            .mappings()
            .all()
        )
    out = []
    for r in rows:
        try:
            entry = _entry(get_error(r["error_code"]))
        except KeyError:
            entry = {"code": r["error_code"], "title": "Not processed", "message": "", "action": ""}
        out.append(
            {
                "id": str(r["id"]),
                "sender_email": r["sender_email"],
                "subject": r["subject"],
                "filename": r["original_filename"],
                "received_at": r["created_at"].isoformat(),
                **entry,
            }
        )
    return {"mail": out}
