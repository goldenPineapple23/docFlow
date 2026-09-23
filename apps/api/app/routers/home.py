"""
The customer's own dashboard (`docflow-mvp-features.docx`: "Simple dashboard --
documents by status, recent activity"). Slice 5.8a, D-128.

Admin-only (`require_tenant_admin`): a reviewer's work is the queue, and the
account's shape is the admin's. Everything is the tenant's own data through the
tenant-scoped layer, with `tenant_id` from the session and never the request
(Section 7.5).

The held count and its plain-English reason come from the same modules the
customer's Held page uses (7.16.4), and the allowance from the same one the
banner uses (7.16.1), so a number cannot mean one thing here and another there.
"""

from __future__ import annotations

from docflow_core import allowance as allowance_module
from docflow_core import quarantine, tenant_home, usage
from docflow_core.db import tenant_session
from fastapi import APIRouter, Depends

from app.deps import AuthenticatedIdentity, get_current_identity, require_tenant_admin

router = APIRouter(tags=["home"])


@router.get("/home")
def get_home(identity: AuthenticatedIdentity = Depends(get_current_identity)) -> dict:
    tenant_id = require_tenant_admin(identity)
    with tenant_session(tenant_id) as session:
        statuses = tenant_home.documents_by_status(session, tenant_id)
        waiting = tenant_home.waiting(session, tenant_id)
        month = tenant_home.this_month(session, tenant_id)
        activity = tenant_home.recent_activity(session, tenant_id)
        a = usage.allowance_for(session, tenant_id)
        banner = allowance_module.current_banner(session, tenant_id)
        held = quarantine.held_summary(session, tenant_id, role=identity.role)

    return {
        "documents_by_status": statuses,
        "oldest_needs_review_at": (
            waiting["oldest_needs_review_at"].isoformat()
            if waiting["oldest_needs_review_at"]
            else None
        ),
        "received_today": waiting["received_today"],
        "this_month": month,
        "allowance": {
            "used": a.used,
            "allowance": a.allowance,
            "tier": a.tier_name,
            "banner": (
                {
                    "threshold_pct": banner.threshold_pct,
                    "code": banner.entry.code,
                    "title": banner.entry.title,
                    "message": banner.entry.message,
                    "action": banner.entry.action,
                }
                if banner
                else None
            ),
        },
        "held": {
            "total": sum(g.count for g in held),
            "groups": [
                {"reason": g.reason, "count": g.count, "title": g.entry.title, "message": g.entry.message}
                for g in held
            ],
        },
        "activity": [
            {
                "at": item.at.isoformat(),
                "kind": item.kind,
                "document_id": str(item.document_id) if item.document_id else None,
                "document_name": item.document_name,
                "po_number": item.po_number,
                "by": item.by,
                "by_docflow_support": item.by_docflow_support,
                "detail": item.detail,
            }
            for item in activity
        ],
    }
