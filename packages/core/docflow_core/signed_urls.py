"""
Short-lived signed URLs for original documents (CLAUDE.md Section 7.4 / 7.12).

The specification:

    "Downloads use short-lived signed URLs; storage paths are never
     user-controlled or user-visible."
    "The original-document viewer (PDF/image) renders inside a sandboxed
     iframe with a strict Content-Security-Policy; the document is served
     from a signed, short-lived, same-tenant URL."

What a token proves, and why each part is in it:

  * **document_id** -- which document. Without it a token for one file would
    open any file.
  * **tenant_id** -- which tenant. This is what makes "same-tenant" a
    property of the token rather than a check someone has to remember. A
    token minted for Tenant A cannot be replayed against Tenant B's route
    even if the document ids were somehow guessed, because the signature
    covers the tenant and the serving route compares it to the session's own.
  * **expiry** -- when it stops working, as an absolute UNIX timestamp.

The storage path is deliberately NOT in the token and never reaches the
client. The serving route looks it up from the document id, inside the
tenant-scoped session, so a path can never be supplied, guessed, or traversed
from outside (Section 7.4, and Section 7.11's "filenames are untrusted").

Signed with HMAC-SHA256 and compared with `hmac.compare_digest`, so a wrong
token fails in constant time rather than leaking how much of it was right.
"""

from __future__ import annotations

import base64
import hmac
import time
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from docflow_core.config import get_settings

# Short, because the viewer fetches it immediately after the page loads. Long
# enough to survive a slow connection and a reviewer whose laptop slept for a
# moment mid-load.
DEFAULT_TTL_SECONDS = 300


class InvalidSignedUrl(Exception):
    """A token that is malformed, tampered with, or expired. One exception
    for all three on purpose -- telling a caller which one it was tells an
    attacker which part to change next."""


@dataclass(frozen=True)
class SignedDocumentToken:
    document_id: UUID
    tenant_id: UUID
    expires_at: int


def _signing_key() -> bytes:
    settings = get_settings()
    secret = settings.document_url_signing_secret
    if not secret:
        raise RuntimeError(
            "DOCUMENT_URL_SIGNING_SECRET is not set, so document viewer URLs cannot be "
            "signed. See SETUP.md Step 2 and .env.example."
        )
    return secret.encode("utf-8")


def _payload(document_id: UUID, tenant_id: UUID, expires_at: int) -> str:
    return f"{document_id}:{tenant_id}:{expires_at}"


def _sign(payload: str) -> str:
    digest = hmac.new(_signing_key(), payload.encode("utf-8"), sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def mint_document_token(
    document_id: UUID, tenant_id: UUID, *, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> tuple[str, int]:
    """
    A token for one tenant's one document, and the UNIX time it expires.

    Callers must already have established that this tenant may read this
    document -- minting is not an authorization check, it is a way of
    carrying one that has already happened.
    """
    expires_at = int(time.time()) + ttl_seconds
    payload = _payload(document_id, tenant_id, expires_at)
    return f"{expires_at}.{_sign(payload)}", expires_at


def verify_document_token(token: str, document_id: UUID, tenant_id: UUID) -> SignedDocumentToken:
    """
    Check a token against the document and tenant the route is serving.

    Raises `InvalidSignedUrl` for a malformed, tampered or expired token. The
    caller passes the tenant from the authenticated session, never from the
    request, so a valid token for Tenant A presented on Tenant B's session
    fails the signature comparison (Section 7.5).
    """
    expires_part, _, signature = token.partition(".")
    if not signature:
        raise InvalidSignedUrl("malformed token")
    try:
        expires_at = int(expires_part)
    except ValueError as exc:
        raise InvalidSignedUrl("malformed token") from exc

    expected = _sign(_payload(document_id, tenant_id, expires_at))
    if not hmac.compare_digest(expected, signature):
        raise InvalidSignedUrl("bad signature")
    if expires_at < int(time.time()):
        raise InvalidSignedUrl("expired")

    return SignedDocumentToken(
        document_id=document_id, tenant_id=tenant_id, expires_at=expires_at
    )
