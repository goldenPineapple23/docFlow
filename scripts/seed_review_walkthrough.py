"""
One-time script: sets up a tenant, a reviewer login, a catalog and one
document waiting in the review queue, so a person can be timed correcting
and approving it.

This is the Phase 3 exit criterion from CLAUDE.md Section 6:

    "a non-technical person corrects and approves the golden fixture in
     under 2 minutes; the audit trail shows exactly what changed"

Only a person can run that test. This script builds the thing they sit down
in front of.

**Why it seeds the document rather than uploading one.** The real path is
upload -> Celery -> the isolated parsing worker -> Anthropic -> matching ->
validation. There is no Redis on this machine yet, so the queue cannot run,
and the exit criterion is about the REVIEW SCREEN, not about extraction --
which the golden fixture test already covers against the live API. So this
writes the same rows the pipeline would have written, using the recorded
golden response as its source, and then runs the REAL matching and
validation code over them. The warnings the reviewer sees are genuine
output, not decoration.

**One value is deliberately wrong.** Line 3's quantity is seeded as 2 where
the document plainly says 24 -- the kind of misread a low-quality scan
produces. That gives the reviewer something real to find and fix, and it
makes the line total and the order total stop reconciling, so validation
raises warnings exactly as it would in production. Everything else is the
golden fixture verbatim.

All data is fictional (CLAUDE.md Section 0 rule 4).

Usage:

    python scripts/seed_review_walkthrough.py reviewer@example.com [password]

Re-running it is safe: it reuses the tenant and reviewer if they exist and
adds a fresh document each time.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text

from docflow_core.buyers import normalize_buyer_name
from docflow_core.config import get_settings
from docflow_core.db import platform_session, tenant_session
from docflow_core.matching import match_document_lines
from docflow_core.storage import save_file
from docflow_core.validation import validate_document

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = REPO_ROOT / "apps" / "api" / "tests" / "fixtures" / "golden" / "recorded_response.json"
SAMPLE_PO = REPO_ROOT / "docs" / "sample_po.txt"

TENANT_NAME = "Acme Test Distributor"

# The catalog this tenant sells from. Three of the document's four SKUs match
# exactly; the fourth is deliberately absent so the reviewer has a line to
# search for and map, which is the "SKU search + create mapping" half of the
# Phase 3 scope.
CATALOG = [
    ("CF-1001", "Colombian Whole Bean 5lb", "CS"),
    ("CF-2210", "Ethiopian Yirgacheffe 5lb", "CS"),
    ("SY-0045", "Vanilla Syrup 750ml", "EA"),
    ("CUP-12X", "12oz Paper Cups (1000 count)", "BOX"),
]

# The misread: the document says 24, the extraction recorded 2.
MISREAD_LINE_NUMBER = 3
MISREAD_QUANTITY = "2"


def _auth_user(email: str, password: str | None) -> tuple[str, str | None]:
    """Creates (or finds) the Supabase Auth account. Returns (id, password)."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        sys.exit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env -- see SETUP.md Step 1.")

    url = f"{settings.supabase_url}/auth/v1/admin/users"
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }

    password = password or ("DocFlow-" + secrets.token_urlsafe(12))

    resp = httpx.get(url, headers=headers, params={"email": email}, timeout=20)
    resp.raise_for_status()
    for existing in resp.json().get("users", []):
        if existing.get("email", "").lower() == email.lower():
            # Set the password so the founder can hand over a known one.
            httpx.put(
                f"{url}/{existing['id']}",
                headers=headers,
                json={"password": password},
                timeout=20,
            ).raise_for_status()
            return existing["id"], password

    resp = httpx.post(
        url,
        headers=headers,
        json={"email": email, "password": password, "email_confirm": True},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["id"], password


def _tenant_and_reviewer(email: str, auth_user_id: str) -> tuple[UUID, UUID]:
    with platform_session() as session:
        row = session.execute(
            text("SELECT id FROM tenants WHERE name = :name AND deleted_at IS NULL"),
            {"name": TENANT_NAME},
        ).mappings().first()
        if row:
            tenant_id = UUID(str(row["id"]))
        else:
            tenant_id = uuid4()
            session.execute(
                text(
                    "INSERT INTO tenants (id, name, status, onboarding_status, created_at, "
                    "updated_at, status_changed_at) "
                    "VALUES (:id, :name, 'active', 'tenant_created', now(), now(), now())"
                ),
                {"id": str(tenant_id), "name": TENANT_NAME},
            )
            print(f"Created tenant {TENANT_NAME} ({tenant_id}).")

        user_row = session.execute(
            text("SELECT id FROM users WHERE email = :email AND tenant_id = :tenant_id"),
            {"email": email, "tenant_id": str(tenant_id)},
        ).mappings().first()
        if user_row:
            user_id = UUID(str(user_row["id"]))
            session.execute(
                text("UPDATE users SET auth_user_id = :a, role = 'reviewer', is_active = true WHERE id = :id"),
                {"a": auth_user_id, "id": str(user_id)},
            )
        else:
            user_id = uuid4()
            session.execute(
                text(
                    "INSERT INTO users (id, tenant_id, auth_user_id, email, role, is_active) "
                    "VALUES (:id, :tenant_id, :auth_user_id, :email, 'reviewer', true)"
                ),
                {
                    "id": str(user_id),
                    "tenant_id": str(tenant_id),
                    "auth_user_id": auth_user_id,
                    "email": email,
                },
            )
            print(f"Created reviewer {email} ({user_id}).")

        for sku, description, uom in CATALOG:
            session.execute(
                text(
                    "INSERT INTO items (id, tenant_id, sku, description, unit_of_measure, "
                    "created_at, updated_at) "
                    "VALUES (:id, :tenant_id, :sku, :description, :uom, now(), now()) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "sku": sku,
                    "description": description,
                    "uom": uom,
                },
            )

    return tenant_id, user_id


def _seed_document(tenant_id: UUID) -> UUID:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    header = golden["header"]
    content = SAMPLE_PO.read_bytes()

    storage_path = save_file(tenant_id, "purchase-order.txt", content)
    document_id = uuid4()

    with platform_session() as session:
        session.execute(
            text(
                """
                INSERT INTO documents
                    (id, tenant_id, original_filename, storage_path, source, status,
                     content_sha256, injection_suspected, overall_confidence, raw_json, created_at)
                VALUES
                    (:id, :tenant_id, 'purchase-order.txt', :storage_path, 'upload', 'needs_review',
                     :sha, false, :confidence, :raw_json, now())
                """
            ),
            {
                "id": str(document_id),
                "tenant_id": str(tenant_id),
                "storage_path": storage_path,
                # The hash of what was actually stored, so duplicate detection
                # behaves normally if this is run twice.
                "sha": hashlib.sha256(content + str(document_id).encode()).hexdigest(),
                "confidence": str(min(Decimal(str(v)) for v in golden["header_confidence"].values())),
                "raw_json": json.dumps(golden),
            },
        )

        session.execute(
            text(
                """
                INSERT INTO document_headers
                    (document_id, tenant_id, po_number, order_date, requested_delivery_date,
                     buyer_name, buyer_contact_email, ship_to_address, payment_terms,
                     order_total, currency, notes, header_confidence, currency_inferred,
                     created_at, updated_at)
                VALUES
                    (:document_id, :tenant_id, :po_number, :order_date, :requested_delivery_date,
                     :buyer_name, :buyer_contact_email, :ship_to_address, :payment_terms,
                     :order_total, :currency, :notes, :header_confidence, :currency_inferred,
                     now(), now())
                """
            ),
            {
                "document_id": str(document_id),
                "tenant_id": str(tenant_id),
                **{k: header.get(k) for k in (
                    "po_number", "order_date", "requested_delivery_date", "buyer_name",
                    "buyer_contact_email", "ship_to_address", "payment_terms", "currency", "notes",
                )},
                "order_total": header["order_total"],
                "header_confidence": json.dumps(golden["header_confidence"]),
                # The golden PO prints "$47.50" and never the letters "USD",
                # so the model inferred the currency from a symbol and capped
                # its confidence at 0.6 (Section 7.1). Hardcoding this False
                # suppressed the banner that explains WHY that field is
                # flagged, and the first walkthrough tester reasonably asked
                # why "everything is USD" was being questioned.
                "currency_inferred": bool(golden.get("currency_inferred")),
            },
        )

        for item in golden["line_items"]:
            quantity = item["quantity"]
            if item["line_number"] == MISREAD_LINE_NUMBER:
                quantity = MISREAD_QUANTITY

            session.execute(
                text(
                    """
                    INSERT INTO document_lines
                        (id, document_id, tenant_id, line_number, sku, description, unit,
                         quantity, unit_price, line_total, confidence, uom_mismatch, created_at)
                    VALUES
                        (:id, :document_id, :tenant_id, :line_number, :sku, :description, :unit,
                         :quantity, :unit_price, :line_total, :confidence, false, now())
                    """
                ),
                {
                    "id": str(uuid4()),
                    "document_id": str(document_id),
                    "tenant_id": str(tenant_id),
                    "line_number": item["line_number"],
                    "sku": item.get("sku"),
                    "description": item.get("description"),
                    "unit": item.get("unit"),
                    "quantity": quantity,
                    "unit_price": item.get("unit_price"),
                    "line_total": item.get("line_total"),
                    "confidence": str(item.get("confidence")),
                },
            )

    # The real matching and validation code, over the seeded rows.
    with tenant_session(tenant_id) as session:
        match_document_lines(session, tenant_id, document_id)
    with tenant_session(tenant_id) as session:
        summary = validate_document(session, tenant_id, document_id)

    print(f"Seeded document {document_id} and ran matching + validation ({summary.created} warnings).")
    return document_id


def main() -> None:
    if len(sys.argv) not in (2, 3):
        sys.exit("Usage: python scripts/seed_review_walkthrough.py <reviewer-email> [password]")
    email = sys.argv[1]
    password = sys.argv[2] if len(sys.argv) == 3 else None

    auth_user_id, password = _auth_user(email, password)
    tenant_id, _user_id = _tenant_and_reviewer(email, auth_user_id)
    document_id = _seed_document(tenant_id)

    settings = get_settings()
    base = settings.app_base_url.rstrip("/")
    print()
    print("=" * 68)
    print("  Ready. Hand these to the person doing the walkthrough:")
    print()
    print(f"    URL:      {base}/login")
    print(f"    Email:    {email}")
    print(f"    Password: {password}")
    print()
    print(f"    Then:     {base}/review")
    print(f"    Document: {base}/review/{document_id}")
    print()
    print("  The order they open says 24 on line 3; the data says 2.")
    print("  That is the correction to find. Time them from opening the")
    print("  document to the moment it shows as approved.")
    print("=" * 68)


if __name__ == "__main__":
    main()
