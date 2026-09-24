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
    # The sign-in is valid but the account's admin removed this person (D-132).
    # They get no tenant and no role, and a catalog answer saying why (AUTH-004).
    access_removed: bool = False


# The only algorithms a Supabase session token is ever signed with. An
# allowlist, so a token claiming `alg: none` -- or any other algorithm -- is
# rejected before a key is chosen for it.
_SYMMETRIC_ALGS = frozenset({"HS256"})
_ASYMMETRIC_ALGS = frozenset({"ES256", "RS256"})


def _decode_bearer_token(authorization: str | None) -> dict | None:
    """
    Returns the decoded claims, or None if there's no usable/valid token.

    **The verification path is chosen by the token's own `alg`, not by which
    setting happens to be filled in.** A Supabase project signs either with
    the legacy shared secret (HS256) or with asymmetric keys published at its
    JWKS endpoint (ES256/RS256), and a project can have a legacy secret
    configured while issuing ES256 -- which is the default for new projects.
    An earlier version branched on `if settings.supabase_jwt_secret` and
    returned None when HS256 verification failed, so on such a project every
    real session token was rejected: sign-in succeeded at Supabase and then
    the API answered 401 to everything, and /admin/* answered 404. See
    DECISIONS.md D-088.

    Selecting the key by `alg` is safe here because the two algorithm classes
    take keys from different places: HS256 uses the project's shared secret,
    which an attacker does not have, and ES256/RS256 use public keys from
    JWKS, which cannot be used to forge a signature. The classic algorithm-
    confusion attack -- handing a public key to an HMAC verifier -- is not
    reachable, because the HS256 branch only ever uses the configured secret.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1]
    settings = get_settings()

    if not settings.supabase_jwt_secret and not settings.supabase_url:
        raise RuntimeError(
            "Neither SUPABASE_JWT_SECRET nor SUPABASE_URL is set -- there's no way to verify a "
            "session token. See SETUP.md Step 1."
        )

    try:
        alg = jwt.get_unverified_header(token).get("alg")
    except jwt.PyJWTError:
        return None

    if alg in _SYMMETRIC_ALGS and settings.supabase_jwt_secret:
        try:
            return jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=sorted(_SYMMETRIC_ALGS),
                audience="authenticated",
            )
        except jwt.PyJWTError:
            return None

    if alg in _ASYMMETRIC_ALGS and settings.supabase_url:
        try:
            signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=sorted(_ASYMMETRIC_ALGS),
                audience="authenticated",
            )
        except Exception:
            # Covers jwt.PyJWTError as well as JWKS-fetch failures (network,
            # bad url, unknown kid) -- all of them mean "can't verify this
            # token", not "the server is broken", so this fails closed rather
            # than raising.
            return None

    # An algorithm we do not accept, or one we have no key material for.
    return None


def _resolve_identity(authorization: str | None) -> AuthenticatedIdentity | None:
    claims = _decode_bearer_token(authorization)
    if claims is None:
        return None
    auth_user_id = claims["sub"]
    email = claims.get("email", "")

    with identity_lookup_session(auth_user_id) as session:
        user_row = session.execute(
            text(
                "SELECT id, tenant_id, role, is_active AND deleted_at IS NULL AS active "
                "FROM users WHERE auth_user_id = :auth_user_id"
            ),
            {"auth_user_id": auth_user_id},
        ).mappings().first()
        # A removed person's Supabase session stays valid until it expires, so
        # the refusal has to happen here, on every request -- not at sign-in.
        if user_row is not None and not user_row["active"]:
            return AuthenticatedIdentity(
                auth_user_id=auth_user_id,
                email=email,
                local_user_id=None,
                tenant_id=None,
                role=None,
                is_platform_admin=False,
                access_removed=True,
            )
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
        from app.errors import catalog_error

        # A catalog answer, so the page can say "you're signed out" and send
        # the person to sign in -- not "we couldn't reach DocFlow" (D-134).
        raise catalog_error("AUTH-005", status_code=401)
    return identity


# CLAUDE.md Section 3: "Roles: owner, admin, reviewer, viewer -- all
# tenant-scoped. Permissions enforced at the API layer, never only in the UI."
#
# The only distinction the MVP needs: a `viewer` reads, everyone else reviews.
# Splitting admin from reviewer would be a permissions model the product does
# not yet have opinions about, and Section 3 defers "roles beyond the four
# above" anyway.
REVIEWING_ROLES: frozenset[str] = frozenset({"owner", "admin", "reviewer"})

# The customer's own administrators. The first user of a tenant is stored as
# `owner` and shown as "Admin" (D-128); `admin` is equivalent and enforced
# everywhere, though no screen hands it out yet.
ADMIN_ROLES: frozenset[str] = frozenset({"owner", "admin"})


def require_tenant_member(identity: AuthenticatedIdentity) -> UUID:
    """
    The tenant this request acts in, or 403.

    A platform-admin-only account (D-004) has no tenant of its own; the
    Console reaches a tenant's documents through its own acting-as path
    (Section 7.15.1), not through these routes.
    """
    if identity.access_removed:
        from app.errors import catalog_error

        raise catalog_error("AUTH-004", status_code=403)
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


def require_tenant_admin(identity: AuthenticatedIdentity) -> UUID:
    """
    The tenant, for a route only the customer's own admin may reach -- their
    dashboard, and managing the people on the account (D-128).

    The tenant's first user is stored as `owner` and shown as "Admin"; the
    `admin` role is equivalent and remains valid, though nothing hands it out
    yet. A reviewer is refused here with a catalog code, not a bare 403, and
    never with a 404: unlike the Console (7.15.1), this surface is one the
    person is legitimately signed in to -- hiding it would only confuse them.
    """
    tenant_id = require_tenant_member(identity)
    if identity.role not in ADMIN_ROLES:
        from app.errors import catalog_error

        raise catalog_error("AUTH-003", status_code=403, extra={"role": identity.role})
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
