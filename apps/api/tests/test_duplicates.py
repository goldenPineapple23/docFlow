"""
Duplicate and change-order detection against the real database
(CLAUDE.md Section 7.8), including the Phase 2 exit criterion "the same PO
number arriving twice is flagged."

`packages/core/tests/test_duplicates.py` covers `relationship_to` and
`normalize_po_number` as pure functions. This file covers the four functions
that only exist in terms of SQL -- `find_earlier_content_duplicate`,
`find_earlier_same_po_document`, `detect_document_relationships` and
`find_content_duplicate_at_ingest` -- where the thing worth testing is whether
Postgres agrees with Python. It is also the one place the expression index in
0006 and `PO_NUMBER_KEY_SQL` are asserted to mean the same thing.

The assertion this file exists to protect is the earlier-document check in
`_detect`: Section 10 forbids duplicate/change-order handling from overwriting
the earlier document, and detection writes an UPDATE, so every test that runs
detection snapshots the earlier document first and asserts it came back
identical.

Everything runs through `tenant_session()` exactly as the worker does, so the
RLS policies are what enforce isolation here -- not a WHERE clause the test
wrote itself. Setup and teardown use `platform_session()` (the same pattern as
test_validation.py) because a test fixture is not tenant traffic. The tenant
fixture is test_validation.py's, not a second copy of it.

Every row created is removed in the context manager's __exit__, whether the
test passed or failed. All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import UUID

from docflow_core.db import platform_session, tenant_session
from docflow_core.duplicates import (
    PO_NUMBER_KEY_SQL,
    detect_document_relationships,
    find_content_duplicate_at_ingest,
    find_earlier_content_duplicate,
    find_earlier_same_po_document,
    normalize_po_number,
)
from sqlalchemy import text

from tests.conftest import requires_validation_schema
from tests.test_validation import _TestValidationTenant

SHARED_CONTENT = "a" * 64
OTHER_CONTENT = "b" * 64


def _document_flags(document_id: UUID) -> dict:
    with platform_session() as session:
        row = session.execute(
            text(
                "SELECT is_possible_duplicate, duplicate_of_document_id, "
                "is_possible_change_order, change_order_of_document_id "
                "FROM documents WHERE id = :id"
            ),
            {"id": str(document_id)},
        ).mappings().first()
    return dict(row) if row else {}


def _created_at(document_id: UUID):
    with platform_session() as session:
        return session.execute(
            text("SELECT created_at FROM documents WHERE id = :id"),
            {"id": str(document_id)},
        ).scalar_one()


def _detect(tenant: _TestValidationTenant, document_id: UUID, *, earlier: UUID | None = None):
    """
    Runs detection and asserts the earlier document came back untouched.

    Every test goes through here rather than calling
    `detect_document_relationships` directly, so the Section 10 guarantee
    cannot be lost by someone adding a test that forgets to check.
    """
    before = _document_flags(earlier) if earlier else None

    with tenant_session(tenant.tenant_id) as session:
        result = detect_document_relationships(session, tenant.tenant_id, document_id)

    if earlier is not None:
        assert _document_flags(earlier) == before, (
            "detection wrote to the earlier document -- Section 7.8 keeps both, "
            "and only the newer one gains a pointer"
        )
    return result


# -- the Phase 2 exit criterion ---------------------------------------------


@requires_validation_schema
def test_the_same_po_number_arriving_twice_is_flagged_and_neither_document_is_lost():
    """
    The Phase 2 exit criterion. A buyer sends PO BCH-2291, then sends it again
    with a changed quantity. Both documents exist afterwards, the newer one
    points at the older, and nothing about the older one changed.
    """
    with _TestValidationTenant("Acme Test Distributor -- change order") as tenant:
        first = tenant.create_document(
            header={"po_number": "BCH-2291", "order_total": "120.00"},
            lines=[{"quantity": "10", "unit_price": "12.00", "line_total": "120.00"}],
            content_sha256=SHARED_CONTENT,
            created_at_offset_days=-2,
        )
        second = tenant.create_document(
            header={"po_number": "BCH-2291", "order_total": "144.00"},
            lines=[{"quantity": "12", "unit_price": "12.00", "line_total": "144.00"}],
            content_sha256=OTHER_CONTENT,
            created_at_offset_days=-1,
        )

        result = _detect(tenant, second, earlier=first)

        assert result.is_possible_change_order is True
        assert result.change_order_of_document_id == first
        assert result.is_possible_duplicate is False

        flags = _document_flags(second)
        assert flags["is_possible_change_order"] is True
        assert UUID(str(flags["change_order_of_document_id"])) == first

        # Both documents still exist, and the earlier one carries no pointer.
        assert _document_flags(first)["is_possible_change_order"] is False
        assert _document_flags(first)["change_order_of_document_id"] is None


@requires_validation_schema
def test_the_earlier_document_is_never_deleted_or_overwritten_by_detection():
    """Section 10, stated directly rather than only as a side assertion."""
    with _TestValidationTenant("Acme Test Distributor -- keeps both") as tenant:
        first = tenant.create_document(
            header={"po_number": "PO-5000", "order_total": "50.00"},
            content_sha256=SHARED_CONTENT,
            created_at_offset_days=-3,
        )
        header_before = tenant.header(first)

        second = tenant.create_document(
            header={"po_number": "PO-5000", "order_total": "75.00"},
            content_sha256=OTHER_CONTENT,
            created_at_offset_days=-1,
        )
        _detect(tenant, second, earlier=first)

        assert tenant.header(first) == header_before, "detection altered the earlier header"
        with platform_session() as session:
            still_there = session.execute(
                text("SELECT deleted_at FROM documents WHERE id = :id"), {"id": str(first)}
            ).mappings().first()
        assert still_there is not None and still_there["deleted_at"] is None


# -- content duplicates ------------------------------------------------------


@requires_validation_schema
def test_identical_content_is_a_duplicate_and_never_also_a_change_order():
    with _TestValidationTenant("Acme Test Distributor -- resend") as tenant:
        first = tenant.create_document(
            header={"po_number": "PO-6001"},
            content_sha256=SHARED_CONTENT,
            created_at_offset_days=-2,
        )
        resend = tenant.create_document(
            header={"po_number": "PO-6001"},
            content_sha256=SHARED_CONTENT,
            created_at_offset_days=-1,
        )

        result = _detect(tenant, resend, earlier=first)

        assert result.is_possible_duplicate is True
        assert result.duplicate_of_document_id == first
        assert result.is_possible_change_order is False
        assert result.change_order_of_document_id is None


@requires_validation_schema
def test_a_chain_of_resends_all_point_at_the_original():
    """
    D-077: the newest points at the *earliest* match, not at its immediate
    predecessor, so five resends do not form a linked list nobody can follow.
    """
    with _TestValidationTenant("Acme Test Distributor -- chain") as tenant:
        original = tenant.create_document(
            content_sha256=SHARED_CONTENT, created_at_offset_days=-5
        )
        second = tenant.create_document(
            content_sha256=SHARED_CONTENT, created_at_offset_days=-4
        )
        third = tenant.create_document(
            content_sha256=SHARED_CONTENT, created_at_offset_days=-3
        )

        assert _detect(tenant, second, earlier=original).duplicate_of_document_id == original
        assert _detect(tenant, third, earlier=original).duplicate_of_document_id == original


@requires_validation_schema
def test_a_document_is_never_its_own_duplicate():
    with _TestValidationTenant("Acme Test Distributor -- alone") as tenant:
        only = tenant.create_document(content_sha256=SHARED_CONTENT)

        result = _detect(tenant, only)

        assert result.is_possible_duplicate is False
        assert result.duplicate_of_document_id is None


@requires_validation_schema
def test_detection_ignores_a_soft_deleted_earlier_document():
    with _TestValidationTenant("Acme Test Distributor -- soft deleted") as tenant:
        first = tenant.create_document(
            content_sha256=SHARED_CONTENT, created_at_offset_days=-2
        )
        second = tenant.create_document(
            content_sha256=SHARED_CONTENT, created_at_offset_days=-1
        )

        assert _detect(tenant, second, earlier=first).duplicate_of_document_id == first

        with platform_session() as session:
            session.execute(
                text("UPDATE documents SET deleted_at = now() WHERE id = :id"),
                {"id": str(first)},
            )

        # Re-running clears the flag rather than leaving a pointer at
        # something a reviewer can no longer open.
        result = _detect(tenant, second)
        assert result.is_possible_duplicate is False
        assert result.duplicate_of_document_id is None
        assert _document_flags(second)["duplicate_of_document_id"] is None


# -- change orders and the buyer rule ----------------------------------------


@requires_validation_schema
def test_the_same_po_number_from_two_known_different_buyers_is_not_a_relationship():
    """
    "PO-1001" is the thousand-and-first order a business ever placed, and two
    buyers reaching that number independently is ordinary, not suspicious.
    """
    with _TestValidationTenant("Acme Test Distributor -- two buyers") as tenant:
        north = tenant.seed_buyer("Northwind Test Bakery")
        south = tenant.seed_buyer("Southgate Test Grocers")

        first = tenant.create_document(
            header={"po_number": "PO-1001"},
            buyer_id=north,
            content_sha256=SHARED_CONTENT,
            created_at_offset_days=-2,
        )
        second = tenant.create_document(
            header={"po_number": "PO-1001"},
            buyer_id=south,
            content_sha256=OTHER_CONTENT,
            created_at_offset_days=-1,
        )

        result = _detect(tenant, second, earlier=first)

        assert result.is_possible_change_order is False
        assert result.change_order_of_document_id is None


@requires_validation_schema
def test_the_same_po_number_from_the_same_known_buyer_is_a_change_order():
    with _TestValidationTenant("Acme Test Distributor -- same buyer") as tenant:
        buyer = tenant.seed_buyer("Northwind Test Bakery")

        first = tenant.create_document(
            header={"po_number": "PO-1001"},
            buyer_id=buyer,
            content_sha256=SHARED_CONTENT,
            created_at_offset_days=-2,
        )
        second = tenant.create_document(
            header={"po_number": "PO-1001"},
            buyer_id=buyer,
            content_sha256=OTHER_CONTENT,
            created_at_offset_days=-1,
        )

        result = _detect(tenant, second, earlier=first)

        assert result.is_possible_change_order is True
        assert result.change_order_of_document_id == first


@requires_validation_schema
def test_an_unknown_buyer_on_either_side_still_flags():
    """
    D-078: this errs toward surfacing. A false flag costs a reviewer one
    glance at two documents; a missed revision ships the wrong order.
    """
    with _TestValidationTenant("Acme Test Distributor -- unknown buyer") as tenant:
        buyer = tenant.seed_buyer("Northwind Test Bakery")

        # Earlier document's buyer is unknown, newer one's is known.
        first = tenant.create_document(
            header={"po_number": "PO-2002"},
            content_sha256=SHARED_CONTENT,
            created_at_offset_days=-4,
        )
        second = tenant.create_document(
            header={"po_number": "PO-2002"},
            buyer_id=buyer,
            content_sha256=OTHER_CONTENT,
            created_at_offset_days=-3,
        )
        assert _detect(tenant, second, earlier=first).change_order_of_document_id == first

        # And the other way round: the newer document's buyer is unknown.
        third = tenant.create_document(
            header={"po_number": "PO-3003"},
            buyer_id=buyer,
            content_sha256="c" * 64,
            created_at_offset_days=-2,
        )
        fourth = tenant.create_document(
            header={"po_number": "PO-3003"},
            content_sha256="d" * 64,
            created_at_offset_days=-1,
        )
        assert _detect(tenant, fourth, earlier=third).change_order_of_document_id == third


@requires_validation_schema
def test_a_document_with_no_po_number_is_never_a_change_order():
    with _TestValidationTenant("Acme Test Distributor -- no po number") as tenant:
        first = tenant.create_document(
            header={"po_number": None},
            content_sha256=SHARED_CONTENT,
            created_at_offset_days=-2,
        )
        second = tenant.create_document(
            header={"po_number": None},
            content_sha256=OTHER_CONTENT,
            created_at_offset_days=-1,
        )

        result = _detect(tenant, second, earlier=first)

        assert result.is_possible_change_order is False
        assert result.change_order_of_document_id is None


# -- Postgres and Python must agree about the PO key -------------------------


@requires_validation_schema
def test_the_sql_po_key_and_normalize_po_number_agree_on_every_form():
    """
    The equivalence `packages/core/tests/test_duplicates.py` defers to this
    file for. If `PO_NUMBER_KEY_SQL` and `normalize_po_number` ever drift,
    "the same PO number" quietly means two different things -- one in the
    change-order query, one everywhere else in the application.
    """
    variants = [
        "PO-1001",
        "po-1001",
        "  PO-1001  ",
        "PO  1001",
        "po\t1001",
        "PO\n1001",
        "Po-1001-A",
        "1001",
    ]
    key_expression = PO_NUMBER_KEY_SQL.format(column="CAST(:value AS text)")
    with platform_session() as session:
        for value in variants:
            rendered = session.execute(
                text(f"SELECT {key_expression} AS key"), {"value": value}
            ).scalar_one()
            assert rendered == normalize_po_number(value), (
                f"Postgres and Python disagree about {value!r}: "
                f"{rendered!r} vs {normalize_po_number(value)!r}"
            )


@requires_validation_schema
def test_po_numbers_differing_only_in_case_and_spacing_are_the_same_order():
    with _TestValidationTenant("Acme Test Distributor -- po key") as tenant:
        first = tenant.create_document(
            header={"po_number": "  bch  2291 "},
            content_sha256=SHARED_CONTENT,
            created_at_offset_days=-2,
        )
        second = tenant.create_document(
            header={"po_number": "BCH 2291"},
            content_sha256=OTHER_CONTENT,
            created_at_offset_days=-1,
        )

        assert _detect(tenant, second, earlier=first).change_order_of_document_id == first

        # And the stored value is still exactly what each document printed
        # (Section 7.1) -- the key is derived, nothing normalizes in place.
        assert tenant.header(first)["po_number"] == "  bch  2291 "
        assert tenant.header(second)["po_number"] == "BCH 2291"


@requires_validation_schema
def test_the_po_key_keeps_punctuation_because_it_can_be_the_difference():
    with _TestValidationTenant("Acme Test Distributor -- punctuation") as tenant:
        first = tenant.create_document(
            header={"po_number": "PO-1001"},
            content_sha256=SHARED_CONTENT,
            created_at_offset_days=-2,
        )
        second = tenant.create_document(
            header={"po_number": "PO1001"},
            content_sha256=OTHER_CONTENT,
            created_at_offset_days=-1,
        )

        result = _detect(tenant, second, earlier=first)

        assert result.is_possible_change_order is False


# -- idempotency -------------------------------------------------------------


@requires_validation_schema
def test_re_running_detection_reaches_the_same_conclusion_and_writes_the_same_values():
    with _TestValidationTenant("Acme Test Distributor -- idempotent") as tenant:
        first = tenant.create_document(
            header={"po_number": "PO-7007"},
            content_sha256=SHARED_CONTENT,
            created_at_offset_days=-2,
        )
        second = tenant.create_document(
            header={"po_number": "PO-7007"},
            content_sha256=OTHER_CONTENT,
            created_at_offset_days=-1,
        )

        first_run = _detect(tenant, second, earlier=first)
        flags_after_first = _document_flags(second)
        second_run = _detect(tenant, second, earlier=first)

        assert first_run == second_run
        assert _document_flags(second) == flags_after_first


# -- ingest-time detection ---------------------------------------------------


@requires_validation_schema
def test_find_content_duplicate_at_ingest_returns_the_earliest_match():
    """
    Called before the new `documents` row exists, so every existing row is
    earlier and there is no "earlier than me" predicate to apply.
    """
    with _TestValidationTenant("Acme Test Distributor -- ingest") as tenant:
        original = tenant.create_document(
            content_sha256=SHARED_CONTENT, created_at_offset_days=-5
        )
        tenant.create_document(content_sha256=SHARED_CONTENT, created_at_offset_days=-4)

        with tenant_session(tenant.tenant_id) as session:
            found = find_content_duplicate_at_ingest(session, tenant.tenant_id, SHARED_CONTENT)
            missing = find_content_duplicate_at_ingest(session, tenant.tenant_id, "f" * 64)

        assert found == original
        assert missing is None


# -- tenant isolation (CLAUDE.md Section 7.5's required test class) ----------


@requires_validation_schema
def test_tenant_as_identical_file_is_never_a_duplicate_of_tenant_bs():
    """
    Section 7.5's required test, for this slice: the identical bytes uploaded
    by two tenants are two unrelated documents. Enforced by RLS on the
    tenant-scoped session, not by a WHERE clause this test wrote.
    """
    with _TestValidationTenant("Acme Test Distributor A") as tenant_a, _TestValidationTenant(
        "Beacon Test Supply B"
    ) as tenant_b:
        a_document = tenant_a.create_document(
            header={"po_number": "PO-9009"},
            content_sha256=SHARED_CONTENT,
            created_at_offset_days=-3,
        )
        b_document = tenant_b.create_document(
            header={"po_number": "PO-9009"},
            content_sha256=SHARED_CONTENT,
            created_at_offset_days=-1,
        )

        result = _detect(tenant_b, b_document, earlier=a_document)

        assert result.is_possible_duplicate is False, (
            "Tenant B's document was flagged against Tenant A's -- RLS is not holding"
        )
        assert result.is_possible_change_order is False

        with tenant_session(tenant_b.tenant_id) as session:
            assert (
                find_content_duplicate_at_ingest(session, tenant_b.tenant_id, SHARED_CONTENT)
                == b_document
            )
            assert (
                find_earlier_content_duplicate(
                    session,
                    tenant_b.tenant_id,
                    b_document,
                    content_sha256=SHARED_CONTENT,
                    created_at=_created_at(b_document),
                )
                is None
            )
            assert (
                find_earlier_same_po_document(
                    session,
                    tenant_b.tenant_id,
                    b_document,
                    po_key=normalize_po_number("PO-9009"),
                    content_sha256=OTHER_CONTENT,
                    buyer_id=None,
                    created_at=_created_at(b_document),
                )
                is None
            )

        # Tenant A's document is untouched and still unflagged.
        assert _document_flags(a_document)["is_possible_duplicate"] is False
