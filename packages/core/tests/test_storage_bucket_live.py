"""
Stage 3b item 2, against the real bucket: `docflow-files` is private and
`storage.objects` has no policies, so nothing but DocFlow's S3 access key
reaches a file -- not the public URL, not the anon key, and not a customer's
own signed-in token.

Runs wherever Storage is configured: against staging from the founder's
machine (STORAGE_S3_* and SUPABASE_ANON_KEY in the root .env), and against
the runner's local stack in CI (scripts/ci/local_storage_env.py sets both).
"""

from __future__ import annotations

import os
import time
from uuid import uuid4

import httpx
import jwt
import pytest

from docflow_core import storage
from docflow_core.config import get_settings


def _anon_key() -> str:
    return get_settings().supabase_anon_key or os.environ.get("DOCFLOW_TEST_ANON_KEY", "")


requires_bucket = pytest.mark.skipif(
    not (get_settings().storage_s3_endpoint and _anon_key()),
    reason="Storage is not configured here (STORAGE_S3_* and an anon key) -- see .env.example.",
)


def _rest_base() -> str:
    # https://<ref>.storage.supabase.co/storage/v1/s3 -> .../storage/v1
    return get_settings().storage_s3_endpoint.rstrip("/").removesuffix("/s3")


@pytest.fixture()
def probe():
    tenant = uuid4()
    content = f"probe {uuid4()}".encode()
    path = storage.save_file(tenant, "probe.txt", content)
    yield tenant, path, content
    storage.delete_tenant_file(tenant, path)


def _refused(response: httpx.Response, content: bytes) -> bool:
    return response.status_code != 200 and content not in response.content


@requires_bucket
def test_the_bucket_has_no_public_url(probe):
    _, path, content = probe
    response = httpx.get(f"{_rest_base()}/object/public/{storage.BUCKET}/{path}", timeout=15)
    assert _refused(response, content)


@requires_bucket
def test_the_anon_key_reaches_no_file(probe):
    _, path, content = probe
    anon = _anon_key()
    headers = {"apikey": anon, "Authorization": f"Bearer {anon}"}
    got = httpx.get(f"{_rest_base()}/object/{storage.BUCKET}/{path}", headers=headers, timeout=15)
    assert _refused(got, content)
    listed = httpx.post(
        f"{_rest_base()}/object/list/{storage.BUCKET}",
        headers=headers,
        json={"prefix": path.rsplit("/", 1)[0]},
        timeout=15,
    )
    assert listed.status_code != 200 or listed.json() == []


@requires_bucket
def test_a_hard_delete_empties_the_tenants_folder_in_the_real_bucket():
    """Item 7's file step against the real Storage, not the fake: the bulk
    delete once answered 400 on Supabase (its body wasn't labelled XML),
    which the in-memory fake could never show."""
    tenant = uuid4()
    for name in ("one.txt", "two.txt", "three.txt"):
        storage.save_file(tenant, name, f"hard delete probe {uuid4()}".encode())
    storage.save_derived(tenant, uuid4(), "extracted_text", b"derived probe")

    assert storage.delete_tenant_storage(tenant) == 4
    assert storage._get_backend().list_keys(f"tenants/{tenant}/") == []


@requires_bucket
def test_a_customers_own_signed_in_token_reaches_no_file(probe):
    """A tenant user's token is `role: authenticated`. Minted here with the
    project's JWT secret; skipped where that secret isn't held (staging keeps
    it blank, D-174), in which case the anon-key test above is the check."""
    secret = get_settings().supabase_jwt_secret
    if not secret:
        pytest.skip("SUPABASE_JWT_SECRET is blank here (D-174); the anon-key test covers it")
    _, path, content = probe
    now = int(time.time())
    token = jwt.encode(
        {"sub": str(uuid4()), "role": "authenticated", "aud": "authenticated", "iat": now, "exp": now + 300},
        secret,
        algorithm="HS256",
    )
    headers = {"apikey": _anon_key(), "Authorization": f"Bearer {token}"}
    got = httpx.get(f"{_rest_base()}/object/{storage.BUCKET}/{path}", headers=headers, timeout=15)
    assert _refused(got, content)
