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

import base64
import binascii
import hmac
import logging
import time
from dataclasses import dataclass
from uuid import UUID

import jwt
from docflow_core.config import get_settings
from docflow_core.constants import MFA_CLOCK_TOLERANCE_SECONDS, MFA_STEP_UP_MAX_AGE_SECONDS
from docflow_core.db import identity_lookup_session
from fastapi import Depends, Header, HTTPException
from jwt import PyJWKClient
from sqlalchemy import text

logger = logging.getLogger(__name__)

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
    # From the session token (D-151, D-177): `aal` is "aal2" once a TOTP
    # challenge has been passed in this session, and `totp_verified_at` is that
    # challenge's time -- the `amr` entry whose method is "totp", in GoTrue's
    # clock (unix seconds). Read from a real token after a real challenge on
    # staging, not assumed (RUNBOOK 1.6).
    aal: str | None = None
    totp_verified_at: int | None = None


# The only algorithms a Supabase session token is ever signed with. An
# allowlist, so a token claiming `alg: none` -- or any other algorithm -- is
# rejected before a key is chosen for it.
_SYMMETRIC_ALGS = frozenset({"HS256"})
_ASYMMETRIC_ALGS = frozenset({"ES256", "RS256"})

# How far this server's clock may differ from Supabase's before a real
# session token is refused (DECISIONS.md D-167).
#
# Supabase stamps `iat` from its own clock. If this machine's clock is even
# one second behind, the token that a browser has just been issued is "not
# yet valid" here, PyJWT raises ImmatureSignatureError, and the person is
# told they are signed out and sent back to /login -- on the very first
# request after signing in, which is when the gap is smallest and the odds
# of losing that coin flip are highest. Caught driving the real stack: one
# sign-in in six failed with iat exactly one second ahead of `now`.
#
# **This allowance is symmetric: PyJWT applies `leeway` to `exp` as well as
# to `iat` and `nbf`.** So an expired token is accepted for this long after
# it expires. That is the cost of the fix and it is stated here rather than
# buried, because it is the only part of it that gives anything away.
#
# 30 seconds, chosen over the conventional 60 (founder, 2026-09-27): the
# skew actually observed was one second, ordinary NTP drift is well under
# 30, and halving the number halves the only downside. If a machine is ever
# more than 30 seconds out, that is an operational fault worth seeing, not
# one worth absorbing silently.
#
# What the 30 seconds on `exp` is worth to an attacker: nothing they did not
# already have. A session token is valid for an hour regardless, and every
# request re-reads the `users` row -- so deactivating or removing someone
# (is_active, deleted_at) takes effect on their next request, not when their
# token expires (D-132).
_CLOCK_SKEW_LEEWAY_SECONDS = 30


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
                leeway=_CLOCK_SKEW_LEEWAY_SECONDS,
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
                leeway=_CLOCK_SKEW_LEEWAY_SECONDS,
            )
        except Exception as exc:
            # Covers jwt.PyJWTError as well as JWKS-fetch failures (network,
            # bad url, unknown kid) -- all of them mean "can't verify this
            # token", not "the server is broken", so this fails closed rather
            # than raising.
            #
            # Logged, because failing closed silently makes two very
            # different things look identical from outside: a forged token,
            # and a signing key we could not fetch. The second signs a real
            # person out for a network blip, and until this line existed
            # there was nothing anywhere to say so. The error's type and text
            # only -- never the token, never a claim (Section 7.10).
            logger.warning("Session token rejected on the JWKS path: %s: %s",
                           type(exc).__name__, exc)
            return None

    # An algorithm we do not accept, or one we have no key material for.
    return None


def _resolve_identity(authorization: str | None) -> AuthenticatedIdentity | None:
    claims = _decode_bearer_token(authorization)
    if claims is None:
        return None
    auth_user_id = claims["sub"]
    email = claims.get("email", "")
    aal = claims.get("aal")
    totp_verified_at = _totp_verified_at(claims)

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
                aal=aal,
                totp_verified_at=totp_verified_at,
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
        aal=aal,
        totp_verified_at=totp_verified_at,
    )


def _totp_verified_at(claims: dict) -> int | None:
    """The latest TOTP challenge time in the token's `amr`, or None."""
    times = [
        entry.get("timestamp")
        for entry in claims.get("amr") or []
        if isinstance(entry, dict) and entry.get("method") == "totp"
    ]
    stamps = [t for t in times if isinstance(t, int) and not isinstance(t, bool)]
    return max(stamps) if stamps else None


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
    # Only a real platform admin gets this far, so answering with a catalog
    # code instead of a 404 reveals nothing to anyone else (7.15.1).
    if get_settings().console_mfa_enforced and identity.aal != "aal2":
        from app.errors import catalog_error

        raise catalog_error("AUTH-006", status_code=403)
    return identity


@dataclass(frozen=True)
class StepUp:
    """A platform admin, cleared for a destructive Console action (D-151)."""

    identity: AuthenticatedIdentity
    # Seconds since the TOTP challenge, by this server's clock; None while
    # enforcement is off and no challenge is on the token. Recorded in the
    # action's admin_actions payload so the audit shows the step-up happened.
    challenge_age_seconds: int | None

    def audit(self) -> dict:
        return {"mfa_challenge_age_seconds": self.challenge_age_seconds}


def require_recent_mfa(identity: AuthenticatedIdentity = Depends(require_platform_admin)) -> StepUp:
    """
    Dependency for a destructive Console action (D-151, D-177): hard delete,
    clear quarantine, cancel, intake-address rotation, buyer merge, go live and
    tier change. On top of the Console's aal2 gate, the TOTP challenge on the
    token must be at most MFA_STEP_UP_MAX_AGE_SECONDS old.

    GoTrue stamps the challenge on its clock, so the comparison allows
    MFA_CLOCK_TOLERANCE_SECONDS either way (D-170): a challenge up to five and
    a half minutes old passes, and one stamped further than the tolerance in
    our future does not -- that is a clock fault, not a fresh challenge.
    """
    stamp = identity.totp_verified_at
    age = int(time.time()) - stamp if stamp is not None else None
    if not get_settings().console_mfa_enforced:
        return StepUp(identity=identity, challenge_age_seconds=age)
    fresh = (
        age is not None
        and -MFA_CLOCK_TOLERANCE_SECONDS <= age <= MFA_STEP_UP_MAX_AGE_SECONDS + MFA_CLOCK_TOLERANCE_SECONDS
    )
    if not fresh:
        from app.errors import catalog_error

        raise catalog_error("AUTH-007", status_code=403)
    return StepUp(identity=identity, challenge_age_seconds=age)


# ── The inbound-mail webhook's own credentials (review finding H8) ──────────
#
# The per-tenant token in the intake URL identifies the tenant. It is the local
# part of an address the customer gives to its buyers, so it is public the
# moment the product is used as intended, and it therefore cannot also be the
# authentication. Without a separate provider credential, anyone holding an
# intake address could POST a Postmark-shaped body with any `From` and any
# `Authentication-Results`, which defeats the DMARC/SPF quarantine, the
# unknown-sender velocity rule and sender-based example selection all at once
# (CLAUDE.md Section 7.16.3).
#
# Postmark carries HTTP Basic credentials on the webhook URL. They are compared
# in constant time, and neither the supplied value nor the configured one is
# ever logged, put in a response, or added to an alert payload (Section 7.10).


class InboundWebhookRefused(Exception):
    """Raised when inbound mail is not from the configured provider."""

    def __init__(self, reason: str) -> None:
        # `reason` is one of a fixed set of words below -- never the credential,
        # and never anything taken from the request.
        self.reason = reason
        super().__init__(reason)


def _configured_inbound_credentials() -> tuple[str, str] | None:
    settings = get_settings()
    if not settings.postmark_webhook_username or not settings.postmark_webhook_password:
        return None
    return settings.postmark_webhook_username, settings.postmark_webhook_password


def check_inbound_webhook_credentials(authorization: str | None) -> None:
    """
    Accept only a request carrying the configured HTTP Basic credentials.

    Blank configuration refuses everything, on purpose: falling back to
    token-only when the environment variable is missing would let a deploy
    mistake silently reopen H8, and a hole that opens quietly is worse than
    inbound mail that stops loudly. RUNBOOK section 2 states the cutover order
    this implies.
    """
    configured = _configured_inbound_credentials()
    if configured is None:
        raise InboundWebhookRefused("not_configured")
    if not authorization:
        raise InboundWebhookRefused("no_credentials")

    scheme, _, encoded = authorization.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        raise InboundWebhookRefused("not_basic")
    try:
        supplied = base64.b64decode(encoded, validate=True)
    except binascii.Error:
        raise InboundWebhookRefused("undecodable") from None

    # Compared as BYTES, never as str. `hmac.compare_digest` raises TypeError on
    # a str containing non-ASCII characters, so comparing decoded text would let
    # a credential with one non-ASCII byte turn a 401 into a 500 -- a refusal
    # path that can be crashed by the thing it refuses. The bytes also mean no
    # decoding step can fail on attacker-chosen input.
    username, sep, password = supplied.partition(b":")
    if not sep:
        raise InboundWebhookRefused("undecodable")

    # Both halves compared, both in constant time, and `&` rather than `and` so
    # the second comparison is not skipped when the first fails -- otherwise the
    # time taken tells an attacker whether the username was right.
    expected_username, expected_password = configured
    ok = hmac.compare_digest(username, expected_username.encode("utf-8")) & hmac.compare_digest(
        password, expected_password.encode("utf-8")
    )
    if not ok:
        raise InboundWebhookRefused("mismatch")


def inbound_source_ip_note(client_host: str | None) -> str | None:
    """
    Whether this request came from an address on the configured allowlist.

    **Log-only. This never refuses anything.** The allowlist stays advisory
    until the real addresses are confirmed against Postmark's published list by
    the RUNBOOK section 2 procedure (D-155), because an allowlist enforced on a
    guess drops customers' purchase orders. Returns None when there is nothing
    to say -- no allowlist configured, or the address is on it.
    """
    allowlist = [a.strip() for a in get_settings().postmark_inbound_ip_allowlist.split(",") if a.strip()]
    if not allowlist or client_host is None:
        return None
    return None if client_host in allowlist else "source_not_on_allowlist"
