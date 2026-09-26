"""
Review integrity (Phase 5.5 Stage 1c; review findings H2, M4, M5; D-162),
against the real database, through the same routes the review screen and the
Console's acting-as screen use.

H2 -- a human edit was never re-validated. A reviewer who typed 4750 for
      47.50 got no math warning and could approve and export it, and a
      warning stayed open after the value behind it was fixed. Now every edit
      re-validates in its own transaction, and approval re-validates before
      it counts what is still open -- saving what it finds even when it then
      refuses.
M4 -- the acknowledgement "warning text" came from the browser: whatever it
      sent, including nothing or a forgery. Now the server writes it from the
      stored warning and the catalog, and ignores anything the client says the
      warning was.
M5 -- approval wasn't tied to what the reviewer saw. Reviewer A could approve
      values reviewer B changed after A loaded the page. Now approval requires
      the version token and locks the row, as edits do.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import uuid4

import pytest
from docflow_core.db import tenant_session
from docflow_core.errors import get_error
from docflow_core.review import (
    CODE_STALE_EDIT,
    CODE_UNACKNOWLEDGED_WARNINGS,
    Acknowledgement,
    EditRequest,
    ReviewError,
    apply_edits,
    approve_document,
    document_version,
    review_trail,
)
from docflow_core.validation import validate_document
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from tests.conftest import requires_review_schema
from tests.test_review_api import (  # noqa: F401 -- _secrets is an autouse fixture
    CLEAN_HEADER,
    CLEAN_LINES,
    _ReviewTenant,
    _secrets,
    _status,
    _version,
)


def _open_codes(tenant, document) -> list[str]:
    return sorted(w["code"] for w in tenant.live_warnings(document) if w["status"] == "open")


def _approval_acks(tenant, document) -> list[dict]:
    with tenant_session(tenant.tenant_id) as session:
        trail = review_trail(session, document)
    (approval,) = [row for row in trail if row["action"] == "approved"]
    acks = approval["warning_acknowledgements"]
    return json.loads(acks) if isinstance(acks, str) else acks


# ── H2: every edit, and every approval, is checked again ────────────────────


@requires_review_schema
def test_H2_a_mistyped_price_raises_a_warning_and_blocks_approval(client):
    with _ReviewTenant("Acme Test H2 Typo") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with tenant_session(tenant.tenant_id) as session:
            validate_document(session, tenant.tenant_id, document)
        assert _open_codes(tenant, document) == []

        line_id = tenant.lines(document)[0]["id"]
        response = client.patch(
            f"/review/documents/{document}",
            headers=tenant.headers(),
            json={
                "lines": [{"line_id": str(line_id), "fields": {"unit_price": "4750"}}],
                "expected_version": _version(client, tenant, document),
            },
        )
        assert response.status_code == 200

        # 12 x 4750 is not 570.00: the line no longer reconciles.
        assert "VAL-001" in _open_codes(tenant, document)
        refused = client.post(
            f"/review/documents/{document}/approve",
            headers=tenant.headers(),
            json={"acknowledgements": [], "expected_version": response.json()["version"]},
        )
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "REV-001"
        assert _status(document) == "needs_review"


@requires_review_schema
def test_H2_fixing_the_value_resolves_its_warning(client):
    with _ReviewTenant("Acme Test H2 Fixed") as tenant:
        document = tenant.create_document(
            header={"po_number": "BCH-2291", "order_total": Decimal("100.00")}, lines=CLEAN_LINES
        )
        with tenant_session(tenant.tenant_id) as session:
            validate_document(session, tenant.tenant_id, document)
        assert _open_codes(tenant, document) == ["VAL-002"]

        response = client.patch(
            f"/review/documents/{document}",
            headers=tenant.headers(),
            json={
                "header": {"order_total": "570.00"},
                "expected_version": _version(client, tenant, document),
            },
        )
        assert response.status_code == 200
        assert _open_codes(tenant, document) == []

        approved = client.post(
            f"/review/documents/{document}/approve",
            headers=tenant.headers(),
            json={"acknowledgements": [], "expected_version": response.json()["version"]},
        )
        assert approved.status_code == 200
        assert _status(document) == "approved"


@requires_review_schema
def test_H2_approval_rechecks_and_saves_a_warning_it_finds_even_though_it_refuses(client):
    """A document that reached review without its checks (or whose checks are
    out of date) is checked again at approval. The warning found is saved, so
    the screen the reviewer returns to shows it -- a refusal that rolled it
    back would leave them approving into the same wall."""
    with _ReviewTenant("Acme Test H2 Approval") as tenant:
        document = tenant.create_document(
            header={"po_number": "BCH-2291", "order_total": Decimal("100.00")}, lines=CLEAN_LINES
        )
        assert _open_codes(tenant, document) == []  # never validated

        refused = client.post(
            f"/review/documents/{document}/approve",
            headers=tenant.headers(),
            json={"acknowledgements": [], "expected_version": _version(client, tenant, document)},
        )
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "REV-001"
        assert _status(document) == "needs_review"
        assert _open_codes(tenant, document) == ["VAL-002"]


@requires_review_schema
def test_H2_an_edit_through_the_core_function_rechecks_too():
    """The Console and every other caller share `apply_edits`: the check is
    in the function, not only in one route."""
    with _ReviewTenant("Acme Test H2 Core") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with tenant_session(tenant.tenant_id) as session:
            apply_edits(
                session,
                tenant.tenant_id,
                document,
                user_id=tenant.user_id,
                request=EditRequest(header={"order_total": "5700.00"}),
            )
        assert _open_codes(tenant, document) == ["VAL-002"]


# ── M4: the server writes what was acknowledged ─────────────────────────────


@requires_review_schema
def test_M4_the_acknowledgement_text_is_the_catalog_wording_not_what_the_client_sent(client):
    with _ReviewTenant("Acme Test M4 Text") as tenant:
        document = tenant.create_document(
            header={"po_number": "BCH-2291", "order_total": Decimal("100.00")}, lines=CLEAN_LINES
        )
        with tenant_session(tenant.tenant_id) as session:
            validate_document(session, tenant.tenant_id, document)
        (warning,) = tenant.live_warnings(document)

        response = client.post(
            f"/review/documents/{document}/approve",
            headers=tenant.headers(),
            json={
                "acknowledgements": [
                    {
                        "warning_id": str(warning["id"]),
                        # A stale or forged client still sends these; they are ignored.
                        "code": "VAL-999",
                        "text": "nothing to see here",
                        "note": "Freight billed separately.",
                    }
                ],
                "expected_version": _version(client, tenant, document),
            },
        )
        assert response.status_code == 200

        (recorded,) = _approval_acks(tenant, document)
        entry = get_error("VAL-002")
        assert recorded["code"] == "VAL-002"
        assert recorded["warning_id"] == str(warning["id"])
        assert entry.title in recorded["text"] and entry.message in recorded["text"]
        assert "nothing to see here" not in recorded["text"]
        # The occurrence's own numbers, as stored on the warning.
        for value in (warning["detail"] or {}).values():
            assert str(value) in recorded["text"]
        assert recorded["note"] == "Freight billed separately."


@requires_review_schema
def test_M4_an_acknowledgement_of_a_warning_not_on_this_order_is_refused(client):
    with _ReviewTenant("Acme Test M4 Foreign") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        response = client.post(
            f"/review/documents/{document}/approve",
            headers=tenant.headers(),
            json={
                "acknowledgements": [{"warning_id": str(uuid4()), "note": None}],
                "expected_version": _version(client, tenant, document),
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "REV-005"
        assert _status(document) == "needs_review"


# ── M5: approval is of what the reviewer saw ────────────────────────────────


@requires_review_schema
def test_M5_approving_values_someone_changed_since_you_loaded_them_is_refused(client):
    with _ReviewTenant("Acme Test M5 Stale") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        seen_by_a = _version(client, tenant, document)

        # Reviewer B saves a change A has not seen.
        edited = client.patch(
            f"/review/documents/{document}",
            headers=tenant.headers(),
            json={"header": {"po_number": "BCH-2292"}, "expected_version": seen_by_a},
        )
        assert edited.status_code == 200

        refused = client.post(
            f"/review/documents/{document}/approve",
            headers=tenant.headers(),
            json={"acknowledgements": [], "expected_version": seen_by_a},
        )
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "REV-005"
        assert _status(document) == "needs_review"


@requires_review_schema
def test_M5_the_approve_route_requires_the_version_token(client):
    with _ReviewTenant("Acme Test M5 Missing") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        response = client.post(
            f"/review/documents/{document}/approve",
            headers=tenant.headers(),
            json={"acknowledgements": []},
        )
        assert response.status_code == 422
        assert _status(document) == "needs_review"


@requires_review_schema
def test_M5_a_stale_version_is_refused_by_the_core_function():
    with _ReviewTenant("Acme Test M5 Core") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with pytest.raises(ReviewError) as excinfo:
            with tenant_session(tenant.tenant_id) as session:
                approve_document(
                    session, tenant.tenant_id, document,
                    user_id=tenant.user_id, expected_version="0" * 64,
                )
        assert excinfo.value.code == CODE_STALE_EDIT
        assert _status(document) == "needs_review"


@requires_review_schema
def test_M5_an_approval_in_progress_holds_the_row_so_an_edit_waits():
    """The version check and the write are one step: while an approval holds
    the document, a concurrent edit cannot slip in between them."""
    with _ReviewTenant("Acme Test M5 Lock") as tenant:
        document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
        with tenant_session(tenant.tenant_id) as session:
            version = document_version(session, document)

        with tenant_session(tenant.tenant_id) as approving:
            approve_document(
                approving, tenant.tenant_id, document,
                user_id=tenant.user_id, expected_version=version,
            )
            # Not committed yet. A second connection trying to edit must wait.
            with pytest.raises(OperationalError) as excinfo:
                with tenant_session(tenant.tenant_id) as editing:
                    editing.execute(text("SET LOCAL lock_timeout = '300ms'"))
                    apply_edits(
                        editing, tenant.tenant_id, document,
                        user_id=tenant.user_id,
                        request=EditRequest(header={"po_number": "BCH-9999"}),
                    )
            assert getattr(excinfo.value.orig, "sqlstate", None) == "55P03"  # lock_not_available

        assert _status(document) == "approved"
        assert tenant.header(document)["po_number"] == "BCH-2291"


@requires_review_schema
def test_M5_M4_the_core_acknowledgement_carries_only_an_id_and_a_note():
    """What a caller supplies is which warning and why; everything else is
    read from the stored warning."""
    with _ReviewTenant("Acme Test M4 Core") as tenant:
        document = tenant.create_document(
            header={"po_number": "BCH-2291", "order_total": Decimal("100.00")}, lines=CLEAN_LINES
        )
        with tenant_session(tenant.tenant_id) as session:
            validate_document(session, tenant.tenant_id, document)
            version = document_version(session, document)
        (warning,) = tenant.live_warnings(document)

        with pytest.raises(ReviewError) as excinfo:
            with tenant_session(tenant.tenant_id) as session:
                approve_document(
                    session, tenant.tenant_id, document,
                    user_id=tenant.user_id, expected_version=version,
                )
        assert excinfo.value.code == CODE_UNACKNOWLEDGED_WARNINGS

        with tenant_session(tenant.tenant_id) as session:
            approve_document(
                session, tenant.tenant_id, document,
                user_id=tenant.user_id, expected_version=version,
                acknowledgements=[Acknowledgement(warning_id=warning["id"], note="Checked.")],
            )
        (recorded,) = _approval_acks(tenant, document)
        assert recorded["code"] == "VAL-002"
        assert get_error("VAL-002").title in recorded["text"]
