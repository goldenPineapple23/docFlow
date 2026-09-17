"""
Session-token verification (CLAUDE.md Section 3, Section 7.5).

These exist because of a bug that blocked every login on a real Supabase
project for two phases (DECISIONS.md D-088). A project can have the legacy
shared secret configured *and* issue ES256 tokens -- that is the default for
new projects -- and the old code chose its verification path from which
setting was filled in rather than from the token itself. It then returned
None when HS256 verification failed, so sign-in succeeded at Supabase and
the API answered 401 to everything, with /admin/* answering 404.

Nothing here needs a database or a network: the JWKS client is stubbed with a
locally generated key pair, so the asymmetric path is exercised for real
without reaching Supabase.
"""

from __future__ import annotations

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from docflow_core.config import get_settings

from app import deps

HS_SECRET = "test-only-legacy-shared-secret"
CLAIMS = {"sub": "11111111-1111-1111-1111-111111111111", "email": "x@example.test", "aud": "authenticated"}


@pytest.fixture
def es256_keys():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", HS_SECRET)
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _stub_jwks(monkeypatch, public_key):
    class _Key:
        def __init__(self, key):
            self.key = key

    class _Client:
        def get_signing_key_from_jwt(self, _token):
            return _Key(public_key)

    monkeypatch.setattr(deps, "_get_jwks_client", lambda: _Client())


def test_an_es256_token_is_accepted_even_when_a_legacy_secret_is_configured(monkeypatch, es256_keys):
    """
    The regression. A new Supabase project signs with ES256 while a legacy
    HS256 secret may still be present in .env; the token, not the config,
    decides how it is verified.
    """
    private_key, public_key = es256_keys
    _stub_jwks(monkeypatch, public_key)
    token = jwt.encode(CLAIMS, private_key, algorithm="ES256")

    claims = deps._decode_bearer_token(f"Bearer {token}")

    assert claims is not None
    assert claims["sub"] == CLAIMS["sub"]


def test_an_hs256_token_is_still_accepted(monkeypatch, es256_keys):
    """The legacy path keeps working for projects that use it."""
    _, public_key = es256_keys
    _stub_jwks(monkeypatch, public_key)
    token = jwt.encode(CLAIMS, HS_SECRET, algorithm="HS256")

    claims = deps._decode_bearer_token(f"Bearer {token}")

    assert claims is not None
    assert claims["sub"] == CLAIMS["sub"]


def test_a_token_signed_with_the_wrong_es256_key_is_refused(monkeypatch, es256_keys):
    _, public_key = es256_keys
    _stub_jwks(monkeypatch, public_key)
    attacker_key = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(CLAIMS, attacker_key, algorithm="ES256")

    assert deps._decode_bearer_token(f"Bearer {token}") is None


def test_a_token_signed_with_the_wrong_shared_secret_is_refused(monkeypatch, es256_keys):
    _, public_key = es256_keys
    _stub_jwks(monkeypatch, public_key)
    token = jwt.encode(CLAIMS, "not-the-secret", algorithm="HS256")

    assert deps._decode_bearer_token(f"Bearer {token}") is None


def test_an_unsigned_token_is_refused(monkeypatch, es256_keys):
    """
    `alg: none` is the oldest JWT attack there is. Selecting the key by the
    token's own `alg` is only safe because the accepted set is an allowlist.
    """
    _, public_key = es256_keys
    _stub_jwks(monkeypatch, public_key)
    token = jwt.encode(CLAIMS, key="", algorithm="none")

    assert deps._decode_bearer_token(f"Bearer {token}") is None


def test_a_missing_or_malformed_header_is_refused():
    assert deps._decode_bearer_token(None) is None
    assert deps._decode_bearer_token("") is None
    assert deps._decode_bearer_token("Basic abc") is None
    assert deps._decode_bearer_token("Bearer not-a-jwt") is None


def test_no_key_material_at_all_is_a_loud_failure(monkeypatch):
    """
    A server with neither verification path configured cannot authenticate
    anyone. That is a deployment mistake, not a bad token, so it raises
    rather than quietly answering 401 to every request.
    """
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "")
    monkeypatch.setenv("SUPABASE_URL", "")
    get_settings.cache_clear()
    token = jwt.encode(CLAIMS, HS_SECRET, algorithm="HS256")

    with pytest.raises(RuntimeError):
        deps._decode_bearer_token(f"Bearer {token}")
