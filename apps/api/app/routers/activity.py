"""
The account's activity trail (Section 6's Phase 5 line: "audit log view";
`docflow-mvp-features.docx`: "Audit trail -- who changed what, when"). Slice
5.8b, D-130.

Visible to reviewers as well as admins (the founder's decision): the people
doing the work are the ones who need to see what a colleague already did, and
an audit trail only the boss can read is a weaker check, not a stronger one.
It is a read of the tenant's own rows, so it carries `require_reviewer`'s role
set rather than the admin's -- a `viewer`, if one is ever created, does not get
it.

Every row comes from `tenant_home.activity_page`, the same union the dashboard's
short list reads, so the page and the dashboard cannot disagree. Nothing here
carries an extracted value: an edit says how many fields changed, never what
they became (Section 7.10).
"""

from __future__ import annotations

from docflow_core import tenant_home
from docflow_core.db import tenant_session
from fastapi import APIRouter, Depends, Query

from app.deps import AuthenticatedIdentity, get_current_identity, require_reviewer

router = APIRouter(tags=["activity"])

# One screenful. Bounded so a request cannot ask for the whole history at once.
_MAX_LIMIT = 200


@router.get("/activity")
def get_activity(
    identity: AuthenticatedIdentity = Depends(get_current_identity),
    kind: list[str] = Query(default=[]),
    limit: int = Query(default=50, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> dict:
    tenant_id = require_reviewer(identity)
    # An unknown kind is dropped rather than refused: a stale bookmark should
    # show the whole list, not an error.
    kinds = tuple(k for k in kind if k in tenant_home.ACTIVITY_KINDS)
    with tenant_session(tenant_id) as session:
        page = tenant_home.activity_page(session, tenant_id, kinds=kinds, limit=limit, offset=offset)

    return {
        "items": [
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
            for item in page["items"]
        ],
        "total": page["total"],
        "limit": page["limit"],
        "offset": page["offset"],
        "kinds": list(tenant_home.ACTIVITY_KINDS),
    }
