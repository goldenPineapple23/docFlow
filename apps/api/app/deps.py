"""
Authentication and authorization dependencies.

Auth model (CLAUDE.md Section 3): password/Google/Microsoft sign-in via
Supabase Auth, for *invited* users only -- there is no signup route. The
frontend authenticates against Supabase directly and sends the resulting
session JWT to this API as a Bearer token. This module verifies that token
and resolves it to our own `users` row (which carries tenant_id and role)
and, separately, checks `platform_admins` for cross-tenant capability.

`tenant_id` is NEVER read from the request here or anywhere else -- it comes
only from the `users` row matched to the verified auth identity (Section 7.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import jwt
from docflow_core.config import get_settings
from docflow_core.db import identity_lookup_session
from fastapi import Header, HTTPException
from sqlalchemy import text


@dataclass(frozen=True)
class AuthenticatedIdentity:
    auth_user_id: str
    email: str
    local_user_id: UUID | None
    tenant_id: UUID | None
    role: str | None
    is_platform_admin: bool


def _decode_bearer_token(authorization: str | None) -> dict | None:
    """Returns the decoded claims, or None if there's no usable token at all."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1]
    settings = get_settings()
    if not settings.supabase_jwt_secret:
        raise RuntimeError(
            "SUPABASE_JWT_SECRET is not set. See SETUP.md Step 1 (Project Settings -> API -> JWT Secret)."
        )
    try:
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.PyJWTError:
        return None


def _resolve_identity(authorization: str | None) -> AuthenticatedIdentity | None:
    claims = _decode_bearer_token(authorization)
    if claims is None:
        return None
    auth_user_id = claims["sub"]
    email = claims.get("email", "")

    with identity_lookup_session(auth_user_id) as session:
        user_row = session.execute(
            text("SELECT id, tenant_id, role FROM users WHERE auth_user_id = :auth_user_id"),
            {"auth_user_id": auth_user_id},
        ).mappings().first()
        is_admin_row = session.execute(
            text("SELECT 1 FROM platform_admins WHERE user_id = :user_id AND revoked_at IS NULL"),
            {"user_id": str(user_row["id"])} if user_row else {"user_id": None},
        ).first()

    return AuthenticatedIdentity(
        auth_user_id=auth_user_id,
        email=email,
        local_user_id=user_row["id"] if user_row else None,
        tenant_id=user_row["tenant_id"] if user_row else None,
        role=user_row["role"] if user_row else None,
        is_platform_admin=bool(is_admin_row),
    )


def get_current_identity(authorization: str | None = Header(default=None)) -> AuthenticatedIdentity:
    """Strict resolver for ordinary tenant-scoped routes: 401 if not authenticated."""
    identity = _resolve_identity(authorization)
    if identity is None:
        raise HTTPException(status_code=401, detail="Missing or invalid session")
    return identity


def require_platform_admin(authorization: str | None = Header(default=None)) -> AuthenticatedIdentity:
    """
    Dependency for every /admin/* route. Returns 404, never 401/403, for
    ANY reason access should be denied -- no auth header, an invalid or
    expired token, or a real but non-admin identity -- so a tenant user or
    an unauthenticated stranger cannot even confirm the Console exists
    (CLAUDE.md Section 7.15.1).
    """
    identity = _resolve_identity(authorization)
    if identity is None or not identity.is_platform_admin:
        raise HTTPException(status_code=404)
    return identity
