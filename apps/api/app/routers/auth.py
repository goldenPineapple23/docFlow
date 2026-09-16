"""
There is no /auth/signup or /auth/login route in this API by design
(CLAUDE.md Section 3: "No public signup"). Sign-in itself happens
client-side, directly against Supabase Auth, for invited users only --
public signup is disabled at the Supabase project level (SETUP.md).

This router only exposes what the frontend needs after that: resolving the
current session to a local identity (tenant, role, platform-admin status).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import AuthenticatedIdentity, get_current_identity

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
def get_me(identity: AuthenticatedIdentity = Depends(get_current_identity)) -> dict:
    return {
        "email": identity.email,
        "tenant_id": str(identity.tenant_id) if identity.tenant_id else None,
        "role": identity.role,
        "is_platform_admin": identity.is_platform_admin,
    }
