"""
Buyer identification and auto-creation against the real database
(CLAUDE.md Section 7.6), including the Section 7.5 required test class:
a buyer belonging to Tenant A is never a match candidate for Tenant B.

These run through `tenant_session()` exactly as the worker does, so the RLS
policies added in supabase/migrations/0004_matching_foundations.sql are what
enforces isolation here -- not a WHERE clause the test wrote itself. Setup
and teardown use `platform_session()` (the same pattern as
test_email_intake.py) because a test fixture is not tenant traffic.

Every row created is removed in the context manager's __exit__, whether the
test passed or failed.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from docflow_core.buyers import identify_and_link_buyer, identify_or_create_buyer
from docflow_core.db import platform_session, tenant_session
from sqlalchemy import text

from tests.conftest import requires_matching_schema


class _TestBuyerTenant:
    """A throwaway tenant with helpers to seed documents and read back rows."""

    def __init__(self, name: str):
        self.name = name
        self.tenant_id = uuid4()

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
        return self

    def create_document(self, *, buyer_name: str | None = None, buyer_contact_email: str | None = None):
        """
        Seeds one `documents` row plus its `document_headers` row in the state
        the extraction task leaves them in, so buyer identification has
        something real to link to.
        """
        document_id = uuid4()
        with platform_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO documents
                        (id, tenant_id, original_filename, storage_path, source, status,
                         content_sha256, raw_json, created_at)
                    VALUES
                        (:id, :tenant_id, 'po.txt', 'tenants/seed/po.txt', 'upload', 'needs_review',
                         :sha, '{"header": {}, "line_items": [], "test_fixture": true}'::jsonb, now())
                    """
                ),
                {"id": str(document_id), "tenant_id": str(self.tenant_id), "sha": uuid4().hex},
            )
            session.execute(
                text(
                    """
                    INSERT INTO document_headers
                        (document_id, tenant_id, buyer_name, buyer_contact_email,
                         created_at, updated_at)
                    VALUES
                        (:document_id, :tenant_id, :buyer_name, :buyer_contact_email, now(), now())
                    """
                ),
                {
                    "document_id": str(document_id),
                    "tenant_id": str(self.tenant_id),
                    "buyer_name": buyer_name,
                    "buyer_contact_email": buyer_contact_email,
                },
            )
        return document_id

    def seed_buyer(self, name: str, *, contact_email: str | None = None) -> UUID:
        """An already-known buyer, as a customer-list import would create it."""
        from docflow_core.buyers import normalize_buyer_name

        buyer_id = uuid4()
        with platform_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO buyers
                        (id, tenant_id, name, normalized_name, contact_email, created_at, updated_at)
                    VALUES (:id, :tenant_id, :name, :normalized_name, :contact_email, now(), now())
                    """
                ),
                {
                    "id": str(buyer_id),
                    "tenant_id": str(self.tenant_id),
                    "name": name,
                    "normalized_name": normalize_buyer_name(name),
                    "contact_email": contact_email,
                },
            )
        return buyer_id

    def buyers(self) -> list[dict]:
        with platform_session() as session:
            rows = session.execute(
                text(
                    "SELECT id, name, normalized_name, contact_email, created_from_document_id, "
                    "deleted_at FROM buyers WHERE tenant_id = :tid ORDER BY created_at"
                ),
                {"tid": str(self.tenant_id)},
            ).mappings().all()
        return [dict(r) for r in rows]

    def merge_candidates(self) -> list[dict]:
        with platform_session() as session:
            rows = session.execute(
                text(
                    "SELECT buyer_id, existing_buyer_id, similarity_score, status "
                    "FROM buyer_merge_candidates WHERE tenant_id = :tid ORDER BY created_at"
                ),
                {"tid": str(self.tenant_id)},
            ).mappings().all()
        return [dict(r) for r in rows]

    def header_buyer_id(self, document_id: UUID) -> UUID | None:
        with platform_session() as session:
            row = session.execute(
                text("SELECT buyer_id FROM document_headers WHERE document_id = :did"),
                {"did": str(document_id)},
            ).mappings().first()
        return UUID(str(row["buyer_id"])) if row and row["buyer_id"] else None

    def __exit__(self, *exc):
        tid = str(self.tenant_id)
        with platform_session() as session:
            session.execute(text("DELETE FROM buyer_merge_candidates WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM document_lines WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM document_headers WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM buyers WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM documents WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tid})


@requires_matching_schema
def test_first_sighting_creates_the_buyer_and_links_the_document():
    with _TestBuyerTenant("Acme Test Distributor") as tenant:
        document_id = tenant.create_document(buyer_name="Bella's Coffee House")

        with tenant_session(tenant.tenant_id) as session:
            result = identify_and_link_buyer(
                session, tenant.tenant_id, document_id, buyer_name="Bella's Coffee House"
            )

        assert result.created is True
        assert result.merge_candidates == []

        rows = tenant.buyers()
        assert len(rows) == 1
        # Stored exactly as the document had it -- normalization is a derived
        # key, never a rewrite of the extracted value.
        assert rows[0]["name"] == "Bella's Coffee House"
        assert rows[0]["normalized_name"] == "bellas coffee house"
        assert UUID(str(rows[0]["created_from_document_id"])) == document_id
        assert tenant.header_buyer_id(document_id) == result.buyer_id


@requires_matching_schema
def test_same_name_on_a_second_document_links_instead_of_creating_a_duplicate():
    with _TestBuyerTenant("Acme Test Distributor") as tenant:
        first = tenant.create_document(buyer_name="Bella's Coffee House")
        # Same company, written differently: punctuation, case and a trailing
        # period are noise, not a different buyer.
        second = tenant.create_document(buyer_name="BELLA'S COFFEE HOUSE.")

        with tenant_session(tenant.tenant_id) as session:
            first_result = identify_and_link_buyer(
                session, tenant.tenant_id, first, buyer_name="Bella's Coffee House"
            )
        with tenant_session(tenant.tenant_id) as session:
            second_result = identify_and_link_buyer(
                session, tenant.tenant_id, second, buyer_name="BELLA'S COFFEE HOUSE."
            )

        assert second_result.created is False
        assert second_result.matched_on == "normalized_name"
        assert second_result.buyer_id == first_result.buyer_id
        assert len(tenant.buyers()) == 1
        assert tenant.merge_candidates() == []


@requires_matching_schema
def test_near_duplicate_name_is_flagged_for_merge_and_never_merged():
    """
    CLAUDE.md Section 7.6 / Section 10: "Never auto-merge." Both buyer rows
    must still exist independently, both documents stay linked to their own
    buyer, and the only thing that happened is a flag with a score.
    """
    with _TestBuyerTenant("Acme Test Distributor") as tenant:
        first = tenant.create_document(buyer_name="Bella's Coffee House")
        second = tenant.create_document(buyer_name="Bellas Coffee House LLC")

        with tenant_session(tenant.tenant_id) as session:
            first_result = identify_and_link_buyer(
                session, tenant.tenant_id, first, buyer_name="Bella's Coffee House"
            )
        with tenant_session(tenant.tenant_id) as session:
            second_result = identify_and_link_buyer(
                session, tenant.tenant_id, second, buyer_name="Bellas Coffee House LLC"
            )

        assert second_result.created is True
        assert second_result.buyer_id != first_result.buyer_id

        rows = tenant.buyers()
        assert len(rows) == 2
        assert all(row["deleted_at"] is None for row in rows)
        assert tenant.header_buyer_id(first) == first_result.buyer_id
        assert tenant.header_buyer_id(second) == second_result.buyer_id

        candidates = tenant.merge_candidates()
        assert len(candidates) == 1
        assert UUID(str(candidates[0]["buyer_id"])) == second_result.buyer_id
        assert UUID(str(candidates[0]["existing_buyer_id"])) == first_result.buyer_id
        assert candidates[0]["status"] == "open"
        score = candidates[0]["similarity_score"]
        assert isinstance(score, Decimal)
        assert Decimal("0.88") <= score <= Decimal("1")


@requires_matching_schema
def test_exact_email_match_links_even_when_the_name_differs():
    """
    A shared ordering address is stronger evidence of identity than the name
    printed on the PO, which buyers write inconsistently (DECISIONS.md D-056).
    Nothing is created and nothing is merged.
    """
    with _TestBuyerTenant("Acme Test Distributor") as tenant:
        existing_id = tenant.seed_buyer(
            "Bella's Coffee House", contact_email="orders@bellastest.example"
        )
        document_id = tenant.create_document(
            buyer_name="Bellas Coffee Roasting Co",
            buyer_contact_email="ORDERS@BellasTest.example",
        )

        with tenant_session(tenant.tenant_id) as session:
            result = identify_and_link_buyer(
                session,
                tenant.tenant_id,
                document_id,
                buyer_name="Bellas Coffee Roasting Co",
                buyer_contact_email="ORDERS@BellasTest.example",
            )

        assert result.buyer_id == existing_id
        assert result.created is False
        assert result.matched_on == "contact_email"
        assert len(tenant.buyers()) == 1
        assert tenant.header_buyer_id(document_id) == existing_id


@requires_matching_schema
def test_null_buyer_name_leaves_the_document_unlinked_without_error():
    """
    Every extraction field is nullable (Section 7.1). A PO with no readable
    buyer name is a document with no buyer link -- not a failure, and
    certainly not a buyer invented from nothing.
    """
    with _TestBuyerTenant("Acme Test Distributor") as tenant:
        document_id = tenant.create_document(buyer_name=None)

        with tenant_session(tenant.tenant_id) as session:
            result = identify_and_link_buyer(
                session, tenant.tenant_id, document_id, buyer_name=None, buyer_contact_email=None
            )

        assert result.buyer_id is None
        assert result.created is False
        assert tenant.buyers() == []
        assert tenant.header_buyer_id(document_id) is None


@requires_matching_schema
def test_blank_buyer_name_is_treated_as_no_buyer():
    with _TestBuyerTenant("Acme Test Distributor") as tenant:
        document_id = tenant.create_document(buyer_name="   ")

        with tenant_session(tenant.tenant_id) as session:
            result = identify_and_link_buyer(
                session, tenant.tenant_id, document_id, buyer_name="   "
            )

        assert result.buyer_id is None
        assert tenant.buyers() == []


@requires_matching_schema
def test_tenant_a_buyer_is_never_a_match_candidate_for_tenant_b():
    """
    CLAUDE.md Section 7.5's required test class, applied to buyers: Tenant
    B's document with the identical buyer name must create Tenant B's own
    buyer, must not link to Tenant A's, and must not flag a cross-tenant
    merge candidate. Isolation here is RLS, not a WHERE clause -- both calls
    go through `tenant_session()`.
    """
    with _TestBuyerTenant("Acme Test Distributor") as tenant_a:
        with _TestBuyerTenant("Northwind Test Supply") as tenant_b:
            a_document = tenant_a.create_document(buyer_name="Bella's Coffee House")
            b_document = tenant_b.create_document(buyer_name="Bella's Coffee House")

            with tenant_session(tenant_a.tenant_id) as session:
                a_result = identify_and_link_buyer(
                    session, tenant_a.tenant_id, a_document, buyer_name="Bella's Coffee House"
                )
            with tenant_session(tenant_b.tenant_id) as session:
                b_result = identify_and_link_buyer(
                    session, tenant_b.tenant_id, b_document, buyer_name="Bella's Coffee House"
                )

            assert b_result.created is True
            assert b_result.buyer_id != a_result.buyer_id

            assert len(tenant_a.buyers()) == 1
            assert len(tenant_b.buyers()) == 1
            assert tenant_a.header_buyer_id(a_document) == a_result.buyer_id
            assert tenant_b.header_buyer_id(b_document) == b_result.buyer_id

            # No cross-tenant near-duplicate flag: Tenant A's identically
            # named buyer was never even a candidate.
            assert tenant_a.merge_candidates() == []
            assert tenant_b.merge_candidates() == []

            # And a direct read from Tenant B's session cannot see A's buyer.
            with tenant_session(tenant_b.tenant_id) as session:
                visible = session.execute(
                    text("SELECT id FROM buyers WHERE id = :id"), {"id": str(a_result.buyer_id)}
                ).first()
            assert visible is None


@requires_matching_schema
def test_identify_without_a_document_creates_no_link_but_still_creates_the_buyer():
    """
    `identify_or_create_buyer` is the callable the Console's buyer-import
    screen will use in Phase 5, where there is no source document. It must
    work with `document_id=None` and record no provenance it doesn't have.
    """
    with _TestBuyerTenant("Acme Test Distributor") as tenant:
        with tenant_session(tenant.tenant_id) as session:
            result = identify_or_create_buyer(
                session, tenant.tenant_id, buyer_name="Riverbend Test Hardware"
            )

        assert result.created is True
        rows = tenant.buyers()
        assert len(rows) == 1
        assert rows[0]["created_from_document_id"] is None
