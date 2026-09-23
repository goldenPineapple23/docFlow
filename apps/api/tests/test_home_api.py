# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
The customer's own dashboard (slice 5.8a, D-128), against the real database
and RLS: the counts are the tenant's own and match a hand-written query, the
setup batch never appears, recent activity carries no extracted value, and one
tenant's dashboard never shows another's rows.
"""

from __future__ import annotations

from uuid import uuid4

from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import requires_database
from tests.test_console_api import _environment  # noqa: F401
from tests.test_quarantine_api import _Tenant, celery, requires_quarantine_schema  # noqa: F401


def _approve(tenant, document_id, user_id, *, hours_to_approve=2, edits=0):
    """Put a document through approval the way the product does, in rows: an
    optional edit action, then the approval, with review_actions to match."""
    with platform_session() as session:
        session.execute(
            text(
                "UPDATE documents SET status = 'approved', approved_at = created_at + "
                "make_interval(hours => :h), approved_by = :u, approved_json = '{}'::jsonb, "
                "approved_snapshot_hash = :hash WHERE id = :d"
            ),
            {"h": hours_to_approve, "u": str(user_id), "hash": uuid4().hex, "d": str(document_id)},
        )
        for _ in range(edits):
            session.execute(
                text(
                    "INSERT INTO review_actions (id, tenant_id, document_id, user_id, action, changes) "
                    "VALUES (:i, :t, :d, :u, 'edited', "
                    "'[{\"field\":\"po_number\",\"before\":\"A\",\"after\":\"B\"}]'::jsonb)"
                ),
                {"i": str(uuid4()), "t": str(tenant.tenant_id), "d": str(document_id), "u": str(user_id)},
            )
        session.execute(
            text(
                "INSERT INTO review_actions (id, tenant_id, document_id, user_id, action, changes) "
                "VALUES (:i, :t, :d, :u, 'approved', '[]'::jsonb)"
            ),
            {"i": str(uuid4()), "t": str(tenant.tenant_id), "d": str(document_id), "u": str(user_id)},
        )


@requires_database
@requires_quarantine_schema
def test_the_dashboard_counts_this_tenants_own_documents_and_matches_a_hand_query(client):
    with _Tenant("Acme Test Home") as t:
        owner_id = t.users["owner"][0]
        needs = t.seed(3, status="needs_review")
        approved = t.seed(2, status="needs_review")
        for doc in approved:
            _approve(t, doc, owner_id)
        t.seed(1, status="failed")
        t.seed(1, status="rejected")
        t.seed(4, test_batch=True, status="needs_review")  # the founder's setup batch
        t.seed(1, status="needs_review", deleted=True)
        t.seed(2, status="quarantined", reason="attachment_cap")

        home = client.get("/home", headers=t.headers()).json()

        assert home["documents_by_status"] == {
            "needs_review": 3,
            "approved": 2,
            "exported": 0,
            "rejected": 1,
            "failed": 1,
        }
        # The same question, asked independently.
        with platform_session() as session:
            hand = dict(
                session.execute(
                    text(
                        "SELECT status, count(*) FROM documents WHERE tenant_id = :t "
                        "AND deleted_at IS NULL AND NOT is_test_batch "
                        "AND status IN ('needs_review','approved','exported','rejected','failed') "
                        "GROUP BY status"
                    ),
                    {"t": str(t.tenant_id)},
                ).all()
            )
        assert {k: v for k, v in home["documents_by_status"].items() if v} == hand

        # Everything that came in: 3 waiting + 2 approved + 1 rejected
        # + 1 that couldn't be read + 2 held. Not the setup batch, not deleted.
        assert home["this_month"]["arrived"] == 9
        # The subset the plan is measured against, the same number the allowance
        # shows: 9 less the 2 held and the 1 failed (7.16.1).
        assert home["this_month"]["counted"] == 6
        assert home["this_month"]["counted"] == home["allowance"]["used"]
        assert home["this_month"]["approved"] == 2
        assert home["this_month"]["median_hours_to_approval"] == 2.0
        assert home["held"]["total"] == 2
        assert home["held"]["groups"][0]["message"]  # plain English, from the catalog
        assert home["oldest_needs_review_at"] is not None
        assert str(needs[0])  # the oldest is one of the three waiting


@requires_database
@requires_quarantine_schema
def test_recent_activity_says_what_happened_and_never_what_a_document_said(client):
    with _Tenant("Acme Test Home Activity") as t:
        owner_id = t.users["owner"][0]
        doc = t.seed(1, status="needs_review")[0]
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO document_headers (document_id, tenant_id, po_number) "
                    "VALUES (:d, :t, 'PO-SECRET-1')"
                ),
                {"d": str(doc), "t": str(t.tenant_id)},
            )
        _approve(t, doc, owner_id, edits=1)

        activity = client.get("/home", headers=t.headers()).json()["activity"]

        kinds = [a["kind"] for a in activity]
        assert kinds == ["approved", "edited"]  # newest first
        edited = activity[1]
        assert edited["detail"] == "1 field"  # how much changed, never to what
        assert edited["by"] == t.owner_email()
        assert edited["by_docflow_support"] is False
        assert edited["po_number"] == "PO-SECRET-1"  # the reference, so it is clickable
        blob = str(activity)
        for value in ("before", "after", '"A"', '"B"'):
            assert value not in blob


@requires_database
@requires_quarantine_schema
def test_work_the_founder_did_inside_the_account_is_labelled_docflow_support(client):
    with _Tenant("Acme Test Home Support") as t:
        doc = t.seed(1, status="needs_review")[0]
        owner_id = t.users["owner"][0]
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO review_actions "
                    "(id, tenant_id, document_id, user_id, acting_as_tenant_id, action, changes) "
                    "VALUES (:i, :t, :d, :u, :t, 'approved', '[]'::jsonb)"
                ),
                {"i": str(uuid4()), "t": str(t.tenant_id), "d": str(doc), "u": str(owner_id)},
            )
        activity = client.get("/home", headers=t.headers()).json()["activity"]
        assert activity[0]["by_docflow_support"] is True
        assert activity[0]["by"] is None  # the founder's own address is not the tenant's business


@requires_database
@requires_quarantine_schema
def test_one_tenants_dashboard_never_shows_anothers(client):
    with _Tenant("Acme Test Home A") as a, _Tenant("Beacon Test Home B") as b:
        b.seed(5, status="needs_review")
        b.seed(2, status="quarantined", reason="auth_fail")
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO review_actions (id, tenant_id, document_id, user_id, action, changes) "
                    "VALUES (:i, :t, :d, :u, 'approved', '[]'::jsonb)"
                ),
                {
                    "i": str(uuid4()),
                    "t": str(b.tenant_id),
                    "d": str(b.seed(1)[0]),
                    "u": str(b.users["owner"][0]),
                },
            )

        home = client.get("/home", headers=a.headers()).json()
        assert home["documents_by_status"] == dict.fromkeys(
            ("needs_review", "approved", "exported", "rejected", "failed"), 0
        )
        assert home["held"]["total"] == 0
        assert home["activity"] == []
        assert home["this_month"]["arrived"] == 0
        assert home["this_month"]["counted"] == 0


@requires_database
@requires_quarantine_schema
def test_an_empty_account_reads_as_empty_rather_than_failing(client):
    with _Tenant("Acme Test Home Empty") as t:
        home = client.get("/home", headers=t.headers()).json()
        assert home["this_month"]["median_hours_to_approval"] is None
        assert home["oldest_needs_review_at"] is None
        assert home["received_today"] == 0
        assert home["allowance"]["banner"] is None
