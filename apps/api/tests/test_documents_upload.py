"""
CLAUDE.md Section 7.11/7.5 required tests for POST /documents/upload: a
valid Tier 1 file is accepted and enqueued; an invalid file (oversized,
wrong magic bytes, zip bomb) is rejected with a catalog-coded response
before ever touching storage; and one tenant's upload never leaks a
duplicate-detection signal across a tenant boundary. All of these need the
real documents/intake_rejections tables (see conftest.py:
requires_documents_schema) since RLS/tenant-scoping is being proven for
real, not mocked.
"""

from __future__ import annotations

from uuid import uuid4

import jwt
from docflow_core.config import get_settings
from docflow_core.db import platform_session
from docflow_core.file_types import MAX_FILE_SIZE_BYTES
from sqlalchemy import text

from tests.conftest import requires_database, requires_documents_schema

JWT_SECRET = "test-only-secret-for-ci"


class _FakeCeleryClient:
    def __init__(self):
        self.sent = []

    def send_task(self, name, args=None, queue=None):
        self.sent.append({"name": name, "args": args, "queue": queue})


def _make_token(auth_user_id: str, email: str) -> str:
    claims = {"sub": auth_user_id, "email": email, "aud": "authenticated"}
    return jwt.encode(claims, JWT_SECRET, algorithm="HS256")


class _TestTenant:
    """Creates a throwaway tenant + owner user, cleans up everything on exit."""

    def __init__(self, name: str, email: str):
        self.name = name
        self.email = email
        self.tenant_id = uuid4()
        self.user_id = uuid4()
        self.auth_user_id = str(uuid4())

    def __enter__(self):
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO tenants "
                    "(id, name, status, onboarding_status, created_at, updated_at, status_changed_at) "
                    "VALUES (:id, :name, 'active', 'tenant_created', now(), now(), now())"
                ),
                {"id": str(self.tenant_id), "name": self.name},
            )
            session.execute(
                text(
                    "INSERT INTO users (id, tenant_id, auth_user_id, email, role, is_active) "
                    "VALUES (:id, :tenant_id, :auth_user_id, :email, 'owner', true)"
                ),
                {
                    "id": str(self.user_id),
                    "tenant_id": str(self.tenant_id),
                    "auth_user_id": self.auth_user_id,
                    "email": self.email,
                },
            )
        return self

    def token(self) -> str:
        return _make_token(self.auth_user_id, self.email)

    def __exit__(self, *exc):
        tid = str(self.tenant_id)
        with platform_session() as session:
            session.execute(text("DELETE FROM document_lines WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM document_headers WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM documents WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM intake_rejections WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM users WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tid})


@requires_documents_schema
def test_upload_rejects_oversized_file(client, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    get_settings.cache_clear()
    try:
        with _TestTenant("Acme Test Distributor", "owner-oversized@example.test") as tenant:
            big_content = b"%PDF-1.4\n" + b"0" * (MAX_FILE_SIZE_BYTES + 1)
            response = client.post(
                "/documents/upload",
                headers={"Authorization": f"Bearer {tenant.token()}"},
                files={"file": ("big.pdf", big_content, "application/pdf")},
            )
            assert response.status_code == 422
            body = response.json()["detail"]
            assert body["code"] == "DOC-002"
            assert "action" in body and body["action"]

            with platform_session() as session:
                row = session.execute(
                    text("SELECT error_code FROM intake_rejections WHERE tenant_id = :tid"),
                    {"tid": str(tenant.tenant_id)},
                ).mappings().first()
            assert row is not None
            assert row["error_code"] == "DOC-002"
    finally:
        get_settings.cache_clear()


@requires_documents_schema
def test_upload_rejects_zip_bomb(client, monkeypatch):
    import zipfile
    from io import BytesIO

    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    get_settings.cache_clear()
    try:
        with _TestTenant("Acme Test Distributor", "owner-zipbomb@example.test") as tenant:
            buf = BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("word/document.xml", b"\x00" * (5 * 1024 * 1024))
            response = client.post(
                "/documents/upload",
                headers={"Authorization": f"Bearer {tenant.token()}"},
                files={"file": ("bomb.docx", buf.getvalue(), "application/octet-stream")},
            )
            assert response.status_code == 422
            assert response.json()["detail"]["code"] == "DOC-003"
    finally:
        get_settings.cache_clear()


@requires_documents_schema
def test_upload_accepts_valid_file_and_enqueues(client, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    get_settings.cache_clear()
    fake_celery = _FakeCeleryClient()
    monkeypatch.setattr("app.routers.documents.celery_client", fake_celery)
    try:
        with _TestTenant("Acme Test Distributor", "owner-valid@example.test") as tenant:
            content = b"PO Number: TEST-0001\nBuyer: Acme Test Distributor\n"
            response = client.post(
                "/documents/upload",
                headers={"Authorization": f"Bearer {tenant.token()}"},
                files={"file": ("po.txt", content, "text/plain")},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "pending"
            assert "document_id" in body
            assert "possible_duplicate_of" not in body

            assert len(fake_celery.sent) == 1
            sent = fake_celery.sent[0]
            assert sent["name"] == "docflow.parse_and_extract"
            assert sent["queue"] == "interactive"
            assert sent["args"] == [str(tenant.tenant_id), body["document_id"]]

            with platform_session() as session:
                row = session.execute(
                    text("SELECT status, source, tenant_id FROM documents WHERE id = :id"),
                    {"id": body["document_id"]},
                ).mappings().first()
            assert row is not None
            assert row["status"] == "pending"
            assert row["source"] == "upload"
            assert str(row["tenant_id"]) == str(tenant.tenant_id)
    finally:
        get_settings.cache_clear()


@requires_documents_schema
def test_duplicate_detection_does_not_cross_tenant_boundary(client, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    get_settings.cache_clear()
    fake_celery = _FakeCeleryClient()
    monkeypatch.setattr("app.routers.documents.celery_client", fake_celery)
    try:
        with _TestTenant("Acme Test Distributor A", "owner-dup-a@example.test") as tenant_a, _TestTenant(
            "Acme Test Distributor B", "owner-dup-b@example.test"
        ) as tenant_b:
            content = b"identical content, uploaded by two different tenants\n"

            response_a = client.post(
                "/documents/upload",
                headers={"Authorization": f"Bearer {tenant_a.token()}"},
                files={"file": ("po.txt", content, "text/plain")},
            )
            assert response_a.status_code == 200
            assert "possible_duplicate_of" not in response_a.json()

            response_b = client.post(
                "/documents/upload",
                headers={"Authorization": f"Bearer {tenant_b.token()}"},
                files={"file": ("po.txt", content, "text/plain")},
            )
            assert response_b.status_code == 200
            # Same bytes, but a different tenant -- RLS scopes the duplicate
            # lookup to tenant_b's own rows, so tenant_a's document must
            # never surface here (CLAUDE.md Section 7.5).
            assert "possible_duplicate_of" not in response_b.json()
    finally:
        get_settings.cache_clear()


@requires_documents_schema
def test_upload_accepts_a_tier2_file_and_enqueues_it_unconverted(client, monkeypatch):
    """
    Tier 2 support arrives at this endpoint for free: the router only calls
    the one allowlist module, and conversion happens later in the isolated
    worker (CLAUDE.md Section 7.11 -- "parsing never runs in the web
    process"). This test proves the router accepts and enqueues a `.tif`
    without parsing or converting a single byte of it.
    """
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    get_settings.cache_clear()
    fake_celery = _FakeCeleryClient()
    monkeypatch.setattr("app.routers.documents.celery_client", fake_celery)
    try:
        with _TestTenant("Acme Test Distributor", "owner-tier2@example.test") as tenant:
            # A TIFF header: enough for the allowlist, and deliberately not a
            # decodable image -- if the web process were converting, this
            # would fail here instead of in the worker.
            content = b"II\x2a\x00" + b"\x00" * 64
            response = client.post(
                "/documents/upload",
                headers={"Authorization": f"Bearer {tenant.token()}"},
                files={"file": ("fax.tif", content, "image/tiff")},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "pending"
            assert len(fake_celery.sent) == 1
    finally:
        get_settings.cache_clear()


@requires_documents_schema
def test_upload_rejects_an_archive_with_its_own_catalog_code(client, monkeypatch):
    """
    CLAUDE.md Section 7.11: archives are never auto-extracted, and every
    rejection names the format and the fix.
    """
    import zipfile
    from io import BytesIO

    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    get_settings.cache_clear()
    try:
        with _TestTenant("Acme Test Distributor", "owner-archive@example.test") as tenant:
            buf = BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("purchase_order.txt", b"PO Number: TEST-0001\n")
            response = client.post(
                "/documents/upload",
                headers={"Authorization": f"Bearer {tenant.token()}"},
                files={"file": ("orders.zip", buf.getvalue(), "application/zip")},
            )
            assert response.status_code == 422
            body = response.json()["detail"]
            assert body["code"] == "DOC-010"
            assert ".zip" in body["message"]
            assert body["action"]
    finally:
        get_settings.cache_clear()


@requires_database
def test_upload_requires_a_tenant_scoped_account(client, monkeypatch):
    """A platform-admin-only account (tenant_id IS NULL, D-004) gets 403, not a crash."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    get_settings.cache_clear()
    admin_user_id = uuid4()
    admin_auth_user_id = str(uuid4())
    admin_email = "platform-only@example.test"
    try:
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO users (id, tenant_id, auth_user_id, email, role, is_active) "
                    "VALUES (:id, NULL, :auth_user_id, :email, 'owner', true)"
                ),
                {"id": str(admin_user_id), "auth_user_id": admin_auth_user_id, "email": admin_email},
            )
        token = _make_token(admin_auth_user_id, admin_email)
        response = client.post(
            "/documents/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("po.txt", b"hello", "text/plain")},
        )
        assert response.status_code == 403
    finally:
        with platform_session() as session:
            session.execute(text("DELETE FROM users WHERE id = :id"), {"id": str(admin_user_id)})
        get_settings.cache_clear()
