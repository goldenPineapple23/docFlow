"""
There is no /auth/signup or /auth/login route in this API by design
(CLAUDE.md Section 3: "No public signup"). Sign-in itself happens
client-side, directly against Supabase Auth, for invited users only --
public signup is disabled at the Supabase project level (SETUP.md).

This router only exposes what the frontend needs after that: resolving the
current session to a local identity (tenant, role, platform-admin status).
"""

from __future__ import annotations

from docflow_core import team
from docflow_core.db import tenant_session
from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.deps import AuthenticatedIdentity, get_current_identity
from app.errors import catalog_detail

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
def get_me(identity: AuthenticatedIdentity = Depends(get_current_identity)) -> dict:
    # The company name shown in the customer's header. Read through the tenant's
    # own session from the identity's tenant -- never from anything the client
    # sent (Section 7.5) -- so it can only ever be the caller's own company.
    tenant_name = None
    if identity.tenant_id is not None:
        with tenant_session(identity.tenant_id) as session:
            tenant_name = session.execute(
                text("SELECT name FROM tenants WHERE id = :id"), {"id": str(identity.tenant_id)}
            ).scalar_one_or_none()
            # The Team page's "Signed in" (D-132): every signed-in screen calls
            # this, so the first visit is the moment an invite was accepted.
            if identity.local_user_id is not None:
                team.mark_signed_in(session, identity.local_user_id)
    return {
        "email": identity.email,
        "tenant_id": str(identity.tenant_id) if identity.tenant_id else None,
        "tenant_name": tenant_name.strip() if tenant_name else None,
        "role": identity.role,
        "is_platform_admin": identity.is_platform_admin,
        # So the page sign-in lands on can say why a removed person sees
        # nothing, in the catalog's words rather than its own (AUTH-004).
        "access_removed": identity.access_removed,
        "refusal": catalog_detail("AUTH-004") if identity.access_removed else None,
    }
