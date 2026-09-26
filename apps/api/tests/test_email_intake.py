"""
CLAUDE.md Section 7.16.3/7.16.4 required tests for POST /intake/email/{token},
adapted to what exists in this slice (no Console "Held for review" screen
yet -- these test the backend outcomes directly): an 11-attachment email
quarantines all 11 and enqueues none; a 21st unknown-sender email in a
rolling hour is quarantined while a simultaneous known sender's email is
not; a DMARC-fail email quarantines its attachment; a DMARC-none email with
a failing SPF is NOT quarantined (per DECISIONS.md's documented
interpretation); a no-attachment email writes an intake_rejections row and
nothing else; a duplicate webhook delivery (same Message-ID) is idempotent;
a resolvable-token email with one valid Tier-1 attachment creates a
`documents` row with source='email' and enqueues parse_and_extract; and an
unresolvable token 404s without leaking whether the tenant exists.

All of these (other than the unresolvable-token case) need the real
raw_emails/documents/intake_rejections tables, so they're gated on
requires_email_intake_schema (see conftest.py).
"""

from __future__ import annotations

import base64
from uuid import uuid4

from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import requires_database, requires_email_intake_schema


class _FakeCeleryClient:
    def __init__(self):
        self.sent = []

    def send_task(self, name, args=None, queue=None):
        self.sent.append({"name": name, "args": args, "queue": queue})


class _TestIntakeTenant:
    """Creates a throwaway tenant + active intake address, cleans up everything on exit."""

    def __init__(self, name: str, *, live: bool = True):
        self.name = name
        self.live = live
        self.tenant_id = uuid4()
        self.token = uuid4().hex
        self.address = f"{self.token}@mail.docflow.test"

    def __enter__(self):
        with platform_session() as session:
            session.execute(
                text(
                    # A live intake address: mail to a tenant that hasn't gone
                    # live is logged and not read (D-114), tested separately.
                    "INSERT INTO tenants "
                    "(id, name, status, onboarding_status, intake_address_active, "
                    "created_at, updated_at, status_changed_at) "
                    "VALUES (:id, :name, 'active', 'tenant_created', :live, now(), now(), now())"
                ),
                {"id": str(self.tenant_id), "name": self.name, "live": self.live},
            )
            session.execute(
                text(
                    "INSERT INTO intake_addresses (id, tenant_id, token, address, status, created_at) "
                    "VALUES (:id, :tenant_id, :token, :address, 'active', now())"
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": str(self.tenant_id),
                    "token": self.token,
                    "address": self.address,
                },
            )
        return self

    def seed_document(self, sender_email: str, status: str = "pending") -> None:
        """Directly inserts a document row to simulate prior email history for
        known-sender / unknown-sender-velocity tests, without going through
        the webhook (keeps those tests fast and focused on the query logic)."""
        with platform_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO documents
                        (id, tenant_id, original_filename, storage_path, source, status,
                         content_sha256, sender_email, raw_json, created_at)
                    VALUES
                        (:id, :tenant_id, 'seed.txt', 'tenants/seed/seed.txt', 'email', :status,
                         :sha, :sender_email,
                         '{"header": {}, "line_items": [], "test_fixture": true}'::jsonb,
                         now())
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": str(self.tenant_id),
                    "status": status,
                    "sha": uuid4().hex,
                    "sender_email": sender_email,
                },
            )

    def documents(self) -> list[dict]:
        with platform_session() as session:
            rows = session.execute(
                text(
                    "SELECT id, status, quarantine_reason, sender_email, source "
                    "FROM documents WHERE tenant_id = :tid ORDER BY created_at"
                ),
                {"tid": str(self.tenant_id)},
            ).mappings().all()
        return [dict(r) for r in rows]

    def raw_emails(self) -> list[dict]:
        with platform_session() as session:
            rows = session.execute(
                text("SELECT id, outcome, message_id FROM raw_emails WHERE tenant_id = :tid"),
                {"tid": str(self.tenant_id)},
            ).mappings().all()
        return [dict(r) for r in rows]

    def intake_rejections(self) -> list[dict]:
        with platform_session() as session:
            rows = session.execute(
                text("SELECT error_code, sender_email FROM intake_rejections WHERE tenant_id = :tid"),
                {"tid": str(self.tenant_id)},
            ).mappings().all()
        return [dict(r) for r in rows]

    def __exit__(self, *exc):
        tid = str(self.tenant_id)
        with platform_session() as session:
            session.execute(text("DELETE FROM raw_emails WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM document_lines WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM document_headers WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM documents WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM intake_rejections WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM intake_addresses WHERE tenant_id = :tid"), {"tid": tid})
            # D-145: a held unverified sender raises a founder alert, which
            # names its email; both point at the tenant.
            session.execute(text("DELETE FROM founder_alerts WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM email_outbox WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tid})


def _pm_attachment(name: str, content: bytes, content_type: str = "text/plain") -> dict:
    return {
        "Name": name,
        "Content": base64.b64encode(content).decode("ascii"),
        "ContentType": content_type,
        "ContentLength": len(content),
    }


def _pm_payload(
    sender_email: str,
    *,
    subject: str = "Test PO",
    message_id: str | None = None,
    attachments: list[dict] | None = None,
    headers: list[dict] | None = None,
) -> dict:
    return {
        "From": sender_email,
        "FromFull": {"Email": sender_email, "Name": "Test Sender"},
        "Subject": subject,
        "MessageID": message_id or str(uuid4()),
        "Date": "Wed, 16 Sep 2026 12:00:00 +0000",
        "TextBody": "",
        "HtmlBody": "",
        "Headers": headers or [],
        "Attachments": attachments or [],
    }


def _auth_header(value: str) -> list[dict]:
    return [{"Name": "Authentication-Results", "Value": value}]


@requires_email_intake_schema
def test_11_attachments_quarantines_all_and_none_enqueued(client, monkeypatch):
    fake_celery = _FakeCeleryClient()
    monkeypatch.setattr("docflow_core.email_intake.celery_client", fake_celery)
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        attachments = [_pm_attachment(f"po-{i}.txt", f"PO number {i}".encode()) for i in range(11)]
        payload = _pm_payload("buyer-cap@example.test", attachments=attachments)
        response = client.post(f"/intake/email/{tenant.token}", json=payload)
        assert response.status_code == 200
        assert response.json()["outcome"] == "quarantined"

        docs = tenant.documents()
        assert len(docs) == 11
        assert all(d["status"] == "quarantined" for d in docs)
        assert all(d["quarantine_reason"] == "attachment_cap" for d in docs)
        assert fake_celery.sent == []


@requires_email_intake_schema
def test_unknown_sender_velocity_quarantines_21st_but_not_known_sender(client, monkeypatch):
    fake_celery = _FakeCeleryClient()
    monkeypatch.setattr("docflow_core.email_intake.celery_client", fake_celery)
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        # A known sender: has one prior, non-quarantined document already.
        tenant.seed_document("known-buyer@example.test", status="pending")
        # 20 distinct first-time (unknown) senders, all within the trailing hour.
        for i in range(20):
            tenant.seed_document(f"unknown-{i}@example.test", status="pending")

        # The known sender's email arrives at the same time -- must NOT be
        # affected by velocity regardless of the current count.
        known_payload = _pm_payload(
            "known-buyer@example.test", attachments=[_pm_attachment("known.txt", b"known sender PO")]
        )
        known_response = client.post(f"/intake/email/{tenant.token}", json=known_payload)
        assert known_response.status_code == 200
        assert known_response.json()["outcome"] == "processed"

        # The 21st unknown sender must be quarantined.
        new_unknown_payload = _pm_payload(
            "unknown-20@example.test", attachments=[_pm_attachment("new.txt", b"brand new unknown sender PO")]
        )
        new_unknown_response = client.post(f"/intake/email/{tenant.token}", json=new_unknown_payload)
        assert new_unknown_response.status_code == 200
        assert new_unknown_response.json()["outcome"] == "quarantined"

        known_docs = [d for d in tenant.documents() if d["sender_email"] == "known-buyer@example.test"]
        new_unknown_docs = [d for d in tenant.documents() if d["sender_email"] == "unknown-20@example.test"]
        assert any(d["status"] == "pending" for d in known_docs)
        assert any(
            d["status"] == "quarantined" and d["quarantine_reason"] == "unknown_sender_velocity"
            for d in new_unknown_docs
        )


@requires_email_intake_schema
def test_dmarc_fail_quarantines_attachment(client, monkeypatch):
    fake_celery = _FakeCeleryClient()
    monkeypatch.setattr("docflow_core.email_intake.celery_client", fake_celery)
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        payload = _pm_payload(
            "spoofed-buyer@example.test",
            attachments=[_pm_attachment("po.txt", b"a purchase order")],
            headers=_auth_header(
                "mx.example.com; spf=pass smtp.mailfrom=x@example.test; dkim=pass; dmarc=fail"
            ),
        )
        response = client.post(f"/intake/email/{tenant.token}", json=payload)
        assert response.status_code == 200
        assert response.json()["outcome"] == "quarantined"

        docs = tenant.documents()
        assert len(docs) == 1
        assert docs[0]["status"] == "quarantined"
        assert docs[0]["quarantine_reason"] == "auth_fail"
        assert fake_celery.sent == []

        # INT-004 tells the sender DocFlow has been alerted (D-145).
        with platform_session() as session:
            alerts = session.execute(
                text(
                    "SELECT severity, payload FROM founder_alerts "
                    "WHERE tenant_id = :t AND type = 'unverified_sender_held'"
                ),
                {"t": str(tenant.tenant_id)},
            ).mappings().all()
        assert len(alerts) == 1
        assert alerts[0]["payload"]["error_code"] == "INT-004"


@requires_email_intake_schema
def test_dmarc_none_with_spf_fail_is_not_quarantined(client, monkeypatch):
    fake_celery = _FakeCeleryClient()
    monkeypatch.setattr("docflow_core.email_intake.celery_client", fake_celery)
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        payload = _pm_payload(
            "no-dmarc-buyer@example.test",
            attachments=[_pm_attachment("po.txt", b"a purchase order")],
            headers=_auth_header("mx.example.com; spf=fail smtp.mailfrom=x@example.test"),
        )
        response = client.post(f"/intake/email/{tenant.token}", json=payload)
        assert response.status_code == 200
        assert response.json()["outcome"] == "processed"

        docs = tenant.documents()
        assert len(docs) == 1
        assert docs[0]["status"] == "pending"
        assert docs[0]["quarantine_reason"] is None
        assert len(fake_celery.sent) == 1


@requires_email_intake_schema
def test_no_attachment_writes_intake_rejection_only(client, monkeypatch):
    fake_celery = _FakeCeleryClient()
    monkeypatch.setattr("docflow_core.email_intake.celery_client", fake_celery)
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        payload = _pm_payload("no-attachment-buyer@example.test", attachments=[])
        response = client.post(f"/intake/email/{tenant.token}", json=payload)
        assert response.status_code == 200
        assert response.json()["outcome"] == "rejected"

        assert tenant.documents() == []
        rejections = tenant.intake_rejections()
        assert len(rejections) == 1
        assert rejections[0]["error_code"] == "INT-001"
        raw = tenant.raw_emails()
        assert len(raw) == 1
        assert raw[0]["outcome"] == "rejected"
        assert fake_celery.sent == []


@requires_email_intake_schema
def test_duplicate_webhook_delivery_is_idempotent(client, monkeypatch):
    fake_celery = _FakeCeleryClient()
    monkeypatch.setattr("docflow_core.email_intake.celery_client", fake_celery)
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        message_id = f"<{uuid4()}@mail.example.test>"
        payload = _pm_payload(
            "repeat-buyer@example.test",
            message_id=message_id,
            attachments=[_pm_attachment("po.txt", b"a purchase order")],
        )

        first = client.post(f"/intake/email/{tenant.token}", json=payload)
        assert first.status_code == 200
        assert first.json()["outcome"] == "processed"

        second = client.post(f"/intake/email/{tenant.token}", json=payload)
        assert second.status_code == 200
        assert second.json()["outcome"] == "duplicate"

        assert len(tenant.raw_emails()) == 1
        assert len(tenant.documents()) == 1
        assert len(fake_celery.sent) == 1


@requires_email_intake_schema
def test_valid_tier1_attachment_creates_document_and_enqueues(client, monkeypatch):
    fake_celery = _FakeCeleryClient()
    monkeypatch.setattr("docflow_core.email_intake.celery_client", fake_celery)
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        payload = _pm_payload(
            "fresh-buyer@example.test",
            attachments=[_pm_attachment("po.txt", b"PO Number: TEST-0001\nBuyer: Acme Test Distributor\n")],
        )
        response = client.post(f"/intake/email/{tenant.token}", json=payload)
        assert response.status_code == 200
        assert response.json()["outcome"] == "processed"

        docs = tenant.documents()
        assert len(docs) == 1
        assert docs[0]["source"] == "email"
        assert docs[0]["status"] == "pending"

        assert len(fake_celery.sent) == 1
        sent = fake_celery.sent[0]
        assert sent["name"] == "docflow.parse_and_extract"
        assert sent["queue"] == "interactive"
        assert sent["args"] == [str(tenant.tenant_id), str(docs[0]["id"])]


@requires_email_intake_schema
def test_tier2_attachment_is_accepted_and_a_tier3_one_is_rejected_by_code(client, monkeypatch):
    """
    Email intake reads the same one allowlist module as the upload endpoint,
    so Tier 2 works here without a line of intake-specific code, and a Tier 3
    archive is refused with its own catalog code (CLAUDE.md Section 7.11).
    Nothing is converted here -- that happens in the worker.
    """
    import zipfile
    from io import BytesIO

    fake_celery = _FakeCeleryClient()
    monkeypatch.setattr("docflow_core.email_intake.celery_client", fake_celery)
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("purchase_order.txt", b"PO Number: TEST-0002\n")
        payload = _pm_payload(
            "fresh-buyer-tier2@example.test",
            attachments=[
                _pm_attachment("fax.tif", b"II\x2a\x00" + b"\x00" * 64, "image/tiff"),
                _pm_attachment("orders.zip", buf.getvalue(), "application/zip"),
            ],
        )
        response = client.post(f"/intake/email/{tenant.token}", json=payload)
        assert response.status_code == 200
        assert response.json()["outcome"] == "processed"

        docs = tenant.documents()
        assert len(docs) == 1  # the TIFF only; the archive never becomes a document
        assert len(fake_celery.sent) == 1

        rejections = tenant.intake_rejections()
        assert [row["error_code"] for row in rejections] == ["DOC-010"]


@requires_database
def test_unresolvable_token_returns_404_without_leaking_existence(client):
    response = client.post(f"/intake/email/{uuid4().hex}", json=_pm_payload("nobody@example.test"))
    assert response.status_code == 404


@requires_email_intake_schema
def test_mail_to_a_tenant_not_yet_live_is_logged_not_read_and_answered_once_a_day(client, monkeypatch):
    """Section 7.15.2 Step 2: until go-live the address "auto-replies 'this
    address is not yet active' and processes nothing" (D-114)."""
    fake_celery = _FakeCeleryClient()
    monkeypatch.setattr("docflow_core.email_intake.celery_client", fake_celery)
    with _TestIntakeTenant("Acme Test Distributor", live=False) as tenant:
        for _ in range(2):
            payload = _pm_payload(
                "early-buyer@example.test",
                attachments=[_pm_attachment("po.txt", b"PO Number: TEST-0001\n")],
            )
            response = client.post(f"/intake/email/{tenant.token}", json=payload)
            assert response.status_code == 200
            assert response.json()["outcome"] == "rejected"

        assert tenant.documents() == []
        assert fake_celery.sent == []
        assert [r["error_code"] for r in tenant.intake_rejections()] == ["INT-005", "INT-005"]
        with platform_session() as session:
            replies = session.execute(
                text(
                    "SELECT to_address FROM email_outbox "
                    "WHERE tenant_id = :t AND template = 'intake_not_active'"
                ),
                {"t": str(tenant.tenant_id)},
            ).scalars().all()
        assert replies == ["early-buyer@example.test"]  # once, not once per email
