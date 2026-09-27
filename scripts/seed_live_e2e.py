"""
Seeds (and removes) the throwaway tenant the live end-to-end suite drives.

`apps/web/e2e-live` is the one browser suite that talks to the **real** API
over HTTP, against real Postgres and real RLS, with nothing stubbed
(DECISIONS.md D-166). It exists because the stubbed suite (D-086) decides
for itself what the API returns, so no amount of it can prove that what the
screen shows after a write matches what the server now holds -- which is
exactly the seam the Stage 1 walkthrough found a bug in.

That suite needs three things this script provides: a tenant of its own, a
reviewer who can really sign in, and documents in known states. It creates
all of them under a tenant named for the run, writes their ids to a JSON
file, and deletes every row again on teardown -- so a run leaves nothing
behind, and two runs never share a row (D-160).

All data is fictional (CLAUDE.md Section 0 rule 4).

Usage:

    python scripts/seed_live_e2e.py setup    --out <file.json>
    python scripts/seed_live_e2e.py teardown --out <file.json>
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from docflow_core.config import get_settings
from docflow_core.db import platform_session, tenant_session
from docflow_core.validation import validate_document
from sqlalchemy import text

# The model's answer is immutable and required on anything reviewable
# (Section 7.1, D-158). These documents are inserted straight into review
# rather than extracted, so they carry the same clearly-labelled stand-in
# the Python fixtures use -- never an invented extraction.
FIXTURE_MODEL_ANSWER = '{"header": {}, "line_items": [], "test_fixture": true}'

# Every required header field, present and confident, so the only checks on
# these documents are the ones a test deliberately causes.
CONFIDENT_HEADER = {
    "po_number": 0.98,
    "order_date": 0.96,
    "buyer_name": 0.95,
    "order_total": 0.97,
    "currency": 0.99,
}


def _money(value: Decimal | None) -> str | None:
    """Every number reaches a NUMERIC column as a string, never a float
    (CLAUDE.md Section 7.1) -- in fixtures too."""
    return str(value) if value is not None else None


# ── Supabase Auth ───────────────────────────────────────────────────────────


def _auth_admin() -> tuple[str, dict[str, str]]:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        sys.exit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set -- see SETUP.md Step 1.")
    return (
        f"{settings.supabase_url}/auth/v1/admin/users",
        {
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
        },
    )


def _create_auth_user(email: str, password: str) -> str:
    url, headers = _auth_admin()
    response = httpx.post(
        url,
        headers=headers,
        json={"email": email, "password": password, "email_confirm": True},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["id"]


def _check_password_sign_in(email: str, password: str) -> None:
    """
    Prove the browser will be able to sign in, before a browser tries.

    Creating the account uses the service-role key and the admin endpoint;
    signing in uses the anon key and the public one. Those are different
    keys on different routes, so the first working says nothing about the
    second -- and when the second is what's broken, the symptom is three
    identical "waitForURL timed out" failures that name nothing. Ask here,
    where the answer can be reported properly.
    """
    settings = get_settings()
    if not settings.supabase_anon_key:
        sys.exit("SUPABASE_ANON_KEY is not set, so the browser could not sign in -- see SETUP.md Step 1.")

    response = httpx.post(
        f"{settings.supabase_url}/auth/v1/token?grant_type=password",
        headers={"apikey": settings.supabase_anon_key, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=30,
    )
    if response.status_code == 200 and "access_token" in response.json():
        # Report which signing algorithm this project really issues, so nobody
        # has to infer it. `app/deps.py` verifies HS256 against the legacy
        # shared secret and ES256/RS256 against JWKS -- different code, taking
        # keys from different places -- and this suite only ever exercises the
        # one its environment happens to issue. Staging and the CI stack are
        # different projects and need not agree, so each run says which path it
        # just proved. Header only: never the token, never a claim (7.10).
        header_b64 = response.json()["access_token"].split(".")[0]
        header = json.loads(base64.urlsafe_b64decode(header_b64 + "=" * (-len(header_b64) % 4)))
        print(
            f"  this project issues alg={header.get('alg')} "
            f"(kid {str(header.get('kid'))[:8]}) -- "
            + ("the JWKS path" if header.get("alg") in ("ES256", "RS256") else "the shared-secret path")
            + " is what this run proves"
        )
        return

    # What the auth server thinks it offers. A refusal here is usually
    # about how the server is configured rather than about this account,
    # and that answer is one request away -- so fetch it rather than
    # leaving whoever reads this to guess which provider is off.
    try:
        offered = httpx.get(
            f"{settings.supabase_url}/auth/v1/settings",
            headers={"apikey": settings.supabase_anon_key},
            timeout=30,
        ).text[:400]
    except Exception as exc:  # noqa: BLE001 -- diagnosis, not control flow
        offered = f"(could not be read: {type(exc).__name__})"
    sys.exit(
        "The seeded reviewer cannot sign in with the anon key, so the live suite would "
        "only report timeouts.\n"
        f"  POST {settings.supabase_url}/auth/v1/token -> {response.status_code}\n"
        f"  {response.text[:400]}\n"
        f"  what this auth server offers: {offered}"
    )


def _delete_auth_user(auth_user_id: str) -> None:
    url, headers = _auth_admin()
    httpx.delete(f"{url}/{auth_user_id}", headers=headers, timeout=30)


# ── Rows ────────────────────────────────────────────────────────────────────


def _create_document(
    tenant_id: UUID,
    *,
    po_number: str,
    order_total: Decimal,
    lines: list[dict],
) -> tuple[UUID, int]:
    document_id = uuid4()
    with platform_session() as session:
        session.execute(
            text(
                """
                INSERT INTO documents
                    (id, tenant_id, original_filename, storage_path, source, status,
                     content_sha256, injection_suspected, raw_json, created_at)
                VALUES
                    (:id, :tenant_id, 'live-e2e-order.txt', 'tenants/live-e2e/order.txt', 'upload',
                     'needs_review', :sha, false, CAST(:raw_json AS jsonb), now())
                """
            ),
            {
                "id": str(document_id),
                "tenant_id": str(tenant_id),
                "sha": uuid4().hex,
                "raw_json": FIXTURE_MODEL_ANSWER,
            },
        )
        session.execute(
            text(
                """
                INSERT INTO document_headers
                    (document_id, tenant_id, po_number, order_date, buyer_name, order_total,
                     currency, header_confidence, currency_inferred, created_at, updated_at)
                VALUES
                    (:document_id, :tenant_id, :po_number, DATE '2026-03-14',
                     'Acme''s Test Coffee House', CAST(:order_total AS numeric), 'USD',
                     CAST(:confidence AS jsonb), false, now(), now())
                """
            ),
            {
                "document_id": str(document_id),
                "tenant_id": str(tenant_id),
                "po_number": po_number,
                "order_total": _money(order_total),
                "confidence": json.dumps(CONFIDENT_HEADER),
            },
        )
        for line in lines:
            session.execute(
                text(
                    """
                    INSERT INTO document_lines
                        (id, document_id, tenant_id, line_number, sku, description, unit,
                         quantity, unit_price, line_total, confidence, uom_mismatch, created_at)
                    VALUES
                        (:id, :document_id, :tenant_id, :line_number, :sku, :description, 'CS',
                         CAST(:quantity AS numeric), CAST(:unit_price AS numeric),
                         CAST(:line_total AS numeric), CAST(:confidence AS numeric), false, now())
                    """
                ),
                {
                    "id": str(uuid4()),
                    "document_id": str(document_id),
                    "tenant_id": str(tenant_id),
                    "line_number": line["line_number"],
                    "sku": line["sku"],
                    "description": line["description"],
                    "quantity": _money(line["quantity"]),
                    "unit_price": _money(line["unit_price"]),
                    "line_total": _money(line["line_total"]),
                    "confidence": _money(line["confidence"]),
                },
            )

    with tenant_session(tenant_id) as session:
        summary = validate_document(session, tenant_id, document_id)
    return document_id, summary.created


def setup(out: Path) -> None:
    # A run that was cancelled between seeding and teardown leaves its file
    # and its rows behind. Clear them before making more: an interrupted run
    # should cost the next one nothing, and staging should never accumulate
    # these (D-160).
    if out.exists():
        print(f"A seed from an earlier run is still here ({out}); removing it first.")
        teardown(out)

    run = secrets.token_hex(3)
    tenant_id = uuid4()
    user_id = uuid4()
    name = f"Acme Test Live E2E {run}"
    email = f"live-e2e-{run}@example.test"
    password = "DocFlow-live-e2e-" + secrets.token_urlsafe(12)

    auth_user_id = _create_auth_user(email, password)
    # From here on the run owns rows. Anything that goes wrong takes them
    # with it rather than leaving a half-built tenant on staging.
    try:
        _seed_rows(out, run, tenant_id, user_id, name, email, password, auth_user_id)
    except BaseException:
        print("Seeding failed; removing what had been created.")
        _remove(tenant_id, auth_user_id)
        out.unlink(missing_ok=True)
        raise


def _seed_rows(
    out: Path,
    run: str,
    tenant_id: UUID,
    user_id: UUID,
    name: str,
    email: str,
    password: str,
    auth_user_id: str,
) -> None:
    with platform_session() as session:
        session.execute(
            text(
                "INSERT INTO tenants "
                "(id, name, status, onboarding_status, created_at, updated_at, status_changed_at) "
                "VALUES (:id, :name, 'active', 'tenant_created', now(), now(), now())"
            ),
            {"id": str(tenant_id), "name": name},
        )
        session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, role, auth_user_id, is_active, "
                "created_at, updated_at) "
                "VALUES (:id, :tenant_id, :email, 'owner', :auth_user_id, true, now(), now())"
            ),
            {
                "id": str(user_id),
                "tenant_id": str(tenant_id),
                "email": email,
                "auth_user_id": auth_user_id,
            },
        )

    # One order that reconciles, so the only check the test ever sees is the
    # one its own edit causes.
    clean_id, clean_warnings = _create_document(
        tenant_id,
        po_number=f"LIVE-{run}-1",
        order_total=Decimal("570.00"),
        lines=[
            {
                "line_number": 1,
                "sku": "CF-1001",
                "description": "Colombian Whole Bean 5lb",
                "quantity": Decimal("12.00"),
                "unit_price": Decimal("47.50"),
                "line_total": Decimal("570.00"),
                "confidence": Decimal("0.97"),
            }
        ],
    )

    # One that already has a check to tick, for the half of the bug where a
    # save wiped the ticks a reviewer had already made.
    flagged_id, flagged_warnings = _create_document(
        tenant_id,
        po_number=f"LIVE-{run}-2",
        order_total=Decimal("900.00"),
        lines=[
            {
                "line_number": 1,
                "sku": "CF-1001",
                "description": "Colombian Whole Bean 5lb",
                "quantity": Decimal("12.00"),
                "unit_price": Decimal("47.50"),
                "line_total": Decimal("570.00"),
                "confidence": Decimal("0.97"),
            }
        ],
    )

    # A third, identical to the one above, so the spec about an
    # acknowledgement being cleared never depends on what an earlier spec
    # left behind.
    ack_id, ack_warnings = _create_document(
        tenant_id,
        po_number=f"LIVE-{run}-3",
        order_total=Decimal("900.00"),
        lines=[
            {
                "line_number": 1,
                "sku": "CF-1001",
                "description": "Colombian Whole Bean 5lb",
                "quantity": Decimal("12.00"),
                "unit_price": Decimal("47.50"),
                "line_total": Decimal("570.00"),
                "confidence": Decimal("0.97"),
            }
        ],
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "tenant_id": str(tenant_id),
                "tenant_name": name,
                "auth_user_id": auth_user_id,
                "email": email,
                "password": password,
                "clean_document_id": str(clean_id),
                "flagged_document_id": str(flagged_id),
                "ack_document_id": str(ack_id),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _check_password_sign_in(email, password)
    print(f"Seeded {name}: clean order has {clean_warnings} checks, flagged order has {flagged_warnings}.")
    if clean_warnings != 0:
        sys.exit("The clean order came back with checks on it -- the live suite needs it clean.")
    if flagged_warnings != 1 or ack_warnings != 1:
        sys.exit(
            f"Each flagged order needs exactly one check, not {flagged_warnings} and {ack_warnings}."
        )


def teardown(out: Path) -> None:
    if not out.exists():
        print(f"No seed file at {out}; nothing to remove.")
        return
    seed = json.loads(out.read_text(encoding="utf-8"))
    _remove(UUID(seed["tenant_id"]), seed["auth_user_id"])
    out.unlink()
    print(f"Removed {seed['tenant_name']}.")


def _remove(tenant_id: UUID, auth_user_id: str | None) -> None:
    """Every row and sign-in the run created. Safe to call twice, and safe
    to call on a tenant that was only half built."""
    tid = str(tenant_id)

    # Dependency order, the same one the Python fixtures unwind in.
    with platform_session() as session:
        for statement in (
            "DELETE FROM exports WHERE tenant_id = :tid",
            "DELETE FROM document_snapshots WHERE tenant_id = :tid",
            "DELETE FROM document_warnings WHERE tenant_id = :tid",
            "DELETE FROM review_actions WHERE tenant_id = :tid",
            "DELETE FROM extraction_runs WHERE tenant_id = :tid",
            "DELETE FROM learned_rules WHERE tenant_id = :tid",
            "DELETE FROM document_lines WHERE tenant_id = :tid",
            "DELETE FROM document_headers WHERE tenant_id = :tid",
            "DELETE FROM buyer_merge_candidates WHERE tenant_id = :tid",
            "DELETE FROM buyers WHERE tenant_id = :tid",
            "DELETE FROM items WHERE tenant_id = :tid",
            "DELETE FROM documents WHERE tenant_id = :tid",
            "DELETE FROM users WHERE tenant_id = :tid",
            "DELETE FROM tenants WHERE id = :tid",
        ):
            session.execute(text(statement), {"tid": tid})

    if auth_user_id:
        _delete_auth_user(auth_user_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("setup", "teardown"))
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    (setup if args.command == "setup" else teardown)(args.out)


if __name__ == "__main__":
    main()
