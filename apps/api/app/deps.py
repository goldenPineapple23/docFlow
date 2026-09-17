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

Token verification, two paths: if `SUPABASE_JWT_SECRET` is set (the
project's "Legacy JWT Secret" -- still available and simplest, no network
call per request), verify with HS256 against that shared secret. Otherwise,
fall back to the project's JWKS endpoint
(`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`) for projects that only use
the newer asymmetric signing keys. Tests use the shared-secret path so they
don't need network access (see apps/api/tests/test_admin_access.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import jwt
from docflow_core.config import get_settings
from docflow_core.db import identity_lookup_session
from fastapi import Header, HTTPException
from jwt import PyJWKClient
from sqlalchemy import text

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        settings = get_settings()
        if not settings.supabase_url:
            raise RuntimeError("SUPABASE_URL is not set. See SETUP.md Step 1.")
        _jwks_client = PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json")
    return _jwks_client


@dataclass(frozen=True)
class AuthenticatedIdentity:
    auth_user_id: str
    email: str
    local_user_id: UUID | None
    tenant_id: UUID | None
    role: str | None
    is_platform_admin: bool


def _decode_bearer_token(authorization: str | None) -> dict | None:
    """Returns the decoded claims, or None if there's no usable/valid token."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1]
    settings = get_settings()

    if settings.supabase_jwt_secret:
        try:
            return jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
        except jwt.PyJWTError:
            return None

    if not settings.supabase_url:
        raise RuntimeError(
            "Neither SUPABASE_JWT_SECRET nor SUPABASE_URL is set -- there's no way to verify a "
            "session token. See SETUP.md Step 1."
        )
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
        )
    except Exception:
        # Covers jwt.PyJWTError as well as JWKS-fetch failures (network, bad
        # url, unknown kid) -- all of them mean "can't verify this token",
        # not "the server is broken", so this fails closed like the HS256
        # path above rather than raising.
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


# CLAUDE.md Section 3: "Roles: owner, admin, reviewer, viewer -- all
# tenant-scoped. Permissions enforced at the API layer, never only in the UI."
#
# The only distinction the MVP needs: a `viewer` reads, everyone else reviews.
# Splitting admin from reviewer would be a permissions model the product does
# not yet have opinions about, and Section 3 defers "roles beyond the four
# above" anyway.
REVIEWING_ROLES: frozenset[str] = frozenset({"owner", "admin", "reviewer"})


def require_tenant_member(identity: AuthenticatedIdentity) -> UUID:
    """
    The tenant this request acts in, or 403.

    A platform-admin-only account (D-004) has no tenant of its own; the
    Console reaches a tenant's documents through its own acting-as path
    (Section 7.15.1), not through these routes.
    """
    if identity.tenant_id is None:
        raise HTTPException(
            status_code=403, detail="This account is not associated with a tenant."
        )
    return identity.tenant_id


def require_reviewer(identity: AuthenticatedIdentity) -> UUID:
    """
    The tenant, for a route that changes something.

    Enforced here rather than in the UI, so hiding a button is a courtesy
    rather than the control (Section 3). Raised as a catalog code, because a
    permission refusal is a user-facing failure like any other (7.16.5).
    """
    tenant_id = require_tenant_member(identity)
    if identity.role not in REVIEWING_ROLES:
        # Imported here rather than at module scope: app.errors imports from
        # docflow_core, and deps is imported by everything.
        from app.errors import catalog_error

        raise catalog_error("AUTH-002", status_code=403, extra={"role": identity.role})
    return tenant_id


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
