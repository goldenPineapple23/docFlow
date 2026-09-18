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


def _payload(object_id: UUID, tenant_id: UUID, expires_at: int, purpose: str = "document") -> str:
    """
    What the signature covers. An export link carries its purpose too, so a
    viewer token can never be replayed as an export download or the other
    way round, whatever the ids. The original-document payload is unchanged,
    so links minted before exports existed still verify.
    """
    if purpose == "document":
        return f"{object_id}:{tenant_id}:{expires_at}"
    return f"{purpose}:{object_id}:{tenant_id}:{expires_at}"


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

    The tenant travels **inside** the token, and inside the signature. The
    viewer loads the document in an `<iframe>`, and an iframe's request is a
    plain browser GET that carries no `Authorization` header -- so the URL
    has to be able to stand on its own. That is what Section 7.4's "downloads
    use short-lived signed URLs" means. See DECISIONS.md D-089.
    """
    expires_at = int(time.time()) + ttl_seconds
    payload = _payload(document_id, tenant_id, expires_at)
    return f"{tenant_id}.{expires_at}.{_sign(payload)}", expires_at


def verify_document_token(token: str, document_id: UUID) -> SignedDocumentToken:
    """
    Check a token against the document the route is serving, and return the
    tenant it was minted for.

    Raises `InvalidSignedUrl` for a malformed, tampered or expired token.

    **The tenant comes out of the token, and that is not a violation of
    Section 7.5.** That section forbids trusting a tenant_id supplied by the
    client; this one is not client-supplied in any meaningful sense -- it is
    covered by an HMAC the server computed with a secret only the server
    holds. Changing the tenant changes the signature, so a token cannot be
    edited to point at another tenant's copy of a document, and one cannot be
    minted at all without the secret. The caller then opens an ordinary
    tenant-scoped session with it, so RLS still decides what can be read.
    """
    return _verify(token, document_id, "document")


def _verify(token: str, object_id: UUID, purpose: str) -> SignedDocumentToken:
    tenant_part, _, rest = token.partition(".")
    expires_part, _, signature = rest.partition(".")
    if not signature or not expires_part:
        raise InvalidSignedUrl("malformed token")
    try:
        tenant_id = UUID(tenant_part)
        expires_at = int(expires_part)
    except ValueError as exc:
        raise InvalidSignedUrl("malformed token") from exc

    expected = _sign(_payload(object_id, tenant_id, expires_at, purpose))
    if not hmac.compare_digest(expected, signature):
        raise InvalidSignedUrl("bad signature")
    if expires_at < int(time.time()):
        raise InvalidSignedUrl("expired")

    return SignedDocumentToken(
        document_id=object_id, tenant_id=tenant_id, expires_at=expires_at
    )


# ── Export downloads (Section 7.4, Phase 4) ─────────────────────────────────
#
# Same construction, different purpose. A download is a plain browser GET
# (a link the user clicks, or `location.href`), so like the viewer it cannot
# carry an Authorization header and the token has to stand on its own. The
# `document_id` field of the returned token holds the EXPORT id here.


def mint_export_token(
    export_id: UUID, tenant_id: UUID, *, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> tuple[str, int]:
    """A token for one tenant's one export file. Minting is not authorization:
    callers must already have established this tenant may read this export."""
    expires_at = int(time.time()) + ttl_seconds
    payload = _payload(export_id, tenant_id, expires_at, "export")
    return f"{tenant_id}.{expires_at}.{_sign(payload)}", expires_at


def verify_export_token(token: str, export_id: UUID) -> SignedDocumentToken:
    """The tenant an export token was minted for, or `InvalidSignedUrl`."""
    return _verify(token, export_id, "export")
