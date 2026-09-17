"""
Short-lived signed URLs for the document viewer (CLAUDE.md Section 7.4 / 7.12).

The properties that matter are all about what a token CANNOT do: open another
document, open another tenant's copy, survive its expiry, or be forged by
changing the part that says when it expires.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import time
from uuid import uuid4

import pytest

from docflow_core.config import get_settings
from docflow_core.signed_urls import (
    InvalidSignedUrl,
    mint_document_token,
    verify_document_token,
)

SECRET = "test-only-signing-secret-not-a-real-one"


@pytest.fixture(autouse=True)
def _signing_secret(monkeypatch):
    monkeypatch.setenv("DOCUMENT_URL_SIGNING_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_a_freshly_minted_token_verifies():
    document_id, tenant_id = uuid4(), uuid4()
    token, expires_at = mint_document_token(document_id, tenant_id)

    verified = verify_document_token(token, document_id)

    assert verified.document_id == document_id
    assert verified.tenant_id == tenant_id
    assert verified.expires_at == expires_at
    assert expires_at > int(time.time())


def test_a_token_for_one_document_does_not_open_another():
    tenant_id = uuid4()
    token, _ = mint_document_token(uuid4(), tenant_id)

    with pytest.raises(InvalidSignedUrl):
        verify_document_token(token, uuid4())


def test_the_tenant_cannot_be_swapped_without_breaking_the_signature():
    """
    Section 7.5's "a signed URL for one tenant's file cannot be reused across
    tenants".

    The tenant now travels inside the token, because the viewer's iframe
    cannot send an Authorization header (D-089) -- so the guarantee is that
    the tenant segment is *covered by the signature*. Editing it to name
    another tenant invalidates the token, and minting a fresh one requires
    the signing secret.
    """
    document_id, tenant_id = uuid4(), uuid4()
    token, expires_at = mint_document_token(document_id, tenant_id)
    _, _, rest = token.partition(".")
    _, _, signature = rest.partition(".")

    other_tenant = uuid4()
    forged = f"{other_tenant}.{expires_at}.{signature}"

    with pytest.raises(InvalidSignedUrl):
        verify_document_token(forged, document_id)


def test_the_verified_tenant_is_the_one_the_token_was_minted_for():
    """The route opens a tenant-scoped session with this, so it has to be the
    tenant that minted it and never anything the caller chose."""
    document_id, tenant_id = uuid4(), uuid4()
    token, _ = mint_document_token(document_id, tenant_id)

    assert verify_document_token(token, document_id).tenant_id == tenant_id


def test_an_expired_token_is_refused():
    document_id, tenant_id = uuid4(), uuid4()
    token, _ = mint_document_token(document_id, tenant_id, ttl_seconds=-1)

    with pytest.raises(InvalidSignedUrl):
        verify_document_token(token, document_id)


def test_extending_the_expiry_invalidates_the_signature():
    """The expiry is inside the signed payload, so a client cannot buy
    itself more time by editing the part of the token it can read."""
    document_id, tenant_id = uuid4(), uuid4()
    token, expires_at = mint_document_token(document_id, tenant_id)
    _, _, rest = token.partition(".")
    _, _, signature = rest.partition(".")
    forged = f"{tenant_id}.{expires_at + 86400}.{signature}"

    with pytest.raises(InvalidSignedUrl):
        verify_document_token(forged, document_id)


def test_a_tampered_signature_is_refused():
    document_id, tenant_id = uuid4(), uuid4()
    token, expires_at = mint_document_token(document_id, tenant_id)

    with pytest.raises(InvalidSignedUrl):
        verify_document_token(f"{tenant_id}.{expires_at}.not-the-signature", document_id)


@pytest.mark.parametrize("malformed", ["", "no-dot", ".", "abc.def", "12x.sig"])
def test_a_malformed_token_is_refused_without_crashing(malformed):
    with pytest.raises(InvalidSignedUrl):
        verify_document_token(malformed, uuid4())


def test_a_token_minted_under_a_different_secret_is_refused(monkeypatch):
    """Rotating the secret invalidates in-flight viewer URLs -- which is the
    point of it being a separate secret from anything that signs a session."""
    document_id, tenant_id = uuid4(), uuid4()
    token, _ = mint_document_token(document_id, tenant_id)

    monkeypatch.setenv("DOCUMENT_URL_SIGNING_SECRET", "a-different-secret")
    get_settings.cache_clear()

    with pytest.raises(InvalidSignedUrl):
        verify_document_token(token, document_id)


def test_the_token_never_contains_the_storage_path():
    """Section 7.4: storage paths are never user-visible. The token carries
    ids and an expiry; the serving route looks the path up itself."""
    document_id, tenant_id = uuid4(), uuid4()
    token, _ = mint_document_token(document_id, tenant_id)

    assert "tenants/" not in token
    assert "/" not in token
