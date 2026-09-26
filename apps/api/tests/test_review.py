"""
Human review, approval and the immutable snapshot, against the real database
(CLAUDE.md Section 7.3), including the Section 7.5 required isolation test
and the Phase 3 exit criterion "the audit trail shows exactly what changed."

`packages/core/tests/test_review.py` covers the allowlist, the diff and the
snapshot hashing as pure functions. This file covers what only exists in
terms of rows: that approving freezes a snapshot and attributes it, that an
unacknowledged warning blocks approval, that editing an approved document
reopens it without destroying the old snapshot, and that a human edit is
never overwritten afterwards.

The assertion this file exists to protect is the machine-columns check inside
`_edit`: Section 10 forbids a review path from altering the record of what the
machine did, so every test that edits a document goes through that helper and
asserts those columns came back untouched.

Everything runs through `tenant_session()` exactly as the API does, so the
RLS policies are what enforce isolation here -- not a WHERE clause the test
wrote itself. The tenant fixture is test_validation.py's, not a second copy.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID

import pytest
from docflow_core.db import platform_session, tenant_session
from docflow_core.review import (
    ACTION_APPROVED,
    ACTION_EDITED,
    ACTION_REJECTED,
    ACTION_REOPENED,
    CODE_ALREADY_APPROVED,
    CODE_NOT_REVIEWABLE,
    CODE_STALE_EDIT,
    CODE_UNACKNOWLEDGED_WARNINGS,
    EditRequest,
    ReviewError,
    WarningAcknowledgement,
    apply_edits,
    approve_document,
    build_snapshot,
    current_snapshot,
    document_version,
    reject_document,
    reopen_document,
    review_trail,
    snapshot_sha256,
    start_review,
)
from docflow_core.validation import validate_document
from sqlalchemy import text

from tests.conftest import requires_review_schema
from tests.test_validation import _TestValidationTenant

# A document whose numbers all reconcile, so validation raises no warnings and
# a test about approval is about approval.
CLEAN_HEADER = {"po_number": "BCH-2291", "order_total": Decimal("570.00")}
CLEAN_LINES = [
    {
        "quantity": Decimal("12"),
        "unit_price": Decimal("47.50"),
        "line_total": Decimal("570.00"),
        "confidence": Decimal("0.97"),
    }
]


def _document_row(document_id: UUID) -> dict:
    with platform_session() as session:
        row = session.execute(
            text(
                "SELECT status, approved_at, approved_by, approved_json, "
                "approved_snapshot_hash, review_started_at, model_id, content_sha256, raw_json "
                "FROM documents WHERE id = :id"
            ),
            {"id": str(document_id)},
        ).mappings().first()
    return dict(row) if row else {}


def _snapshots(document_id: UUID) -> list[dict]:
    with platform_session() as session:
        rows = session.execute(
            text(
                "SELECT id, snapshot, snapshot_sha256, review_action_id, superseded_at, created_at "
                "FROM document_snapshots WHERE document_id = :id ORDER BY created_at"
            ),
            {"id": str(document_id)},
        ).mappings().all()
    return [dict(row) for row in rows]


def _machine_columns(tenant: _TestValidationTenant, document_id: UUID) -> dict:
    """The record of what DocFlow did, which review must never change."""
    document = _document_row(document_id)
    with platform_session() as session:
        header = session.execute(
            text(
                "SELECT header_confidence, currency_inferred, buyer_id "
                "FROM document_headers WHERE document_id = :id"
            ),
            {"id": str(document_id)},
        ).mappings().first()
        lines = session.execute(
            text(
                "SELECT id, confidence, matched_item_id, match_method, match_score, "
                "match_candidates FROM document_lines WHERE document_id = :id ORDER BY line_number"
            ),
            {"id": str(document_id)},
        ).mappings().all()
    return {
        "model_id": document["model_id"],
        "content_sha256": document["content_sha256"],
        "raw_json": document["raw_json"],
        "header": dict(header) if header else {},
        "lines": [dict(row) for row in lines],
    }


def _edit(
    tenant: _TestValidationTenant,
    document_id: UUID,
    request: EditRequest,
    **kwargs,
) -> UUID | None:
    """
    Apply edits and assert the machine's own record came back untouched.

    Every test that edits goes through here rather than calling `apply_edits`
    directly, so the Section 10 guarantee cannot be lost by someone adding a
    test that forgets to check.
    """
    before = _machine_columns(tenant, document_id)
    with tenant_session(tenant.tenant_id) as session:
        action_id = apply_edits(
            session,
            tenant.tenant_id,
            document_id,
            user_id=tenant.user_id,
            request=request,
            **kwargs,
        )
    assert _machine_columns(tenant, document_id) == before, (
        "a review edit altered the record of what the machine did"
    )
    return action_id


def _detail(value) -> list | dict:
    return json.loads(value) if isinstance(value, str) else value


# -- the Phase 3 exit criterion ---------------------------------------------


@requires_review_schema
def test_the_audit_trail_shows_exactly_what_changed():
    """
    The Phase 3 exit criterion. A reviewer corrects two header fields and a
    line, approves, and the trail names every value before and after.
    """
    with _TestValidationTenant("Acme Test Distributor -- trail") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        line_id = tenant.lines(document)[0]["id"]

        _edit(
            tenant,
            document,
            EditRequest(
                header={"po_number": "BCH-2292", "payment_terms": "Net 30"},
                lines={line_id: {"quantity": "13"}},
            ),
        )
        with tenant_session(tenant.tenant_id) as session:
            approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)

        trail = review_trail_for(tenant, document)
        assert [row["action"] for row in trail] == [ACTION_EDITED, ACTION_APPROVED]

        changes = {c["field"]: c for c in _detail(trail[0]["changes"])}
        assert changes["po_number"]["before"] == "BCH-2291"
        assert changes["po_number"]["after"] == "BCH-2292"
        assert changes["payment_terms"]["before"] is None
        assert changes["payment_terms"]["after"] == "Net 30"
        # Exactly as it was entered (Decimal("12")): since C1 (D-154) the column
        # no longer pads it to "12.0000".
        assert changes["quantity"]["before"] == "12"
        assert changes["quantity"]["after"] == "13"
        assert changes["quantity"]["line_number"] == 1

        # Every action names a person.
        assert all(row["user_id"] is not None for row in trail)


def review_trail_for(tenant: _TestValidationTenant, document_id: UUID) -> list[dict]:
    with tenant_session(tenant.tenant_id) as session:
        return review_trail(session, document_id)


# -- approval is never anonymous, and freezes a snapshot ---------------------


@requires_review_schema
def test_approving_freezes_a_snapshot_and_attributes_it():
    with _TestValidationTenant("Acme Test Distributor -- approve") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        with tenant_session(tenant.tenant_id) as session:
            expected = build_snapshot(session, document)
            action_id = approve_document(
                session, tenant.tenant_id, document, user_id=tenant.user_id
            )

        row = _document_row(document)
        assert row["status"] == "approved"
        assert row["approved_by"] == tenant.user_id
        assert row["approved_at"] is not None
        assert row["approved_snapshot_hash"] == snapshot_sha256(expected)
        assert _detail(row["approved_json"]) == expected

        snapshots = _snapshots(document)
        assert len(snapshots) == 1
        assert snapshots[0]["superseded_at"] is None
        assert snapshots[0]["review_action_id"] == action_id
        assert _detail(snapshots[0]["snapshot"]) == expected


@requires_review_schema
def test_the_database_itself_refuses_an_unattributed_approval():
    """
    Section 7.3: "there is no code path that sets `approved` without a user ID
    and timestamp." The application enforces it; this asserts the database
    does too, so a future code path that bypassed `approve_document` could
    not succeed either.
    """
    with _TestValidationTenant("Acme Test Distributor -- constraint") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        with pytest.raises(Exception) as excinfo:
            with platform_session() as session:
                session.execute(
                    text("UPDATE documents SET status = 'approved' WHERE id = :id"),
                    {"id": str(document)},
                )
        assert "documents_approved_is_attributable" in str(excinfo.value)


@requires_review_schema
def test_the_snapshot_is_self_contained_and_survives_later_edits():
    """
    Section 7.3: "Exports are generated from the snapshot, never from the live
    tables." So the snapshot has to still say the approved thing after the
    live rows have moved on.
    """
    with _TestValidationTenant("Acme Test Distributor -- frozen") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with tenant_session(tenant.tenant_id) as session:
            approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)

        frozen = _detail(_snapshots(document)[0]["snapshot"])
        assert frozen["header"]["po_number"] == "BCH-2291"

        # Editing reopens the document and changes the live row...
        _edit(tenant, document, EditRequest(header={"po_number": "BCH-9999"}))
        assert tenant.header(document)["po_number"] == "BCH-9999"

        # ...and the old snapshot still says what was approved.
        assert _detail(_snapshots(document)[0]["snapshot"])["header"]["po_number"] == "BCH-2291"


@requires_review_schema
def test_approving_twice_is_refused():
    with _TestValidationTenant("Acme Test Distributor -- twice") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with tenant_session(tenant.tenant_id) as session:
            approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)

        with pytest.raises(ReviewError) as excinfo:
            with tenant_session(tenant.tenant_id) as session:
                approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)
        assert excinfo.value.code == CODE_ALREADY_APPROVED
        assert len(_snapshots(document)) == 1


@requires_review_schema
def test_a_failed_document_cannot_be_approved():
    with _TestValidationTenant("Acme Test Distributor -- failed") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with platform_session() as session:
            session.execute(
                text("UPDATE documents SET status = 'failed' WHERE id = :id"),
                {"id": str(document)},
            )

        with pytest.raises(ReviewError) as excinfo:
            with tenant_session(tenant.tenant_id) as session:
                approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)
        assert excinfo.value.code == CODE_NOT_REVIEWABLE


# -- unresolved warnings must be acknowledged --------------------------------


@requires_review_schema
def test_an_unacknowledged_warning_blocks_approval():
    """Section 7.3: "any unresolved warning at approval time must be
    explicitly acknowledged"."""
    with _TestValidationTenant("Acme Test Distributor -- blocked") as tenant:
        # A total that does not reconcile, so validation raises VAL-002.
        document = tenant.create_document(
            header={"po_number": "BCH-2291", "order_total": Decimal("100.00")},
            lines=CLEAN_LINES,
        )
        with tenant_session(tenant.tenant_id) as session:
            validate_document(session, tenant.tenant_id, document)

        with pytest.raises(ReviewError) as excinfo:
            with tenant_session(tenant.tenant_id) as session:
                approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)

        assert excinfo.value.code == CODE_UNACKNOWLEDGED_WARNINGS
        assert "VAL-002" in excinfo.value.detail["codes"]
        assert _document_row(document)["status"] == "needs_review"
        assert _snapshots(document) == []


@requires_review_schema
def test_acknowledging_a_warning_records_its_text_and_allows_approval():
    with _TestValidationTenant("Acme Test Distributor -- acknowledged") as tenant:
        document = tenant.create_document(
            header={"po_number": "BCH-2291", "order_total": Decimal("100.00")},
            lines=CLEAN_LINES,
        )
        with tenant_session(tenant.tenant_id) as session:
            validate_document(session, tenant.tenant_id, document)

        warning = tenant.live_warnings(document)[0]
        ack = WarningAcknowledgement(
            warning_id=warning["id"],
            code=warning["code"],
            text="The order total doesn't equal the sum of the line totals.",
            note="Freight is invoiced separately for this buyer.",
        )

        with tenant_session(tenant.tenant_id) as session:
            approve_document(
                session,
                tenant.tenant_id,
                document,
                user_id=tenant.user_id,
                acknowledgements=[ack],
            )

        assert _document_row(document)["status"] == "approved"

        approval = [r for r in review_trail_for(tenant, document) if r["action"] == ACTION_APPROVED][0]
        recorded = _detail(approval["warning_acknowledgements"])
        assert len(recorded) == 1
        assert recorded[0]["code"] == "VAL-002"
        assert "doesn't equal the sum" in recorded[0]["text"]
        assert recorded[0]["note"] == "Freight is invoiced separately for this buyer."

        # The warning row itself is now acknowledged and linked back.
        stored = [w for w in tenant.warnings(document) if w["id"] == warning["id"]][0]
        assert stored["status"] == "acknowledged"
        assert stored["acknowledged_by"] == tenant.user_id


# -- a human edit is never overwritten ---------------------------------------


@requires_review_schema
def test_an_edit_stamps_human_provenance_so_rematching_leaves_it_alone():
    """
    Section 10: "never overwrite a human correction with a machine value."
    The mechanism is the provenance stamp -- `matching._human_edited` reads
    exactly this.
    """
    from docflow_core.matching import match_document_lines

    with _TestValidationTenant("Acme Test Distributor -- provenance") as tenant:
        tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        line_id = tenant.lines(document)[0]["id"]

        action_id = _edit(tenant, document, EditRequest(lines={line_id: {"sku": "CF-9999"}}))

        with platform_session() as session:
            provenance = session.execute(
                text("SELECT field_provenance FROM document_lines WHERE id = :id"),
                {"id": str(line_id)},
            ).scalar_one()
        provenance = _detail(provenance)
        assert provenance["sku"] == f"human_edit:{action_id}"

        # Re-running matching must not touch the human's SKU.
        with tenant_session(tenant.tenant_id) as session:
            match_document_lines(session, tenant.tenant_id, document)

        assert tenant.lines(document)[0]["sku"] == "CF-9999"


@requires_review_schema
def test_retyping_the_same_value_writes_no_action_at_all():
    with _TestValidationTenant("Acme Test Distributor -- noop") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        action_id = _edit(tenant, document, EditRequest(header={"po_number": "BCH-2291"}))

        assert action_id is None
        assert review_trail_for(tenant, document) == []


@requires_review_schema
def test_an_edit_records_money_as_a_string_not_a_float():
    """Section 7.1, in the audit trail -- the one place a value's history is
    supposed to be provable."""
    with _TestValidationTenant("Acme Test Distributor -- money") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        _edit(tenant, document, EditRequest(header={"order_total": "571.25"}))

        changes = _detail(review_trail_for(tenant, document)[0]["changes"])
        assert changes[0]["before"] == "570.00"
        assert changes[0]["after"] == "571.25"
        assert isinstance(changes[0]["after"], str)
        assert tenant.header(document)["order_total"] == Decimal("571.25")


# -- editing after approval reopens, and retains the old snapshot ------------


@requires_review_schema
def test_editing_an_approved_document_reopens_it_and_keeps_the_old_snapshot():
    """
    Section 7.3: "If someone edits after approval, the document reverts to
    needs_review and must be re-approved -- the old snapshot is retained."
    """
    with _TestValidationTenant("Acme Test Distributor -- reopen") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with tenant_session(tenant.tenant_id) as session:
            approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)
        first_hash = _document_row(document)["approved_snapshot_hash"]

        _edit(tenant, document, EditRequest(header={"order_total": "600.00"}))

        row = _document_row(document)
        assert row["status"] == "needs_review"
        assert row["approved_at"] is None
        assert row["approved_by"] is None
        assert row["approved_json"] is None
        assert row["approved_snapshot_hash"] is None

        # The snapshot survives, superseded rather than deleted.
        snapshots = _snapshots(document)
        assert len(snapshots) == 1
        assert snapshots[0]["superseded_at"] is not None
        assert snapshots[0]["snapshot_sha256"] == first_hash

        # The approval is itself part of the trail, and the edit precedes the
        # reopen it caused.
        actions = [r["action"] for r in review_trail_for(tenant, document)]
        assert actions == [ACTION_APPROVED, ACTION_EDITED, ACTION_REOPENED]


@requires_review_schema
def test_the_edit_and_the_reopen_it_causes_are_ordered_by_sequence_not_time():
    """
    D-084, pinned to the exact hazard rather than to luck.

    The `edited` row and the `reopened` row it triggers are written in ONE
    transaction, and Postgres `now()` is transaction start time -- so their
    `created_at` values are identical and cannot order them. Ordering by
    `(created_at, id)` with a random uuid tiebreaker rendered cause and
    effect in arbitrary order; this asserts both halves of that: the
    timestamps really do tie, and `sequence` really does separate them.
    """
    with _TestValidationTenant("Acme Test Distributor -- ordering") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with tenant_session(tenant.tenant_id) as session:
            approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)

        _edit(tenant, document, EditRequest(header={"order_total": "600.00"}))

        trail = review_trail_for(tenant, document)
        edited = next(r for r in trail if r["action"] == ACTION_EDITED)
        reopened = next(r for r in trail if r["action"] == ACTION_REOPENED)

        # The hazard: nothing in the timestamps distinguishes them.
        assert edited["created_at"] == reopened["created_at"]
        # The fix: the sequence does, and it puts the cause first.
        assert edited["sequence"] < reopened["sequence"]


@requires_review_schema
def test_re_approving_supersedes_the_first_snapshot_and_keeps_both():
    with _TestValidationTenant("Acme Test Distributor -- reapprove") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with tenant_session(tenant.tenant_id) as session:
            approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)

        _edit(tenant, document, EditRequest(header={"order_total": "600.00"}))

        with tenant_session(tenant.tenant_id) as session:
            approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)

        snapshots = _snapshots(document)
        assert len(snapshots) == 2
        assert snapshots[0]["superseded_at"] is not None
        assert snapshots[1]["superseded_at"] is None
        assert _detail(snapshots[0]["snapshot"])["header"]["order_total"] == "570.00"
        assert _detail(snapshots[1]["snapshot"])["header"]["order_total"] == "600.00"

        with tenant_session(tenant.tenant_id) as session:
            live = current_snapshot(session, document)
        assert live["snapshot_sha256"] == snapshots[1]["snapshot_sha256"]


# -- review_started_at, the KPI clock ----------------------------------------


@requires_review_schema
def test_review_started_at_is_set_once_and_never_reset():
    """
    7.15.3 measures review time as `approved_at - review_started_at`. A
    reviewer who opens a document, goes to lunch and reopens it must not have
    the clock reset -- that would turn a slow review into a fast one.
    """
    with _TestValidationTenant("Acme Test Distributor -- clock") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        assert _document_row(document)["review_started_at"] is None

        with tenant_session(tenant.tenant_id) as session:
            start_review(session, document)
        first = _document_row(document)["review_started_at"]
        assert first is not None

        with tenant_session(tenant.tenant_id) as session:
            start_review(session, document)
        assert _document_row(document)["review_started_at"] == first


# -- rejection ---------------------------------------------------------------


@requires_review_schema
def test_rejecting_records_a_reason_and_destroys_nothing():
    with _TestValidationTenant("Acme Test Distributor -- reject") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        header_before = tenant.header(document)

        with tenant_session(tenant.tenant_id) as session:
            reject_document(
                session,
                tenant.tenant_id,
                document,
                user_id=tenant.user_id,
                note="Duplicate of the order received yesterday.",
            )

        assert _document_row(document)["status"] == "rejected"
        assert tenant.header(document) == header_before
        assert _snapshots(document) == []

        action = review_trail_for(tenant, document)[0]
        assert action["action"] == ACTION_REJECTED
        assert action["note"] == "Duplicate of the order received yesterday."


@requires_review_schema
def test_reopening_an_approved_document_keeps_its_snapshot_and_needs_a_fresh_approval():
    """
    D-144: the review screen keeps a decided order locked, and "Reopen for
    review" is the deliberate way back. It must do exactly what an edit after
    approval does (Section 7.3): back to needs_review, the approval cleared,
    the old snapshot superseded and kept, and a `reopened` row in the trail.
    """
    with _TestValidationTenant("Acme Test Distributor -- explicit reopen") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with tenant_session(tenant.tenant_id) as session:
            approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)
        first_hash = _document_row(document)["approved_snapshot_hash"]
        header_before = tenant.header(document)

        with tenant_session(tenant.tenant_id) as session:
            reopen_document(session, tenant.tenant_id, document, user_id=tenant.user_id)

        row = _document_row(document)
        assert row["status"] == "needs_review"
        assert row["approved_at"] is None
        assert row["approved_json"] is None
        assert row["approved_snapshot_hash"] is None
        snapshots = _snapshots(document)
        assert len(snapshots) == 1
        assert snapshots[0]["superseded_at"] is not None
        assert snapshots[0]["snapshot_sha256"] == first_hash
        # Reopening changes no value.
        assert tenant.header(document) == header_before

        actions = [r["action"] for r in review_trail_for(tenant, document)]
        assert actions == [ACTION_APPROVED, ACTION_REOPENED]

        # And it can be approved again, as a new snapshot.
        with tenant_session(tenant.tenant_id) as session:
            approve_document(session, tenant.tenant_id, document, user_id=tenant.user_id)
        assert _document_row(document)["status"] == "approved"
        assert len(_snapshots(document)) == 2


@requires_review_schema
def test_reopening_a_rejected_document_sends_it_back_to_review():
    """A rejection is "reversible by re-review" (reject_document); D-144 is
    that re-review. The rejection stays in the trail."""
    with _TestValidationTenant("Acme Test Distributor -- reopen rejected") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with tenant_session(tenant.tenant_id) as session:
            reject_document(
                session, tenant.tenant_id, document, user_id=tenant.user_id, note="Wrong buyer."
            )
            reopen_document(session, tenant.tenant_id, document, user_id=tenant.user_id)

        assert _document_row(document)["status"] == "needs_review"
        actions = [r["action"] for r in review_trail_for(tenant, document)]
        assert actions == [ACTION_REJECTED, ACTION_REOPENED]


@requires_review_schema
def test_only_a_decided_document_can_be_reopened():
    with _TestValidationTenant("Acme Test Distributor -- reopen refused") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with tenant_session(tenant.tenant_id) as session:
            with pytest.raises(ReviewError) as excinfo:
                reopen_document(session, tenant.tenant_id, document, user_id=tenant.user_id)
        assert excinfo.value.code == CODE_NOT_REVIEWABLE
        assert review_trail_for(tenant, document) == []


# -- concurrency -------------------------------------------------------------


@requires_review_schema
def test_a_stale_save_is_refused_rather_than_overwriting_the_other_person():
    with _TestValidationTenant("Acme Test Distributor -- stale") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        with tenant_session(tenant.tenant_id) as session:
            version = document_version(session, document)

        # Someone else saves first.
        _edit(tenant, document, EditRequest(header={"po_number": "BCH-0001"}))

        with pytest.raises(ReviewError) as excinfo:
            _edit(
                tenant,
                document,
                EditRequest(header={"po_number": "BCH-0002"}),
                expected_version=version,
            )
        assert excinfo.value.code == CODE_STALE_EDIT
        assert tenant.header(document)["po_number"] == "BCH-0001"


# -- acting-as (CLAUDE.md Section 7.15.1) ------------------------------------


@requires_review_schema
def test_a_founder_acting_in_a_tenant_is_recorded_as_themselves():
    """
    7.15.1: "every resulting review_actions row records both user_id (the
    founder) and acting_as_tenant_id." The founder never becomes a tenant
    user and never appears as anyone but themselves.
    """
    with _TestValidationTenant("Acme Test Distributor -- acting as") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        _edit(
            tenant,
            document,
            EditRequest(header={"po_number": "BCH-7777"}),
            acting_as_tenant_id=tenant.tenant_id,
        )

        action = review_trail_for(tenant, document)[0]
        assert action["user_id"] == tenant.user_id
        assert action["acting_as_tenant_id"] == tenant.tenant_id


# -- tenant isolation (CLAUDE.md Section 7.5's required test class) ----------


@requires_review_schema
def test_tenant_b_can_never_read_or_approve_tenant_as_document():
    with _TestValidationTenant("Acme Test Distributor A") as tenant_a, _TestValidationTenant(
        "Beacon Test Supply B"
    ) as tenant_b:
        document = tenant_a.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)

        with tenant_session(tenant_b.tenant_id) as session:
            # RLS hides the row entirely, so it is not reviewable rather than
            # forbidden -- Tenant B cannot even confirm it exists.
            with pytest.raises(ReviewError) as excinfo:
                approve_document(
                    session, tenant_b.tenant_id, document, user_id=tenant_b.user_id
                )
            assert excinfo.value.code == CODE_NOT_REVIEWABLE
            assert review_trail(session, document) == []

        assert _document_row(document)["status"] == "needs_review"
        assert _snapshots(document) == []


@requires_review_schema
def test_tenant_b_never_sees_tenant_as_review_actions():
    with _TestValidationTenant("Acme Test Distributor A") as tenant_a, _TestValidationTenant(
        "Beacon Test Supply B"
    ) as tenant_b:
        document = tenant_a.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        _edit(tenant_a, document, EditRequest(header={"po_number": "BCH-5555"}))

        assert len(review_trail_for(tenant_a, document)) == 1

        with tenant_session(tenant_b.tenant_id) as session:
            rows = session.execute(
                text("SELECT id FROM review_actions WHERE document_id = :id"),
                {"id": str(document)},
            ).mappings().all()
        assert rows == []
