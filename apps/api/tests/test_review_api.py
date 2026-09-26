"""
The review API (CLAUDE.md Section 7.3, Phase 3 slice 2) against the real
database and the real RLS policies.

`apps/api/tests/test_review.py` proves the review logic itself. This file
proves the HTTP layer around it: that `tenant_id` comes from the session and
never the request, that a `viewer` cannot change anything, that every failure
arrives as a catalog code, and that the document viewer's URL is short-lived,
single-document and single-tenant.

The assertion worth naming is in `test_a_tenant_cannot_read_another_tenants_
document`: Section 7.5's "user in Tenant A requests a Tenant B document by ID
-> 404/403", asserted against the real policies rather than a WHERE clause
this test wrote.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import jwt
import pytest
from docflow_core.config import get_settings
from docflow_core.db import platform_session, tenant_session
from docflow_core.review import approve_document
from docflow_core.storage import save_file
from docflow_core.validation import validate_document
from sqlalchemy import text

from tests.conftest import requires_review_schema
from tests.test_validation import _TestValidationTenant

JWT_SECRET = "test-only-secret-for-ci"
SIGNING_SECRET = "test-only-signing-secret-not-a-real-one"

CLEAN_HEADER = {"po_number": "BCH-2291", "order_total": Decimal("570.00")}
CLEAN_LINES = [
    {
        "quantity": Decimal("12"),
        "unit_price": Decimal("47.50"),
        "line_total": Decimal("570.00"),
        "confidence": Decimal("0.97"),
    }
]


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("DOCUMENT_URL_SIGNING_SECRET", SIGNING_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _ReviewTenant(_TestValidationTenant):
    """
    test_validation's tenant, plus the auth identity the HTTP layer needs.

    Subclassed rather than copied, so there is still one fixture that knows
    how to build a tenant with documents (Section 10's "write a second ..."
    rule applies to test scaffolding too -- two of these would drift).
    """

    def __init__(self, name: str, role: str = "owner"):
        super().__init__(name)
        self.role = role
        self.auth_user_id = str(uuid4())

    def __enter__(self):
        super().__enter__()
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE users SET auth_user_id = :auth_user_id, role = :role, is_active = true "
                    "WHERE id = :id"
                ),
                {
                    "auth_user_id": self.auth_user_id,
                    "role": self.role,
                    "id": str(self.user_id),
                },
            )
        return self

    def headers(self) -> dict:
        claims = {"sub": self.auth_user_id, "email": f"r-{self.user_id.hex[:8]}@example.test",
                  "aud": "authenticated"}
        return {"Authorization": f"Bearer {jwt.encode(claims, JWT_SECRET, algorithm='HS256')}"}


def _status(document_id: UUID) -> str:
    with platform_session() as session:
        return session.execute(
            text("SELECT status FROM documents WHERE id = :id"), {"id": str(document_id)}
        ).scalar_one()


# -- the queue and the detail view -------------------------------------------


@requires_review_schema
def test_the_queue_shows_this_tenants_documents_only(client):
    with _ReviewTenant("Acme Test Distributor A") as a, _ReviewTenant("Beacon Test Supply B") as b:
        mine = a.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        theirs = b.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        response = client.get("/review/documents", headers=a.headers())

        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["documents"]}
        assert str(mine) in ids
        assert str(theirs) not in ids
        # The count is tenant-scoped too: B's document is not in A's total.
        assert response.json()["total"] == 1


@requires_review_schema
def test_paging_through_the_queue_reaches_every_document_exactly_once(client):
    """
    D-097: the queue used to return the first 50 and stop, with no way to see
    the rest. Paging with the returned total must reach every document.
    """
    with _ReviewTenant("Acme Test Distributor -- paging") as tenant:
        created = {str(tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)) for _ in range(5)}

        seen: list[str] = []
        offset = 0
        while True:
            body = client.get(
                f"/review/documents?limit=2&offset={offset}", headers=tenant.headers()
            ).json()
            assert body["total"] == 5
            seen.extend(row["id"] for row in body["documents"])
            offset += 2
            if offset >= body["total"]:
                break

        assert len(seen) == 5
        assert set(seen) == created


@requires_review_schema
def test_the_detail_view_returns_values_confidence_warnings_and_the_trail(client):
    with _ReviewTenant("Acme Test Distributor -- detail") as tenant:
        document = tenant.create_document(
            header={"po_number": "BCH-2291", "order_total": Decimal("100.00")},
            lines=CLEAN_LINES,
        )
        with tenant_session(tenant.tenant_id) as session:
            validate_document(session, tenant.tenant_id, document)

        body = client.get(f"/review/documents/{document}", headers=tenant.headers()).json()

        assert body["document"]["status"] == "needs_review"
        assert body["header"]["po_number"] == "BCH-2291"
        # Money is a string all the way to the client (Section 7.1).
        assert body["header"]["order_total"] == "100.00"
        assert isinstance(body["lines"][0]["quantity"], str)
        assert body["header"]["confidence"]
        assert any(w["code"] == "VAL-002" for w in body["warnings"])
        assert body["version"]
        assert body["can_edit"] is True


@requires_review_schema
def test_opening_a_document_starts_the_review_clock_once(client):
    with _ReviewTenant("Acme Test Distributor -- clock") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        client.get(f"/review/documents/{document}", headers=tenant.headers())
        with platform_session() as session:
            first = session.execute(
                text("SELECT review_started_at FROM documents WHERE id = :id"),
                {"id": str(document)},
            ).scalar_one()
        assert first is not None

        client.get(f"/review/documents/{document}", headers=tenant.headers())
        with platform_session() as session:
            second = session.execute(
                text("SELECT review_started_at FROM documents WHERE id = :id"),
                {"id": str(document)},
            ).scalar_one()
        assert second == first


@requires_review_schema
def test_a_tenant_cannot_read_another_tenants_document(client):
    """Section 7.5's required test, over HTTP."""
    with _ReviewTenant("Acme Test Distributor A") as a, _ReviewTenant("Beacon Test Supply B") as b:
        theirs = b.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        response = client.get(f"/review/documents/{theirs}", headers=a.headers())

        assert response.status_code == 404


@requires_review_schema
def test_an_unauthenticated_request_is_refused(client):
    with _ReviewTenant("Acme Test Distributor -- anon") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        assert client.get(f"/review/documents/{document}").status_code == 401


# -- editing ------------------------------------------------------------------


@requires_review_schema
def test_an_edit_applies_and_returns_the_new_version(client):
    with _ReviewTenant("Acme Test Distributor -- edit") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        before = client.get(f"/review/documents/{document}", headers=tenant.headers()).json()

        response = client.patch(
            f"/review/documents/{document}",
            headers=tenant.headers(),
            json={
                "header": {"po_number": "BCH-2292"},
                "lines": [{"line_id": before["lines"][0]["id"], "fields": {"quantity": "13"}}],
                "expected_version": before["version"],
            },
        )

        assert response.status_code == 200
        assert response.json()["review_action_id"]
        assert response.json()["version"] != before["version"]
        assert tenant.header(document)["po_number"] == "BCH-2292"


@requires_review_schema
def test_editing_a_field_outside_the_allowlist_is_a_catalog_coded_refusal(client):
    """
    A review screen that could edit `status` could approve a document without
    approving it. The refusal is REV-003, not a 500.
    """
    with _ReviewTenant("Acme Test Distributor -- forbidden") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        response = client.patch(
            f"/review/documents/{document}",
            headers=tenant.headers(),
            json={"header": {"status": "approved"}},
        )

        assert response.status_code == 409
        body = response.json()["detail"]
        assert body["code"] == "REV-003"
        assert body["action"]
        assert _status(document) == "needs_review"


@requires_review_schema
def test_a_stale_edit_is_refused_over_http(client):
    with _ReviewTenant("Acme Test Distributor -- stale") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        stale = client.get(f"/review/documents/{document}", headers=tenant.headers()).json()["version"]

        client.patch(
            f"/review/documents/{document}",
            headers=tenant.headers(),
            json={"header": {"po_number": "BCH-0001"}},
        )
        response = client.patch(
            f"/review/documents/{document}",
            headers=tenant.headers(),
            json={"header": {"po_number": "BCH-0002"}, "expected_version": stale},
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "REV-005"
        assert tenant.header(document)["po_number"] == "BCH-0001"


# -- approval -----------------------------------------------------------------


@requires_review_schema
def test_approving_a_clean_document_succeeds(client):
    with _ReviewTenant("Acme Test Distributor -- approve") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        response = client.post(
            f"/review/documents/{document}/approve",
            headers=tenant.headers(),
            json={"acknowledgements": []},
        )

        assert response.status_code == 200
        assert _status(document) == "approved"


@requires_review_schema
def test_approving_with_an_open_warning_is_refused_with_rev_001(client):
    with _ReviewTenant("Acme Test Distributor -- warned") as tenant:
        document = tenant.create_document(
            header={"po_number": "BCH-2291", "order_total": Decimal("100.00")},
            lines=CLEAN_LINES,
        )
        with tenant_session(tenant.tenant_id) as session:
            validate_document(session, tenant.tenant_id, document)

        # The UI asks what approval would refuse on, before offering the button.
        open_warnings = client.get(
            f"/review/documents/{document}/warnings/open", headers=tenant.headers()
        ).json()["warnings"]
        assert any(w["code"] == "VAL-002" for w in open_warnings)

        response = client.post(
            f"/review/documents/{document}/approve",
            headers=tenant.headers(),
            json={"acknowledgements": []},
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "REV-001"
        assert _status(document) == "needs_review"

        # Acknowledging it lets the approval through.
        warning = open_warnings[0]
        ok = client.post(
            f"/review/documents/{document}/approve",
            headers=tenant.headers(),
            json={
                "acknowledgements": [
                    {
                        "warning_id": warning["id"],
                        "code": warning["code"],
                        "text": "The order total doesn't equal the sum of the line totals.",
                        "note": "Freight billed separately.",
                    }
                ]
            },
        )
        assert ok.status_code == 200
        assert _status(document) == "approved"


@requires_review_schema
def test_rejecting_requires_a_note(client):
    with _ReviewTenant("Acme Test Distributor -- reject") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        assert client.post(
            f"/review/documents/{document}/reject", headers=tenant.headers(), json={"note": ""}
        ).status_code == 422

        ok = client.post(
            f"/review/documents/{document}/reject",
            headers=tenant.headers(),
            json={"note": "Duplicate of yesterday's order."},
        )
        assert ok.status_code == 200
        assert _status(document) == "rejected"


@requires_review_schema
def test_reopening_over_http_sends_a_decided_order_back_to_review(client):
    """D-144: the screen's "Reopen for review" button, through the API."""
    with _ReviewTenant("Acme Test Distributor -- reopen api") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with tenant_session(tenant.tenant_id) as session:
            approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)

        ok = client.post(f"/review/documents/{document}/reopen", headers=tenant.headers())
        assert ok.status_code == 200
        assert _status(document) == "needs_review"

        # An order already in review has nothing to reopen: a catalog code.
        again = client.post(f"/review/documents/{document}/reopen", headers=tenant.headers())
        assert again.status_code >= 400
        assert again.json()["detail"]["code"] == "REV-004"


@requires_review_schema
def test_a_viewer_cannot_reopen(client):
    with _ReviewTenant("Acme Test Distributor -- reopen viewer", role="viewer") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with tenant_session(tenant.tenant_id) as session:
            approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)

        response = client.post(f"/review/documents/{document}/reopen", headers=tenant.headers())
        assert response.status_code == 403
        assert _status(document) == "approved"


def _failed(tenant, raw_json: str | None) -> UUID:
    return tenant.create_document(
        header=CLEAN_HEADER, lines=CLEAN_LINES, status="failed", raw_json=raw_json
    )


@requires_review_schema
def test_a_failed_order_says_why_in_the_catalogs_words(client):
    """
    D-145: a reviewer used to see only "Couldn't be read". The reason is the
    code the worker recorded -- on the document when it stopped before the
    model, on the extraction run when the model call failed -- rendered from
    the catalog, never the raw cause.
    """
    with _ReviewTenant("Acme Test Distributor -- failure reason") as tenant:
        # Created failed: needs_review -> failed isn't a path (migration 0027).
        converted = _failed(tenant, '{"error_code": "DOC-017", "detail": "LibreOffice exited 1"}')

        model = _failed(tenant, None)
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO extraction_runs (tenant_id, document_id, succeeded, error_code) "
                    "VALUES (:t, :d, false, 'DOC-009')"
                ),
                {"t": str(tenant.tenant_id), "d": str(model)},
            )

        unknown = _failed(tenant, None)

        fine = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        def failure(document_id):
            response = client.get(f"/review/documents/{document_id}", headers=tenant.headers())
            assert response.status_code == 200
            return response.json()["document"]["failure"]

        got = failure(converted)
        assert got["code"] == "DOC-017"
        assert got["title"] and got["message"] and got["action"]
        # The raw cause stays in the log (Section 7.16.5).
        assert "LibreOffice" not in str(got)

        assert failure(model)["code"] == "DOC-009"
        assert failure(unknown)["code"] == "DOC-008"
        assert failure(fine) is None


# -- roles are enforced at the API, not in the UI (Section 3) ----------------


@requires_review_schema
def test_a_viewer_can_read_but_cannot_edit_or_approve(client):
    with _ReviewTenant("Acme Test Distributor -- viewer", role="viewer") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        detail = client.get(f"/review/documents/{document}", headers=tenant.headers())
        assert detail.status_code == 200
        assert detail.json()["can_edit"] is False

        edit = client.patch(
            f"/review/documents/{document}",
            headers=tenant.headers(),
            json={"header": {"po_number": "BCH-9999"}},
        )
        assert edit.status_code == 403
        assert edit.json()["detail"]["code"] == "AUTH-002"

        approve = client.post(
            f"/review/documents/{document}/approve",
            headers=tenant.headers(),
            json={"acknowledgements": []},
        )
        assert approve.status_code == 403

        assert tenant.header(document)["po_number"] == "BCH-2291"
        assert _status(document) == "needs_review"


@requires_review_schema
def test_a_viewer_opening_a_document_does_not_start_the_review_clock(client):
    """The KPI measures a reviewer's time. Someone browsing history should
    not start a clock that a reviewer is later measured against."""
    with _ReviewTenant("Acme Test Distributor -- viewer clock", role="viewer") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        client.get(f"/review/documents/{document}", headers=tenant.headers())

        with platform_session() as session:
            started = session.execute(
                text("SELECT review_started_at FROM documents WHERE id = :id"),
                {"id": str(document)},
            ).scalar_one()
        assert started is None


# -- catalog search and the learning step ------------------------------------


@requires_review_schema
def test_item_search_finds_this_tenants_catalog_only(client):
    with _ReviewTenant("Acme Test Distributor A") as a, _ReviewTenant("Beacon Test Supply B") as b:
        a.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        b.seed_item("CF-1001", "Colombian Whole Bean 5lb")

        results = client.get("/review/items", params={"q": "Colombian"}, headers=a.headers()).json()

        assert len(results["items"]) == 1
        assert results["items"][0]["sku"] == "CF-1001"


@requires_review_schema
def test_confirming_a_mapping_creates_a_learned_rule(client):
    with _ReviewTenant("Acme Test Distributor -- mapping") as tenant:
        item_id = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        line_id = tenant.lines(document)[0]["id"]

        response = client.post(
            f"/review/documents/{document}/mapping",
            headers=tenant.headers(),
            json={"line_id": str(line_id), "item_id": str(item_id)},
        )

        assert response.status_code == 200
        with platform_session() as session:
            rule = session.execute(
                text(
                    "SELECT status, confirmed_by FROM learned_rules "
                    "WHERE tenant_id = :tid AND rule_type = 'sku_mapping'"
                ),
                {"tid": str(tenant.tenant_id)},
            ).mappings().first()
        # Section 10: no rule activates without a human confirmation.
        assert rule is not None
        assert rule["status"] == "active"
        assert rule["confirmed_by"] == tenant.user_id


@requires_review_schema
def test_a_mapping_for_a_line_on_another_document_is_refused(client):
    with _ReviewTenant("Acme Test Distributor -- wrong line") as tenant:
        item_id = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        one = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        two = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        line_on_two = tenant.lines(two)[0]["id"]

        response = client.post(
            f"/review/documents/{one}/mapping",
            headers=tenant.headers(),
            json={"line_id": str(line_on_two), "item_id": str(item_id)},
        )

        assert response.status_code == 404


# -- the document viewer's signed URL ----------------------------------------


@requires_review_schema
def test_the_viewer_url_is_minted_then_served_with_a_strict_csp(client):
    with _ReviewTenant("Acme Test Distributor -- viewer url") as tenant:
        content = b"PO Number: BCH-2291\n"
        storage_path = save_file(tenant.tenant_id, "po.txt", content)
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with platform_session() as session:
            session.execute(
                text("UPDATE documents SET storage_path = :p WHERE id = :id"),
                {"p": storage_path, "id": str(document)},
            )

        minted = client.get(
            f"/review/documents/{document}/original", headers=tenant.headers()
        ).json()

        # Section 7.4: the storage path is never user-visible.
        assert "tenants/" not in minted["url"]
        assert storage_path not in minted["url"]

        served = client.get(minted["url"], headers=tenant.headers())
        assert served.status_code == 200
        assert served.content == content
        csp = served.headers["content-security-policy"]
        assert "script-src 'none'" in csp
        assert "object-src 'none'" in csp
        assert served.headers["x-content-type-options"] == "nosniff"


@requires_review_schema
def test_a_tenant_cannot_mint_a_viewer_url_for_another_tenants_document(client):
    """
    Section 7.5, at the point where it is enforceable.

    The content route authenticates from the signed token alone, because the
    viewer is an iframe and an iframe sends no Authorization header (D-089).
    So the boundary that matters is *minting*: Tenant B cannot obtain a URL
    for Tenant A's document, because RLS hides the document from the mint
    route entirely.
    """
    with _ReviewTenant("Acme Test Distributor A") as a, _ReviewTenant("Beacon Test Supply B") as b:
        document = a.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        refused = client.get(f"/review/documents/{document}/original", headers=b.headers())

        assert refused.status_code == 404


@requires_review_schema
def test_a_viewer_token_edited_to_name_another_tenant_is_refused(client):
    """
    The tenant travels inside the token, so the guarantee is that it is
    covered by the signature: editing it invalidates the token, and minting
    a fresh one needs the signing secret.

    **What this deliberately does not claim:** a signed URL that is passed to
    someone else still works until it expires. That is what a signed URL is,
    and Section 7.4 asks for exactly that -- possession for a few minutes is
    the access grant. The mitigations are the short TTL, the unguessable
    signature, and that the storage path never appears in it.
    """
    with _ReviewTenant("Acme Test Distributor A") as a, _ReviewTenant("Beacon Test Supply B") as b:
        document = a.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        url = client.get(
            f"/review/documents/{document}/original", headers=a.headers()
        ).json()["url"]

        token = url.split("token=")[1]
        _tenant, rest = token.split(".", 1)
        forged = f"{b.tenant_id}.{rest}"

        replayed = client.get(url.split("?")[0] + f"?token={forged}")

        assert replayed.status_code == 404


@requires_review_schema
def test_a_forged_viewer_token_is_refused(client):
    with _ReviewTenant("Acme Test Distributor -- forged") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        response = client.get(
            f"/review/documents/{document}/original/content",
            params={"token": "9999999999.not-a-real-signature"},
            headers=tenant.headers(),
        )

        assert response.status_code == 404


# -- the approved document is no longer editable as if it were new -----------


@requires_review_schema
def test_approving_twice_over_http_is_rev_002(client):
    with _ReviewTenant("Acme Test Distributor -- twice") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with tenant_session(tenant.tenant_id) as session:
            approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)

        response = client.post(
            f"/review/documents/{document}/approve",
            headers=tenant.headers(),
            json={"acknowledgements": []},
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "REV-002"
