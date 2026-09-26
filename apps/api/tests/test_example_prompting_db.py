"""
Approved-example prompting against the real schema (CLAUDE.md Section 7.13;
slice 5.10, D-141/D-142).

The rules that only a database can prove:
  * an example never comes from another tenant (Section 7.5/7.13: "No
    learning crosses a tenant boundary") -- even a same-named buyer;
  * only approved or exported orders are ever examples, never one awaiting
    review, rejected, or deleted;
  * a buyer below EXAMPLE_MIN_APPROVED_DOCS gets none;
  * most recent first, at most EXAMPLE_MAX_PER_PROMPT, only ones with text;
  * the sender rules (contact email, a domain owned by exactly one buyer);
  * the Console switch: 404 to anyone but a platform admin, EXM-001 without
    the golden-run confirmation, and logged both ways;
  * the recorder writes the runs the cost breaker reads (D-142).

Skips cleanly until supabase/migrations/0025_example_prompting.sql is applied.
"""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from docflow_core import example_prompting as ep
from docflow_core import model_runs, usage
from docflow_core.constants import EXAMPLE_MAX_PER_PROMPT, EXAMPLE_MIN_APPROVED_DOCS
from docflow_core.db import platform_session, tenant_session
from docflow_core.extraction import ExtractionResult, RoutingResult
from docflow_core.review import snapshot_sha256
from docflow_core.storage import save_file
from sqlalchemy import text

from tests.conftest import database_available, requires_database
from tests.test_console_api import _Console, _environment, stripe  # noqa: F401
from tests.test_quarantine_api import _Tenant as _BaseTenant


class _Tenant(_BaseTenant):
    """The quarantine tests' throwaway tenant, plus removal of the example
    text files these tests write under the tenant's storage prefix."""

    def __exit__(self, *exc):
        import shutil

        from docflow_core.storage import _resolve

        try:
            super().__exit__(*exc)
        finally:
            shutil.rmtree(_resolve(f"tenants/{self.tenant_id}"), ignore_errors=True)


def _schema_available() -> bool:
    if not database_available():
        return False
    from docflow_core.db import get_engine

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT extracted_text_path FROM documents LIMIT 0"))
            conn.execute(text("SELECT run_kind FROM extraction_runs LIMIT 0"))
        return True
    except Exception:  # noqa: BLE001 -- any failure means "not applied yet"
        return False


requires_example_schema = pytest.mark.skipif(
    not _schema_available(),
    reason="supabase/migrations/0025_example_prompting.sql has not been applied yet (D-141).",
)

pytestmark = [requires_database, requires_example_schema]


# ── seeding ─────────────────────────────────────────────────────────────────


def _buyer(tenant: _Tenant, name: str, email: str | None = None) -> UUID:
    buyer_id = uuid4()
    with platform_session() as session:
        session.execute(
            text(
                "INSERT INTO buyers (id, tenant_id, name, normalized_name, contact_email) "
                "VALUES (:id, :t, :name, :norm, :email)"
            ),
            {
                "id": str(buyer_id),
                "t": str(tenant.tenant_id),
                "name": name,
                "norm": ep.buyers.normalize_buyer_name(name),
                "email": email,
            },
        )
    return buyer_id


def _orders(
    tenant: _Tenant,
    buyer_id: UUID,
    n: int,
    *,
    status: str = "approved",
    with_text: bool = True,
    deleted: bool = False,
    start_minutes_ago: int = 1000,
) -> list[UUID]:
    """`n` orders for one buyer, oldest first, each approved one minute after
    the last, each with its own PO number in its snapshot and its text."""
    ids = []
    for i in range(n):
        doc_id = uuid4()
        po = f"TEST-{doc_id.hex[:6]}"
        path = (
            save_file(tenant.tenant_id, "extracted.txt", f"PURCHASE ORDER\nPO Number: {po}\n".encode())
            if with_text
            else None
        )
        snapshot = {"document_id": str(doc_id), "header": {"po_number": po}, "lines": []}
        approved = status in ("approved", "exported")
        with platform_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO documents
                        (id, tenant_id, original_filename, storage_path, source, status, content_sha256,
                         approved_json, approved_snapshot_hash, approved_at, approved_by,
                         extracted_text_path, deleted_at, raw_json, created_at)
                    VALUES (:id, :t, 'po.txt', 'tenants/seed/po.txt', 'upload', :status, :sha,
                            CAST(:snap AS jsonb), :snap_hash,
                            CASE WHEN :approved THEN now() - make_interval(mins => :ago) END,
                            CAST(:approver AS uuid), :path, CASE WHEN :deleted THEN now() END,
                            '{"header": {}, "line_items": [], "test_fixture": true}'::jsonb,
                            now() - make_interval(mins => :ago))
                    """
                ),
                {
                    "id": str(doc_id),
                    "t": str(tenant.tenant_id),
                    "status": status,
                    "sha": doc_id.hex,
                    # Section 7.3: an approval always names who approved it.
                    "snap_hash": snapshot_sha256(snapshot) if approved else None,
                    "approver": str(tenant.users["owner"][0]) if approved else None,
                    "snap": json.dumps(snapshot) if approved else None,
                    "approved": approved,
                    "ago": start_minutes_ago - i,
                    "path": path,
                    "deleted": deleted,
                },
            )
            session.execute(
                text(
                    "INSERT INTO document_headers (document_id, tenant_id, po_number, buyer_id) "
                    "VALUES (:d, :t, :po, :b)"
                ),
                {"d": str(doc_id), "t": str(tenant.tenant_id), "po": po, "b": str(buyer_id)},
            )
        ids.append(doc_id)
    return ids


def _select(tenant: _Tenant, buyer_id: UUID, exclude: UUID | None = None):
    with tenant_session(tenant.tenant_id) as session:
        return ep.select_examples(session, tenant.tenant_id, buyer_id, exclude_document_id=exclude or uuid4())


def _enable(tenant: _Tenant, on: bool = True) -> None:
    with platform_session() as session:
        session.execute(
            text("UPDATE tenants SET example_prompting_enabled = :on WHERE id = :t"),
            {"on": on, "t": str(tenant.tenant_id)},
        )


# ── selection ───────────────────────────────────────────────────────────────


def test_examples_are_the_three_most_recent_approved_orders_newest_first():
    with _Tenant("Acme Test Examples") as t:
        buyer = _buyer(t, "Acme Test Buyer One")
        ids = _orders(t, buyer, 5)
        _orders(t, buyer, 1, status="exported", start_minutes_ago=10)  # the newest of all

        examples = _select(t, buyer)

        assert len(examples) == EXAMPLE_MAX_PER_PROMPT
        assert examples[1].document_id == str(ids[-1]) and examples[2].document_id == str(ids[-2])
        assert examples[0].extraction["header"]["po_number"].startswith("TEST-")
        assert "PURCHASE ORDER" in examples[0].text


def test_never_an_order_awaiting_review_rejected_deleted_or_without_text():
    with _Tenant("Acme Test Examples") as t:
        buyer = _buyer(t, "Acme Test Buyer One")
        _orders(t, buyer, 2, status="needs_review", start_minutes_ago=5)
        _orders(t, buyer, 2, status="rejected", start_minutes_ago=7)
        _orders(t, buyer, 2, deleted=True, start_minutes_ago=9)
        _orders(t, buyer, 2, with_text=False, start_minutes_ago=11)
        keep = _orders(t, buyer, 1, start_minutes_ago=500)

        assert [e.document_id for e in _select(t, buyer)] == [str(keep[0])]


def test_the_document_being_read_is_never_its_own_example():
    with _Tenant("Acme Test Examples") as t:
        buyer = _buyer(t, "Acme Test Buyer One")
        ids = _orders(t, buyer, 2)
        assert [e.document_id for e in _select(t, buyer, exclude=ids[-1])] == [str(ids[0])]


def test_an_example_never_crosses_a_tenant_boundary_even_for_a_same_named_buyer():
    with _Tenant("Acme Test Examples A") as a, _Tenant("Acme Test Examples B") as b:
        buyer_a = _buyer(a, "Acme Test Shared Name", "orders@shared.example")
        buyer_b = _buyer(b, "Acme Test Shared Name", "orders@shared.example")
        _orders(b, buyer_b, EXAMPLE_MIN_APPROVED_DOCS + 2)

        assert _select(a, buyer_a) == []
        # Tenant A's session asking for tenant B's buyer by id sees nothing either.
        assert _select(a, buyer_b) == []
        with tenant_session(a.tenant_id) as session:
            assert ep.approved_count(session, a.tenant_id, buyer_b) == 0
            assert ep.any_buyer_qualifies(session, a.tenant_id) is False


# ── identification and the gate ─────────────────────────────────────────────


def test_the_sender_identifies_a_buyer_by_contact_email_or_a_domain_only_one_buyer_uses():
    with _Tenant("Acme Test Examples") as t:
        one = _buyer(t, "Acme Test Buyer One", "orders@one-test.example")
        _buyer(t, "Acme Test Buyer Two", "a@shared-test.example")
        _buyer(t, "Acme Test Buyer Three", "b@shared-test.example")
        with tenant_session(t.tenant_id) as session:

            def who(sender):
                return ep.buyer_from_sender(session, t.tenant_id, sender)

            assert who("ORDERS@one-test.example") == (one, "sender_email")
            assert who("jo@one-test.example") == (one, "sender_domain")
            assert who("jo@shared-test.example") is None
            assert who("nobody@elsewhere.example") is None


def test_the_whole_plan_below_and_at_the_threshold():
    with _Tenant("Acme Test Examples") as t:
        buyer = _buyer(t, "Acme Test Buyer One", "orders@one-test.example")
        _orders(t, buyer, EXAMPLE_MIN_APPROVED_DOCS - 1)

        def plan():
            return ep.plan(
                object(), t.tenant_id, uuid4(), sender_email="orders@one-test.example", parts=[],
                session_factory=tenant_session,
            )

        assert plan().outcome == "flag_off"
        _enable(t)
        assert plan().outcome == "below_threshold"
        _orders(t, buyer, 1, start_minutes_ago=1)
        full = plan()
        assert full.outcome == "examples_used" and len(full.examples) == EXAMPLE_MAX_PER_PROMPT
        assert full.identified_by == "sender_email" and full.routing is None
        _enable(t, False)
        assert plan().outcome == "flag_off"  # the kill switch, immediately


def test_the_routing_read_is_matched_read_only_and_creates_nothing():
    with _Tenant("Acme Test Examples") as t:
        buyer = _buyer(t, "Acme Test Buyer One")
        routing = RoutingResult(
            ok=True, model_id="m", prompt_hash="h", schema_version="r", raw_response={},
            buyer_name="  acme test buyer ONE ", buyer_confidence=0.9,
        )
        unknown = RoutingResult(**{**routing.__dict__, "buyer_name": "Acme Test Nobody"})
        with tenant_session(t.tenant_id) as session:
            assert ep.buyer_from_routing(session, t.tenant_id, routing) == buyer
            assert ep.buyer_from_routing(session, t.tenant_id, unknown) is None
            count = session.execute(
                text("SELECT count(*) FROM buyers WHERE tenant_id = :t"), {"t": str(t.tenant_id)}
            ).scalar_one()
        assert count == 1


# ── the runs the cost breaker reads (D-142) ─────────────────────────────────


def test_recorded_runs_are_what_the_daily_cost_breaker_counts():
    with _Tenant("Acme Test Examples") as t:
        doc = t.seed(1)[0]
        extraction = ExtractionResult(
            ok=True, model_id="claude-sonnet-5", prompt_hash="p", schema_version="1", raw_response={"a": 1},
            input_tokens=5000, output_tokens=800, est_cost_usd=Decimal("0.0180"),
            examples_used=["00000000-0000-4000-8000-000000000101"], example_input_tokens=2500,
        )
        routing = RoutingResult(
            ok=True, model_id="claude-haiku-4-5", prompt_hash="r", schema_version="r", raw_response={},
            input_tokens=900, output_tokens=40, est_cost_usd=Decimal("0.0011"),
        )
        with tenant_session(t.tenant_id) as session:
            model_runs.record_routing(session, t.tenant_id, doc, routing)
            model_runs.record_extraction(session, t.tenant_id, doc, extraction)
            order = session.execute(
                text("SELECT run_kind FROM extraction_runs WHERE document_id = :d ORDER BY created_at"),
                {"d": str(doc)},
            ).scalars().all()
            cost, tokens = usage.daily_ai_spend(session, t.tenant_id)
            month = ep.month_usage(session, t.tenant_id)

        # Same transaction, yet in the order they happened (clock_timestamp,
        # found on the 5.10 drive).
        assert order == ["buyer_routing", "extraction"]
        assert cost == Decimal("0.0191") and tokens == 5000 + 800 + 900 + 40
        assert month["runs_with_examples"] == 1 and month["example_input_tokens"] == 2500
        assert month["routing_runs"] == 1 and Decimal(month["routing_cost_usd"]) == Decimal("0.0011")
        assert Decimal(month["example_cost_usd"]) == Decimal("0.0050")


# ── the Console switch ──────────────────────────────────────────────────────


def test_the_console_switch_is_404_to_a_tenant_owner(client):
    with _Tenant("Acme Test Examples") as t:
        response = client.get(f"/admin/tenants/{t.tenant_id}/example-prompting", headers=t.headers())
        assert response.status_code == 404


def test_switching_on_needs_the_golden_run_confirmation_and_both_ways_are_logged(client):
    with _Console() as console, _Tenant("Acme Test Examples") as t:
        url = f"/admin/tenants/{t.tenant_id}/example-prompting"
        buyer = _buyer(t, "Acme Test Buyer One")
        _orders(t, buyer, EXAMPLE_MIN_APPROVED_DOCS)

        got = client.get(url, headers=console.headers()).json()
        assert got["enabled"] is False
        assert got["buyers"] == [
            {"buyer_id": str(buyer), "name": "Acme Test Buyer One", "approved": 10, "with_text": 10,
             "qualifies": True}
        ]

        refused = client.put(url, headers=console.headers(), json={"enabled": True})
        assert refused.status_code == 422 and refused.json()["detail"]["code"] == "EXM-001"

        on = client.put(url, headers=console.headers(), json={"enabled": True, "golden_run_confirmed": True})
        assert on.status_code == 200 and on.json()["enabled"] is True
        off = client.put(url, headers=console.headers(), json={"enabled": False})
        assert off.status_code == 200 and off.json()["enabled"] is False

        with platform_session() as session:
            events = session.execute(
                text(
                    "SELECT event_type, actor_user_id FROM tenant_lifecycle_events "
                    "WHERE tenant_id = :t AND event_type LIKE 'example_prompting%' ORDER BY created_at"
                ),
                {"t": str(t.tenant_id)},
            ).all()
            actions = session.execute(
                text(
                    "SELECT count(*) FROM admin_actions WHERE target_tenant_id = :t "
                    "AND action = 'example_prompting_set'"
                ),
                {"t": str(t.tenant_id)},
            ).scalar_one()
        assert [e[0] for e in events] == ["example_prompting_enabled", "example_prompting_disabled"]
        assert all(str(e[1]) == str(console.user_id) for e in events)
        assert actions == 3  # the refused attempt is audited too
