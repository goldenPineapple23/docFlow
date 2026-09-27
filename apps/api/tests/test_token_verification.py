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

import time

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


# ── Clock skew (D-167) ──────────────────────────────────────────────────────


def _at(offset_seconds: int) -> dict:
    """The claims, with `iat` shifted relative to this machine's clock."""
    now = int(time.time())
    return {**CLAIMS, "iat": now + offset_seconds, "exp": now + offset_seconds + 3600}


def test_a_token_issued_a_few_seconds_ahead_of_this_clock_is_accepted(monkeypatch, es256_keys):
    """
    Supabase stamps `iat` from its own clock. When this server's clock is a
    second or two behind, a token the browser has only just been issued is
    "not yet valid" here -- and the person is told they are signed out on
    their first request after signing in. Found driving the real stack: one
    sign-in in six, with `iat` exactly one second ahead of `now` (D-167).
    """
    private_key, public_key = es256_keys
    _stub_jwks(monkeypatch, public_key)
    token = jwt.encode(_at(5), private_key, algorithm="ES256")

    claims = deps._decode_bearer_token(f"Bearer {token}")

    assert claims is not None and claims["sub"] == CLAIMS["sub"]


def test_the_same_allowance_applies_to_the_shared_secret_path(monkeypatch, es256_keys):
    token = jwt.encode(_at(5), HS_SECRET, algorithm="HS256")

    claims = deps._decode_bearer_token(f"Bearer {token}")

    assert claims is not None and claims["sub"] == CLAIMS["sub"]


def test_a_token_from_far_in_the_future_is_still_refused(monkeypatch, es256_keys):
    """The allowance is for clock skew, not for a token that was never
    plausibly issued to this session."""
    private_key, public_key = es256_keys
    _stub_jwks(monkeypatch, public_key)
    token = jwt.encode(_at(3600), private_key, algorithm="ES256")

    assert deps._decode_bearer_token(f"Bearer {token}") is None


def test_the_allowance_is_symmetric_and_that_is_on_purpose(monkeypatch, es256_keys):
    """
    PyJWT applies `leeway` to `exp` as well as to `iat`, so a token that has
    just expired is accepted for the same 30 seconds. This asserts it rather
    than leaving it as a side effect nobody wrote down (D-167).

    It costs nothing that matters: a session token is good for an hour
    either way, and access is re-read from the `users` row on every request,
    so removing someone takes effect on their next request and not when
    their token runs out (D-132).
    """
    private_key, public_key = es256_keys
    _stub_jwks(monkeypatch, public_key)
    now = int(time.time())
    token = jwt.encode({**CLAIMS, "iat": now - 3600, "exp": now - 5}, private_key, algorithm="ES256")

    assert deps._decode_bearer_token(f"Bearer {token}") is not None


def test_a_token_expired_beyond_the_allowance_is_refused(monkeypatch, es256_keys):
    """The line has to be somewhere, and it is 30 seconds."""
    private_key, public_key = es256_keys
    _stub_jwks(monkeypatch, public_key)
    now = int(time.time())
    token = jwt.encode(
        {**CLAIMS, "iat": now - 3600, "exp": now - 120}, private_key, algorithm="ES256"
    )

    assert deps._decode_bearer_token(f"Bearer {token}") is None


def test_an_expired_token_is_still_refused(monkeypatch, es256_keys):
    private_key, public_key = es256_keys
    _stub_jwks(monkeypatch, public_key)
    now = int(time.time())
    token = jwt.encode(
        {**CLAIMS, "iat": now - 7200, "exp": now - 3600}, private_key, algorithm="ES256"
    )

    assert deps._decode_bearer_token(f"Bearer {token}") is None
