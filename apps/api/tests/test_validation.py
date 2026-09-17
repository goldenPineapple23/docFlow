"""
Validation against the real database (CLAUDE.md Section 7.7), including the
Section 7.5 required test class and the Phase 2 exit criterion "totals that
don't reconcile produce a warning and are not silently corrected."

The single most important assertion in this file is
`_assert_extracted_values_unchanged`: every test that runs validation snapshots
the stored header and line values first, runs validation, and asserts they came
back identical. Section 10 forbids altering a model-extracted value to make
validation pass, and an assertion is the only thing that keeps that true after
the next person edits this module.

Everything runs through `tenant_session()` exactly as the worker does, so the
RLS policies are what enforce isolation here -- not a WHERE clause the test
wrote itself. Setup and teardown use `platform_session()` (the same pattern as
test_matching.py) because a test fixture is not tenant traffic.

Every row created is removed in the context manager's __exit__, whether the
test passed or failed. All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID, uuid4

from docflow_core.buyers import normalize_buyer_name
from docflow_core.db import platform_session, tenant_session
from docflow_core.validation import (
    CODE_HEADER_TOTAL_MISMATCH,
    CODE_INJECTION_SUSPECTED,
    CODE_INVALID_CURRENCY,
    CODE_LINE_TOTAL_MISMATCH,
    CODE_LOW_CONFIDENCE,
    CODE_MISSING_REQUIRED_FIELD,
    CODE_NON_POSITIVE_QUANTITY,
    CODE_POSSIBLE_CHANGE_ORDER,
    CODE_POSSIBLE_DUPLICATE,
    CODE_UOM_MISMATCH,
    evaluate_document,
    load_snapshot,
    open_warnings,
    unacknowledged_warning_count,
    validate_document,
)
from sqlalchemy import text

from tests.conftest import requires_validation_schema, review_schema_available


class _TestValidationTenant:
    """A throwaway tenant with documents, headers and lines."""

    def __init__(self, name: str):
        self.name = name
        self.tenant_id = uuid4()
        self.user_id = uuid4()

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
                    "INSERT INTO users (id, tenant_id, email, role, created_at, updated_at) "
                    "VALUES (:id, :tenant_id, :email, 'owner', now(), now())"
                ),
                {
                    "id": str(self.user_id),
                    "tenant_id": str(self.tenant_id),
                    "email": f"reviewer-{self.user_id.hex[:8]}@example.test",
                },
            )
        return self

    def seed_item(self, sku: str, description: str | None, uom: str | None = "CS") -> UUID:
        item_id = uuid4()
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO items (id, tenant_id, sku, description, unit_of_measure, "
                    "created_at, updated_at) "
                    "VALUES (:id, :tenant_id, :sku, :description, :uom, now(), now())"
                ),
                {
                    "id": str(item_id),
                    "tenant_id": str(self.tenant_id),
                    "sku": sku,
                    "description": description,
                    "uom": uom,
                },
            )
        return item_id

    def seed_buyer(self, name: str, contact_email: str | None = None) -> UUID:
        buyer_id = uuid4()
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO buyers "
                    "(id, tenant_id, name, normalized_name, contact_email, created_at, updated_at) "
                    "VALUES (:id, :tenant_id, :name, :normalized_name, :contact_email, now(), now())"
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

    def create_document(
        self,
        *,
        header: dict | None = None,
        buyer_id: UUID | None = None,
        lines: list[dict] | None = None,
        header_confidence: dict | None = None,
        injection_suspected: bool = False,
        currency_inferred: bool = False,
        created_at_offset_days: int = 0,
        content_sha256: str | None = None,
    ) -> UUID:
        document_id = uuid4()
        header_values = {
            "po_number": "BCH-2291",
            "order_date": "2025-05-28",
            "requested_delivery_date": None,
            "buyer_name": "Bella's Test Coffee House",
            "buyer_contact_email": None,
            "ship_to_address": None,
            "payment_terms": None,
            "order_total": Decimal("100.00"),
            "currency": "USD",
            "notes": None,
        }
        header_values.update(header or {})

        with platform_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO documents
                        (id, tenant_id, original_filename, storage_path, source, status,
                         content_sha256, injection_suspected, created_at)
                    VALUES
                        (:id, :tenant_id, 'po.txt', 'tenants/seed/po.txt', 'upload', 'needs_review',
                         :sha, :injection_suspected, now() + make_interval(days => :offset))
                    """
                ),
                {
                    "id": str(document_id),
                    "tenant_id": str(self.tenant_id),
                    "sha": content_sha256 or uuid4().hex,
                    "injection_suspected": injection_suspected,
                    "offset": created_at_offset_days,
                },
            )
            session.execute(
                text(
                    """
                    INSERT INTO document_headers
                        (document_id, tenant_id, po_number, order_date, requested_delivery_date,
                         buyer_name, buyer_contact_email, ship_to_address, payment_terms,
                         order_total, currency, notes, header_confidence, currency_inferred,
                         buyer_id, created_at, updated_at)
                    VALUES
                        (:document_id, :tenant_id, :po_number, :order_date, :requested_delivery_date,
                         :buyer_name, :buyer_contact_email, :ship_to_address, :payment_terms,
                         :order_total, :currency, :notes, :header_confidence, :currency_inferred,
                         :buyer_id, now(), now())
                    """
                ),
                {
                    "document_id": str(document_id),
                    "tenant_id": str(self.tenant_id),
                    **{
                        key: (str(value) if isinstance(value, Decimal) else value)
                        for key, value in header_values.items()
                    },
                    "header_confidence": header_confidence or {"po_number": 0.98},
                    "currency_inferred": currency_inferred,
                    "buyer_id": str(buyer_id) if buyer_id else None,
                },
            )
            for number, line in enumerate(lines or [], start=1):
                session.execute(
                    text(
                        """
                        INSERT INTO document_lines
                            (id, document_id, tenant_id, line_number, sku, description, unit,
                             quantity, unit_price, line_total, confidence, matched_item_id,
                             match_method, match_score, uom_mismatch, created_at)
                        VALUES
                            (:id, :document_id, :tenant_id, :line_number, :sku, :description, :unit,
                             :quantity, :unit_price, :line_total, :confidence, :matched_item_id,
                             :match_method, :match_score, :uom_mismatch, now())
                        """
                    ),
                    {
                        "id": str(line.get("id") or uuid4()),
                        "document_id": str(document_id),
                        "tenant_id": str(self.tenant_id),
                        "line_number": line.get("line_number", number),
                        "sku": line.get("sku", "CF-1001"),
                        "description": line.get("description", "Colombian Whole Bean 5lb"),
                        "unit": line.get("unit"),
                        "quantity": _as_text(line.get("quantity")),
                        "unit_price": _as_text(line.get("unit_price")),
                        "line_total": _as_text(line.get("line_total")),
                        "confidence": _as_text(line.get("confidence")),
                        "matched_item_id": (
                            str(line["matched_item_id"]) if line.get("matched_item_id") else None
                        ),
                        "match_method": "exact_sku" if line.get("matched_item_id") else None,
                        "match_score": "1.0000" if line.get("matched_item_id") else None,
                        "uom_mismatch": line.get("uom_mismatch", False),
                    },
                )
        return document_id

    def header(self, document_id: UUID) -> dict:
        with platform_session() as session:
            row = session.execute(
                text(
                    "SELECT po_number, order_date, requested_delivery_date, buyer_name, "
                    "order_total, currency, notes, header_confidence, currency_inferred "
                    "FROM document_headers WHERE document_id = :id"
                ),
                {"id": str(document_id)},
            ).mappings().first()
        return dict(row) if row else {}

    def lines(self, document_id: UUID) -> list[dict]:
        with platform_session() as session:
            rows = session.execute(
                text(
                    "SELECT id, line_number, sku, description, unit, quantity, unit_price, "
                    "line_total, confidence, uom_mismatch FROM document_lines "
                    "WHERE document_id = :id ORDER BY line_number"
                ),
                {"id": str(document_id)},
            ).mappings().all()
        return [dict(row) for row in rows]

    def warnings(self, document_id: UUID) -> list[dict]:
        with platform_session() as session:
            rows = session.execute(
                text(
                    "SELECT id, code, severity, field_name, line_number, document_line_id, detail, "
                    "fingerprint, status, acknowledged_by, resolved_at, deleted_at "
                    "FROM document_warnings WHERE document_id = :id "
                    "ORDER BY code, line_number NULLS FIRST, created_at"
                ),
                {"id": str(document_id)},
            ).mappings().all()
        return [dict(row) for row in rows]

    def live_warnings(self, document_id: UUID) -> list[dict]:
        return [w for w in self.warnings(document_id) if w["deleted_at"] is None]

    def __exit__(self, *exc):
        tid = str(self.tenant_id)
        with platform_session() as session:
            # 0007's tables, when it has been applied. Snapshots reference
            # review_actions and warnings reference them too, so both go
            # before review_actions itself.
            if review_schema_available():
                session.execute(
                    text("DELETE FROM document_snapshots WHERE tenant_id = :tid"), {"tid": tid}
                )
            session.execute(text("DELETE FROM document_warnings WHERE tenant_id = :tid"), {"tid": tid})
            if review_schema_available():
                session.execute(
                    text("DELETE FROM review_actions WHERE tenant_id = :tid"), {"tid": tid}
                )
            session.execute(text("DELETE FROM learned_rules WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM document_lines WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM document_headers WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(
                text("DELETE FROM buyer_merge_candidates WHERE tenant_id = :tid"), {"tid": tid}
            )
            session.execute(text("DELETE FROM buyers WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM items WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(
                text("UPDATE documents SET duplicate_of_document_id = NULL, "
                     "change_order_of_document_id = NULL WHERE tenant_id = :tid"),
                {"tid": tid},
            )
            session.execute(text("DELETE FROM documents WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM users WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tid})


def _as_text(value) -> str | None:
    """Every number reaches a NUMERIC column as a string, never a float
    (CLAUDE.md Section 7.1) -- including in test fixtures."""
    return str(value) if value is not None else None


def _codes(warnings: list[dict]) -> list[str]:
    return [w["code"] for w in warnings]


def _detail(warning: dict) -> dict:
    value = warning["detail"]
    return json.loads(value) if isinstance(value, str) else value


def _run_validation_preserving_values(tenant: _TestValidationTenant, document_id: UUID):
    """
    Runs validation and asserts the extracted values are identical afterwards.

    This is the assertion the whole slice exists to protect (CLAUDE.md Section
    10: "never alter a model-extracted value to make validation pass"). Every
    test that validates a document goes through here rather than calling
    `validate_document` directly, so the guarantee cannot be lost by someone
    adding a test that forgets to check.
    """
    header_before = tenant.header(document_id)
    lines_before = tenant.lines(document_id)

    with tenant_session(tenant.tenant_id) as session:
        summary = validate_document(session, tenant.tenant_id, document_id)

    assert tenant.header(document_id) == header_before, (
        "validation altered an extracted header value"
    )
    assert tenant.lines(document_id) == lines_before, "validation altered an extracted line value"
    return summary


# ── the Phase 2 exit criterion ──────────────────────────────────────────────


@requires_validation_schema
def test_a_total_that_does_not_reconcile_warns_and_is_never_silently_corrected():
    """
    **The Phase 2 exit criterion:** "totals that don't reconcile produce a
    warning and are not silently corrected."

    The document below says the order is $4,300 while its one line says $300.
    Both numbers stay exactly as extracted; a `high`-severity warning names
    both and the difference between them, and the human decides which is
    right (CLAUDE.md Section 7.7).
    """
    with _TestValidationTenant("Acme Test Distributor") as tenant:
        document = tenant.create_document(
            header={"order_total": Decimal("4300.00")},
            lines=[
                {
                    "quantity": Decimal("10"),
                    "unit_price": Decimal("30.00"),
                    "line_total": Decimal("300.00"),
                }
            ],
        )

        summary = _run_validation_preserving_values(tenant, document)
        assert summary.created == 1

        warnings = tenant.live_warnings(document)
        assert _codes(warnings) == [CODE_HEADER_TOTAL_MISMATCH]
        assert warnings[0]["severity"] == "high"
        assert warnings[0]["field_name"] == "order_total"

        detail = _detail(warnings[0])
        assert detail["order_total"] == "4300.00"
        assert detail["sum_of_lines"] == "300.00"
        assert detail["difference"] == "4000.00"

        # Explicitly: the extracted values are still the ones the model read.
        assert tenant.header(document)["order_total"] == Decimal("4300.00")
        assert tenant.lines(document)[0]["line_total"] == Decimal("300.00")


@requires_validation_schema
def test_a_forty_line_document_that_rounds_legitimately_produces_no_warning():
    """
    The failure mode a reconciliation rule dies of: firing on every correct
    40-line purchase order until reviewers learn to ignore it.

    Forty lines of one unit at a true 0.125 each, printed rounded to 0.13.
    The lines sum to 5.20; the buyer's system computed 5.00 from the unrounded
    price. Nobody made a mistake, so nobody is warned.
    """
    with _TestValidationTenant("Acme Test Distributor") as tenant:
        document = tenant.create_document(
            header={"order_total": Decimal("5.00")},
            lines=[
                {
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("0.1250"),
                    "line_total": Decimal("0.13"),
                    "confidence": Decimal("0.99"),
                }
                for _ in range(40)
            ],
        )

        summary = _run_validation_preserving_values(tenant, document)
        assert summary.created == 0
        assert tenant.live_warnings(document) == []


# ── each rule, against real rows ────────────────────────────────────────────


@requires_validation_schema
def test_a_line_total_that_does_not_multiply_out_is_warned_not_fixed():
    with _TestValidationTenant("Acme Test Distributor") as tenant:
        document = tenant.create_document(
            header={"order_total": Decimal("470.00")},
            lines=[
                {
                    "quantity": Decimal("12"),
                    "unit_price": Decimal("47.50"),
                    "line_total": Decimal("470.00"),
                }
            ],
        )

        _run_validation_preserving_values(tenant, document)

        warnings = tenant.live_warnings(document)
        line_warnings = [w for w in warnings if w["code"] == CODE_LINE_TOTAL_MISMATCH]
        assert len(line_warnings) == 1
        assert line_warnings[0]["line_number"] == 1
        assert line_warnings[0]["document_line_id"] is not None
        detail = _detail(line_warnings[0])
        assert detail["expected"] == "570.00"
        assert detail["line_total"] == "470.00"
        # And the stored number is still what the document printed.
        assert tenant.lines(document)[0]["line_total"] == Decimal("470.00")


@requires_validation_schema
def test_a_negative_quantity_a_bad_currency_and_a_missing_po_number_are_all_reported():
    with _TestValidationTenant("Acme Test Distributor") as tenant:
        document = tenant.create_document(
            header={"po_number": None, "currency": "US$", "order_total": None},
            lines=[
                {
                    "quantity": Decimal("-4"),
                    "unit_price": Decimal("10.00"),
                    "line_total": Decimal("-40.00"),
                }
            ],
        )

        _run_validation_preserving_values(tenant, document)

        warnings = tenant.live_warnings(document)
        codes = set(_codes(warnings))
        assert CODE_NON_POSITIVE_QUANTITY in codes
        assert CODE_INVALID_CURRENCY in codes
        assert CODE_MISSING_REQUIRED_FIELD in codes

        missing = [w for w in warnings if w["code"] == CODE_MISSING_REQUIRED_FIELD]
        by_field = {w["field_name"]: w for w in missing}
        assert by_field["po_number"]["severity"] == "high"
        assert by_field["order_total"]["severity"] == "warning"


@requires_validation_schema
def test_an_injection_flag_becomes_a_high_severity_warning_row():
    with _TestValidationTenant("Acme Test Distributor") as tenant:
        document = tenant.create_document(
            injection_suspected=True,
            lines=[
                {
                    "quantity": Decimal("10"),
                    "unit_price": Decimal("10.00"),
                    "line_total": Decimal("100.00"),
                }
            ],
        )

        _run_validation_preserving_values(tenant, document)

        warnings = [w for w in tenant.live_warnings(document) if w["code"] == CODE_INJECTION_SUSPECTED]
        assert len(warnings) == 1
        assert warnings[0]["severity"] == "high"
        assert warnings[0]["document_line_id"] is None


@requires_validation_schema
def test_a_uom_mismatch_from_the_matching_slice_becomes_a_warning_row():
    """DECISIONS.md D-070 deferred this here: slice 2's boolean becomes a
    first-class, acknowledgeable warning, and still nothing is corrected."""
    with _TestValidationTenant("Acme Test Distributor") as tenant:
        item = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb", uom="CASE")
        document = tenant.create_document(
            lines=[
                {
                    "quantity": Decimal("10"),
                    "unit_price": Decimal("10.00"),
                    "line_total": Decimal("100.00"),
                    "unit": "EA",
                    "matched_item_id": item,
                    "uom_mismatch": True,
                }
            ],
        )

        _run_validation_preserving_values(tenant, document)

        warnings = [w for w in tenant.live_warnings(document) if w["code"] == CODE_UOM_MISMATCH]
        assert len(warnings) == 1
        detail = _detail(warnings[0])
        assert detail["unit"] == "EA"
        assert detail["catalog_unit_of_measure"] == "CASE"
        assert tenant.lines(document)[0]["unit"] == "EA", "the extracted unit must never be rewritten"


@requires_validation_schema
def test_low_confidence_is_flagged_per_field_and_per_line():
    with _TestValidationTenant("Acme Test Distributor") as tenant:
        document = tenant.create_document(
            header_confidence={"po_number": 0.42, "order_total": 0.99},
            lines=[
                {
                    "quantity": Decimal("10"),
                    "unit_price": Decimal("10.00"),
                    "line_total": Decimal("100.00"),
                    "confidence": Decimal("0.310"),
                }
            ],
        )

        _run_validation_preserving_values(tenant, document)

        low = [w for w in tenant.live_warnings(document) if w["code"] == CODE_LOW_CONFIDENCE]
        assert len(low) == 2
        assert {w["field_name"] for w in low} == {"po_number", None}


@requires_validation_schema
def test_a_document_whose_every_field_is_null_never_crashes_and_reports_only_what_is_missing():
    """
    Every extraction field is nullable by design (Section 7.1). A document
    the model could read almost nothing from must produce required-field
    warnings and nothing spurious -- no reconciliation warning against a total
    that isn't there, no date warning about a date that wasn't printed.
    """
    with _TestValidationTenant("Acme Test Distributor") as tenant:
        document = tenant.create_document(
            header={
                "po_number": None,
                "order_date": None,
                "requested_delivery_date": None,
                "buyer_name": None,
                "order_total": None,
                "currency": None,
            },
            header_confidence={},
            lines=[
                {
                    "sku": None,
                    "description": None,
                    "quantity": None,
                    "unit_price": None,
                    "line_total": None,
                    "confidence": None,
                }
            ],
        )

        _run_validation_preserving_values(tenant, document)

        warnings = tenant.live_warnings(document)
        assert set(_codes(warnings)) == {CODE_MISSING_REQUIRED_FIELD}
        fields = {w["field_name"] for w in warnings}
        assert fields == {
            "po_number",
            "buyer_name",
            "order_total",
            "currency",
            "sku_or_description",
            "quantity",
            "line_total",
        }


@requires_validation_schema
def test_a_document_with_no_lines_at_all_is_handled():
    with _TestValidationTenant("Acme Test Distributor") as tenant:
        document = tenant.create_document(lines=[])
        _run_validation_preserving_values(tenant, document)
        # Nothing to reconcile against, so no reconciliation warning.
        assert CODE_HEADER_TOTAL_MISMATCH not in _codes(tenant.live_warnings(document))


# ── idempotent re-validation ────────────────────────────────────────────────


@requires_validation_schema
def test_re_running_validation_neither_duplicates_a_warning_nor_discards_an_acknowledgement():
    """
    CLAUDE.md Section 7.3 records an acknowledgement against a warning;
    Section 10 forbids overwriting a human's action with a machine value.
    Re-processing a document (a re-extraction, a matching re-run) must
    therefore leave an acknowledged warning exactly where it was.
    """
    with _TestValidationTenant("Acme Test Distributor") as tenant:
        document = tenant.create_document(
            header={"order_total": Decimal("4300.00")},
            lines=[
                {
                    "quantity": Decimal("10"),
                    "unit_price": Decimal("30.00"),
                    "line_total": Decimal("300.00"),
                }
            ],
        )

        first = _run_validation_preserving_values(tenant, document)
        assert first.created == 1
        warning_id = tenant.live_warnings(document)[0]["id"]

        # A human acknowledges it (the UI that does this is Phase 3).
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE document_warnings SET status = 'acknowledged', "
                    "acknowledged_by = :user_id, acknowledged_at = now(), updated_at = now() "
                    "WHERE id = :id"
                ),
                {"id": str(warning_id), "user_id": str(tenant.user_id)},
            )

        second = _run_validation_preserving_values(tenant, document)
        assert second.created == 0
        assert second.retained == 1
        assert second.resolved == 0

        warnings = tenant.live_warnings(document)
        assert len(warnings) == 1, "re-validation duplicated a warning"
        assert warnings[0]["id"] == warning_id
        assert warnings[0]["status"] == "acknowledged"
        assert UUID(str(warnings[0]["acknowledged_by"])) == tenant.user_id
        assert unacknowledged_warning_count_for(tenant, document) == 0


def unacknowledged_warning_count_for(tenant: _TestValidationTenant, document_id: UUID) -> int:
    with tenant_session(tenant.tenant_id) as session:
        return unacknowledged_warning_count(session, document_id)


@requires_validation_schema
def test_a_corrected_value_resolves_the_warning_without_destroying_its_history():
    with _TestValidationTenant("Acme Test Distributor") as tenant:
        document = tenant.create_document(
            header={"order_total": Decimal("4300.00")},
            lines=[
                {
                    "quantity": Decimal("10"),
                    "unit_price": Decimal("30.00"),
                    "line_total": Decimal("300.00"),
                }
            ],
        )
        _run_validation_preserving_values(tenant, document)
        warning_id = tenant.live_warnings(document)[0]["id"]

        # A reviewer fixes the header total (the review API is Phase 3; this
        # stands in for the human edit that slice will make).
        with platform_session() as session:
            session.execute(
                text("UPDATE document_headers SET order_total = :total WHERE document_id = :id"),
                {"id": str(document), "total": "300.00"},
            )

        summary = _run_validation_preserving_values(tenant, document)
        assert summary.resolved == 1
        assert tenant.live_warnings(document) == []

        # The row survives, soft-deleted, with its history intact.
        all_rows = tenant.warnings(document)
        assert len(all_rows) == 1
        assert all_rows[0]["id"] == warning_id
        assert all_rows[0]["status"] == "resolved"
        assert all_rows[0]["resolved_at"] is not None
        assert all_rows[0]["deleted_at"] is not None


@requires_validation_schema
def test_a_changed_discrepancy_raises_a_new_unacknowledged_warning():
    """
    An acknowledgement of "this total is out by two dollars" must not carry
    over to "this total is out by four thousand". The fingerprint covers the
    compared values, so the old warning resolves and a new one opens.
    """
    with _TestValidationTenant("Acme Test Distributor") as tenant:
        document = tenant.create_document(
            header={"order_total": Decimal("302.00")},
            lines=[
                {
                    "quantity": Decimal("10"),
                    "unit_price": Decimal("30.00"),
                    "line_total": Decimal("300.00"),
                }
            ],
        )
        _run_validation_preserving_values(tenant, document)
        first_id = tenant.live_warnings(document)[0]["id"]
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE document_warnings SET status = 'acknowledged', "
                    "acknowledged_by = :user_id, acknowledged_at = now() WHERE id = :id"
                ),
                {"id": str(first_id), "user_id": str(tenant.user_id)},
            )
            session.execute(
                text("UPDATE document_headers SET order_total = :total WHERE document_id = :id"),
                {"id": str(document), "total": "4300.00"},
            )

        _run_validation_preserving_values(tenant, document)

        live = tenant.live_warnings(document)
        assert len(live) == 1
        assert live[0]["id"] != first_id
        assert live[0]["status"] == "open"
        assert live[0]["severity"] == "high"
        assert unacknowledged_warning_count_for(tenant, document) == 1


# ── ordering and reads ──────────────────────────────────────────────────────


@requires_validation_schema
def test_open_warnings_are_returned_most_severe_first():
    with _TestValidationTenant("Acme Test Distributor") as tenant:
        document = tenant.create_document(
            header={"po_number": None, "order_total": Decimal("4300.00")},
            currency_inferred=True,
            lines=[
                {
                    "quantity": Decimal("10"),
                    "unit_price": Decimal("30.00"),
                    "line_total": Decimal("300.00"),
                }
            ],
        )
        _run_validation_preserving_values(tenant, document)

        with tenant_session(tenant.tenant_id) as session:
            rows = open_warnings(session, document)

        severities = [row["severity"] for row in rows]
        assert severities == sorted(
            severities, key=lambda s: {"critical": 0, "high": 1, "warning": 2, "info": 3}[s]
        )
        assert severities[0] == "high"
        assert severities[-1] == "info"


# ── tenant isolation (CLAUDE.md Section 7.5's required test class) ──────────


@requires_validation_schema
def test_tenant_b_never_sees_or_is_given_tenant_a_warnings():
    with _TestValidationTenant("Acme Test Distributor") as tenant_a:
        with _TestValidationTenant("Northwind Test Supply") as tenant_b:
            a_document = tenant_a.create_document(
                header={"order_total": Decimal("4300.00")},
                lines=[
                    {
                        "quantity": Decimal("10"),
                        "unit_price": Decimal("30.00"),
                        "line_total": Decimal("300.00"),
                    }
                ],
            )
            _run_validation_preserving_values(tenant_a, a_document)
            assert len(tenant_a.live_warnings(a_document)) == 1

            # Tenant B's session cannot see the warning row at all -- RLS, not
            # a WHERE clause this test wrote.
            with tenant_session(tenant_b.tenant_id) as session:
                assert open_warnings(session, a_document) == []
                visible = session.execute(
                    text("SELECT id FROM document_warnings WHERE document_id = :id"),
                    {"id": str(a_document)},
                ).first()
            assert visible is None

            # And B cannot validate A's document into existence either: the
            # snapshot load is scoped by the same RLS.
            with tenant_session(tenant_b.tenant_id) as session:
                assert load_snapshot(session, a_document) is None
                summary = validate_document(session, tenant_b.tenant_id, a_document)
            assert summary.created == 0
            assert len(tenant_a.live_warnings(a_document)) == 1


# ── the snapshot the pure rules run on ──────────────────────────────────────


@requires_validation_schema
def test_the_loaded_snapshot_carries_decimals_not_floats():
    """Section 7.1: no float ever touches money. NUMERIC columns must come
    back as Decimal all the way into the rules."""
    with _TestValidationTenant("Acme Test Distributor") as tenant:
        document = tenant.create_document(
            # The header total has to match the line, or this document
            # legitimately reconciles badly and the `evaluate_document`
            # assertion below would be testing VAL-002 rather than Decimals.
            header={"order_total": Decimal("570.00")},
            lines=[
                {
                    "quantity": Decimal("12"),
                    "unit_price": Decimal("47.50"),
                    "line_total": Decimal("570.00"),
                    "confidence": Decimal("0.950"),
                }
            ],
        )

        with tenant_session(tenant.tenant_id) as session:
            snapshot = load_snapshot(session, document)

        assert snapshot is not None
        assert isinstance(snapshot.header["order_total"], Decimal)
        line = snapshot.lines[0]
        for value in (line.quantity, line.unit_price, line.line_total, line.confidence):
            assert isinstance(value, Decimal)
        # The pure rules agree with what the database handed over.
        assert evaluate_document(snapshot) == []


@requires_validation_schema
def test_date_plausibility_is_measured_against_the_documents_own_received_date():
    """
    Not against "today" -- so re-validating an old document is stable, which
    is what keeps `sync_warnings` idempotent over time (D-074).
    """
    with _TestValidationTenant("Acme Test Distributor") as tenant:
        document = tenant.create_document(
            header={"order_date": "1825-06-01"},
            lines=[
                {
                    "quantity": Decimal("10"),
                    "unit_price": Decimal("10.00"),
                    "line_total": Decimal("100.00"),
                }
            ],
        )

        with tenant_session(tenant.tenant_id) as session:
            snapshot = load_snapshot(session, document)

        assert snapshot is not None
        # Sanity: the window is anchored to the row's own created_at.
        with platform_session() as session:
            created = session.execute(
                text("SELECT created_at FROM documents WHERE id = :id"), {"id": str(document)}
            ).scalar_one()
        assert snapshot.received_on == created.date()

        _run_validation_preserving_values(tenant, document)
        assert "VAL-005" in _codes(tenant.live_warnings(document))


# ── the duplicate/change-order flags become warnings ────────────────────────


@requires_validation_schema
def test_the_duplicate_and_change_order_flags_surface_as_warnings():
    with _TestValidationTenant("Acme Test Distributor") as tenant:
        earlier = tenant.create_document(
            lines=[
                {
                    "quantity": Decimal("10"),
                    "unit_price": Decimal("10.00"),
                    "line_total": Decimal("100.00"),
                }
            ],
        )
        later = tenant.create_document(
            created_at_offset_days=1,
            lines=[
                {
                    "quantity": Decimal("10"),
                    "unit_price": Decimal("10.00"),
                    "line_total": Decimal("100.00"),
                }
            ],
        )
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE documents SET is_possible_duplicate = true, "
                    "duplicate_of_document_id = :earlier WHERE id = :later"
                ),
                {"earlier": str(earlier), "later": str(later)},
            )

        _run_validation_preserving_values(tenant, later)
        warnings = [w for w in tenant.live_warnings(later) if w["code"] == CODE_POSSIBLE_DUPLICATE]
        assert len(warnings) == 1
        assert _detail(warnings[0])["duplicate_of_document_id"] == str(earlier)

        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE documents SET is_possible_duplicate = false, "
                    "duplicate_of_document_id = NULL, is_possible_change_order = true, "
                    "change_order_of_document_id = :earlier WHERE id = :later"
                ),
                {"earlier": str(earlier), "later": str(later)},
            )

        summary = _run_validation_preserving_values(tenant, later)
        assert summary.resolved == 1
        codes = _codes(tenant.live_warnings(later))
        assert codes == [CODE_POSSIBLE_CHANGE_ORDER]
