# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
The account's activity trail (slice 5.8b, D-130), against the real database and
RLS: it pages and filters the same rows the dashboard's short list shows, a
reviewer may read it and a viewer may not, one tenant never sees another's, and
no row ever carries an extracted value.
"""

from __future__ import annotations

from uuid import uuid4

from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import requires_database
from tests.test_console_api import _environment  # noqa: F401
from tests.test_quarantine_api import _Tenant, celery, requires_quarantine_schema  # noqa: F401


def _action(tenant, document_id, *, action: str, role: str = "owner", changes: str = "[]"):
    """One review action, written the way the product writes it."""
    with platform_session() as session:
        session.execute(
            text(
                "INSERT INTO review_actions (id, tenant_id, document_id, user_id, action, changes) "
                "VALUES (:i, :t, :d, :u, :a, CAST(:c AS jsonb))"
            ),
            {
                "i": str(uuid4()),
                "t": str(tenant.tenant_id),
                "d": str(document_id),
                "u": str(tenant.users[role][0]),
                "a": action,
                "c": changes,
            },
        )


@requires_database
@requires_quarantine_schema
def test_activity_pages_newest_first_and_counts_the_whole_filtered_set(client):
    with _Tenant("Acme Test Activity") as t:
        docs = t.seed(3, status="needs_review")
        for doc in docs:
            _action(t, doc, action="edited", changes='[{"field":"po_number"},{"field":"total"}]')
            _action(t, doc, action="approved")

        first = client.get("/activity?limit=4", headers=t.headers()).json()

        assert first["total"] == 6
        assert len(first["items"]) == 4
        assert first["offset"] == 0
        # Newest first, and within one transaction `sequence` breaks the tie:
        # an approval written after an edit reads after it (D-084).
        assert first["items"][0]["kind"] == "approved"
        assert first["items"][1]["kind"] == "edited"
        assert first["items"][0]["detail"] is None
        assert first["items"][1]["detail"] == "2 fields"

        second = client.get("/activity?limit=4&offset=4", headers=t.headers()).json()
        assert len(second["items"]) == 2
        assert second["total"] == 6
        # No row appears on both pages.
        seen = [(i["at"], i["kind"], i["document_id"]) for i in first["items"] + second["items"]]
        assert len(set(seen)) == 6


@requires_database
@requires_quarantine_schema
def test_filtering_narrows_the_rows_and_the_total_together(client):
    with _Tenant("Acme Test Activity Filter") as t:
        doc = t.seed(1, status="needs_review")[0]
        _action(t, doc, action="edited", changes='[{"field":"po_number"}]')
        _action(t, doc, action="approved")
        _action(t, doc, action="reopened")

        approved = client.get("/activity?kind=approved", headers=t.headers()).json()
        assert approved["total"] == 1
        assert [i["kind"] for i in approved["items"]] == ["approved"]

        two = client.get("/activity?kind=approved&kind=edited", headers=t.headers()).json()
        assert two["total"] == 2
        assert sorted(i["kind"] for i in two["items"]) == ["approved", "edited"]

        # A filter nobody has heard of shows everything rather than an error: a
        # stale bookmark should not be a dead end.
        unknown = client.get("/activity?kind=teleported", headers=t.headers()).json()
        assert unknown["total"] == 3

        # The page's chips come from the API, so the two lists cannot drift.
        assert set(unknown["kinds"]) == {
            "edited",
            "approved",
            "rejected",
            "reopened",
            "exported",
            "released",
        }


@requires_database
@requires_quarantine_schema
def test_the_dashboard_and_the_activity_page_tell_the_same_story(client):
    with _Tenant("Acme Test Activity Agree") as t:
        docs = t.seed(2, status="needs_review")
        for doc in docs:
            _action(t, doc, action="approved")

        home = client.get("/home", headers=t.headers()).json()["activity"]
        page = client.get("/activity", headers=t.headers()).json()["items"]

        assert home == page[: len(home)]


@requires_database
@requires_quarantine_schema
def test_a_reviewer_may_read_the_trail_and_a_viewer_may_not(client):
    with _Tenant("Acme Test Activity Roles") as t:
        t.add_user("reviewer")
        t.add_user("viewer")
        doc = t.seed(1, status="needs_review")[0]
        _action(t, doc, action="approved")

        assert client.get("/activity", headers=t.headers("reviewer")).status_code == 200
        assert client.get("/activity", headers=t.headers()).status_code == 200

        denied = client.get("/activity", headers=t.headers("viewer"))
        assert denied.status_code == 403
        assert denied.json()["detail"]["code"] == "AUTH-002"
        assert denied.json()["detail"]["action"]  # every tenant-facing entry says what to do


@requires_database
@requires_quarantine_schema
def test_the_trail_never_carries_what_a_document_said(client):
    with _Tenant("Acme Test Activity Values") as t:
        doc = t.seed(1, status="needs_review")[0]
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO document_headers (document_id, tenant_id, po_number) "
                    "VALUES (:d, :t, 'PO-SECRET-9')"
                ),
                {"d": str(doc), "t": str(t.tenant_id)},
            )
        _action(
            t,
            doc,
            action="edited",
            changes='[{"field":"total","before":"10.00","after":"9999.00"}]',
        )

        items = client.get("/activity", headers=t.headers()).json()["items"]

        assert items[0]["detail"] == "1 field"
        assert items[0]["po_number"] == "PO-SECRET-9"  # the reference, so it is clickable
        blob = str(items)
        for value in ("9999.00", "10.00", "before", "after"):
            assert value not in blob


@requires_database
@requires_quarantine_schema
def test_one_tenants_trail_never_shows_anothers(client):
    with _Tenant("Acme Test Activity A") as a, _Tenant("Beacon Test Activity B") as b:
        doc = b.seed(1, status="needs_review")[0]
        _action(b, doc, action="approved")

        mine = client.get("/activity", headers=a.headers()).json()
        assert mine["total"] == 0 and mine["items"] == []
        assert client.get("/activity", headers=b.headers()).json()["total"] == 1
