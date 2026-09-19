# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
Onboarding Steps 6-9 through the Console (CLAUDE.md Section 7.15.2; D-112,
D-113, D-114), against the real database and RLS (migration 0013).

What this proves:
  * Step 6 -- the test batch uses the production upload path and checks; its
    files are stored 'staged', flagged is_test_batch, and NOT sent to the
    model; a file that fails the checks is reported and stops nothing else;
    no test batch before a catalog.
  * Step 7 -- "Run extraction" releases them to the normal pipeline, oldest
    first, at interactive priority.
  * Step 8 -- the batch can't be marked complete until every document is
    approved.
  * Step 9 -- go-live bills from the tier (never typed-in prices), turns the
    intake address on, queues the go-live email, schedules the first-week
    check-in, and marks the tenant live; a Stripe failure leaves it not live;
    it can't run twice; the setup fee must be a real amount.
  * Every step moves onboarding_status forward only, with a lifecycle event,
    and every Console call writes admin_actions.

Stripe, Supabase and the queue are replaced at their boundaries. All data is
fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal
from uuid import UUID

import pytest
from docflow_core import external_services
from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import requires_console_schema
from tests.test_catalog_import_api import CATALOG_V1, _commit, _import
from tests.test_console_api import (  # noqa: F401 -- fixtures
    _Console,
    _environment,
    _scalar,
    stripe,
    supabase_links,
)

PO_TEXT = (
    b"PURCHASE ORDER\nPO Number: TEST-PO-0001\nBuyer: Acme Test Buyer\n"
    b"TEST-1001  Test Beans 5lb  12 CS  47.50  570.00\nTotal: 570.00 USD\n"
)
PO_TEXT_2 = PO_TEXT.replace(b"TEST-PO-0001", b"TEST-PO-0002")


def _go_live_schema_available() -> bool:
    try:
        with platform_session() as session:
            session.execute(text("SELECT id FROM scheduled_jobs LIMIT 0"))
            session.execute(text("SELECT setup_fee_billing, founding_price FROM tenants LIMIT 0"))
        return True
    except Exception:
        return False


requires_go_live_schema = pytest.mark.skipif(
    not _go_live_schema_available(),
    reason="supabase/migrations/0013_test_batch_and_go_live.sql has not been applied yet -- see D-112.",
)


@pytest.fixture
def queue(monkeypatch):
    """Every send_task from the Console and the upload path, captured."""
    sent: list[tuple[str, list, str]] = []

    class _FakeCelery:
        def send_task(self, name, args=None, queue=None):
            sent.append((name, args, queue))

    monkeypatch.setattr("app.routers.admin.celery_client", _FakeCelery())
    monkeypatch.setattr("app.routers.documents.celery_client", _FakeCelery())
    return sent


@pytest.fixture
def billing(monkeypatch):
    """Records go-live's Stripe calls; set `.fail = True` to make Stripe refuse."""

    class _Billing:
        calls: list[dict] = []
        fail = False

    def start(**kwargs):
        if _Billing.fail:
            raise external_services.ExternalServiceError("stripe", "test refusal")
        _Billing.calls.append(kwargs)
        return external_services.SubscriptionResult(
            subscription_id=f"sub_test_{kwargs['tenant_id'].hex[:8]}", status="active",
            current_period_end=1_790_000_000,
        )

    monkeypatch.setattr(external_services, "start_subscription", start)
    _Billing.calls, _Billing.fail = [], False
    return _Billing


def _tenant_with_catalog(client, console) -> str:
    tenant_id = console.create_tenant(client).json()["tenant_id"]
    preview = _import(client, console, tenant_id, CATALOG_V1)
    assert _commit(client, console, tenant_id, preview["id"]).status_code == 200
    return tenant_id


def _upload(client, console, tenant_id, *files):
    return client.post(
        f"/admin/tenants/{tenant_id}/test-batch",
        headers=console.headers(),
        files=[("files", f) for f in files],
    )


def _approve_all_test_documents(tenant_id, approver) -> None:
    """Stands in for Step 8's reviews, which run through the normal review
    routes (proven in test_acting_as.py and test_review_api.py). Fills every
    field the approval constraint demands, so it is a well-formed approval."""
    with platform_session() as session:
        session.execute(
            text(
                "UPDATE documents SET status = 'approved', approved_at = now(), approved_by = :u, "
                "approved_json = '{}'::jsonb, approved_snapshot_hash = 'test-snapshot' "
                "WHERE tenant_id = :t AND is_test_batch"
            ),
            {"t": tenant_id, "u": str(approver)},
        )


def _cleanup_jobs_and_alerts(tenant_id) -> None:
    with platform_session() as session:
        session.execute(text("DELETE FROM scheduled_jobs WHERE tenant_id = :t"), {"t": tenant_id})


def _events(tenant_id) -> list[str]:
    with platform_session() as session:
        return list(
            session.execute(
                text(
                    "SELECT event_type FROM tenant_lifecycle_events WHERE tenant_id = :t "
                    "ORDER BY created_at"
                ),
                {"t": tenant_id},
            ).scalars()
        )


# ── Step 6 ──────────────────────────────────────────────────────────────────


@requires_go_live_schema
@requires_console_schema
def test_no_test_batch_before_a_catalog(client, stripe, queue):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        response = _upload(client, console, tenant_id, ("po.txt", PO_TEXT))
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "ONB-001"
        assert _scalar("SELECT count(*) FROM documents WHERE tenant_id = :t", t=tenant_id) == 0


@requires_go_live_schema
@requires_console_schema
def test_test_batch_files_are_checked_stored_staged_and_not_sent_to_the_model(client, stripe, queue):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("po.txt", "PO")
    with _Console() as console:
        tenant_id = _tenant_with_catalog(client, console)
        queue.clear()

        response = _upload(
            client, console, tenant_id,
            ("po-1.txt", PO_TEXT), ("orders.zip", archive.getvalue()), ("po-2.txt", PO_TEXT_2),
        )
        assert response.status_code == 200, response.text
        results = response.json()["results"]
        assert [r.get("status") for r in results] == ["staged", None, "staged"]
        # The same catalog-coded refusal a customer's upload gets (7.11 / 7.16.5).
        assert results[1]["error"]["code"].startswith("DOC-")

        with platform_session() as session:
            rows = session.execute(
                text("SELECT status, is_test_batch FROM documents WHERE tenant_id = :t"), {"t": tenant_id}
            ).all()
        assert sorted(rows) == [("staged", True), ("staged", True)]
        assert queue == []  # nothing reached the model
        assert console.overview(client, tenant_id)["onboarding_status"] == "test_batch_uploaded"

        # One audited Console action for the whole upload (Section 7.15.1).
        assert _scalar(
            "SELECT count(*) FROM admin_actions WHERE target_tenant_id = :t AND action = 'test_batch_upload'",
            t=tenant_id,
        ) == 1


# ── Steps 7 and 8 ───────────────────────────────────────────────────────────


@requires_go_live_schema
@requires_console_schema
def test_run_sends_the_batch_through_the_normal_pipeline_and_completion_waits_for_approval(
    client, stripe, queue
):
    with _Console() as console:
        tenant_id = _tenant_with_catalog(client, console)
        _upload(client, console, tenant_id, ("po-1.txt", PO_TEXT), ("po-2.txt", PO_TEXT_2))
        queue.clear()  # the catalog import's own parse message
        staged = [
            str(d["id"])
            for d in client.get(f"/admin/tenants/{tenant_id}/test-batch", headers=console.headers())
            .json()["documents"]
        ]

        run = client.post(f"/admin/tenants/{tenant_id}/test-batch/run", headers=console.headers())
        assert run.status_code == 200 and run.json() == {"started": 2}
        assert queue == [
            ("docflow.parse_and_extract", [tenant_id, staged[0]], "interactive"),
            ("docflow.parse_and_extract", [tenant_id, staged[1]], "interactive"),
        ]
        assert _scalar(
            "SELECT count(*) FROM documents WHERE tenant_id = :t AND status = 'pending'", t=tenant_id
        ) == 2
        assert console.overview(client, tenant_id)["onboarding_status"] == "test_batch_running"

        again = client.post(f"/admin/tenants/{tenant_id}/test-batch/run", headers=console.headers())
        assert again.status_code == 409 and again.json()["detail"]["code"] == "ONB-003"

        early = client.post(f"/admin/tenants/{tenant_id}/test-batch/complete", headers=console.headers())
        assert early.status_code == 409 and early.json()["detail"]["code"] == "ONB-004"

        _approve_all_test_documents(tenant_id, console.user_id)
        done = client.post(f"/admin/tenants/{tenant_id}/test-batch/complete", headers=console.headers())
        assert done.status_code == 200, done.text
        tenant = console.overview(client, tenant_id)
        assert tenant["onboarding_status"] == "test_batch_complete"
        assert tenant["test_batch_completed_at"] is not None

        closed = _upload(client, console, tenant_id, ("po-3.txt", PO_TEXT))
        assert closed.status_code == 409 and closed.json()["detail"]["code"] == "ONB-002"
        assert _events(tenant_id)[-3:] == [
            "onboarding_test_batch_uploaded",
            "onboarding_test_batch_running",
            "onboarding_test_batch_complete",
        ]


# ── Step 9 ──────────────────────────────────────────────────────────────────


def _ready_to_go_live(client, console) -> str:
    tenant_id = _tenant_with_catalog(client, console)
    _upload(client, console, tenant_id, ("po-1.txt", PO_TEXT))
    client.post(f"/admin/tenants/{tenant_id}/test-batch/run", headers=console.headers())
    _approve_all_test_documents(tenant_id, console.user_id)
    assert client.post(
        f"/admin/tenants/{tenant_id}/test-batch/complete", headers=console.headers()
    ).status_code == 200
    return tenant_id


@requires_go_live_schema
@requires_console_schema
def test_go_live_bills_from_the_tier_and_turns_everything_on(
    client, stripe, supabase_links, queue, billing
):
    with _Console() as console:
        tenant_id = _ready_to_go_live(client, console)
        try:
            assert console.overview(client, tenant_id)["intake_address_active"] is False
            plan = client.get(f"/admin/tenants/{tenant_id}/go-live", headers=console.headers()).json()
            assert plan["monthly_price"] == "299.00" and plan["promo_monthly_price"] == "199.00"
            assert plan["promo_months"] == 3

            response = client.post(
                f"/admin/tenants/{tenant_id}/go-live",
                headers=console.headers(),
                json={"setup_fee_amount": "750.00", "setup_fee_billing": "stripe", "founding_price": True},
            )
            assert response.status_code == 200, response.text

            (call,) = billing.calls
            assert call["monthly_price"] == Decimal("299.00")  # from the tiers table
            assert call["promo_monthly_price"] == Decimal("199.00") and call["promo_months"] == 3
            assert call["setup_fee"] == Decimal("750.00")

            tenant = console.overview(client, tenant_id)
            assert tenant["onboarding_status"] == "live"
            assert tenant["went_live_at"] is not None
            assert tenant["intake_address_active"] is True
            assert tenant["stripe_subscription_status"] == "active"
            assert (tenant["setup_fee_amount"], tenant["setup_fee_billing"], tenant["founding_price"]) == (
                "750.00", "stripe", True,
            )
            templates = [e["template"] for e in console.outbox(client, tenant_id)]
            assert "invite" in templates and "go_live" in templates  # invite sent because it hadn't been
            with platform_session() as session:
                job = session.execute(
                    text(
                        "SELECT job_type, status, run_at - now() > interval '6 days' AS in_a_week "
                        "FROM scheduled_jobs WHERE tenant_id = :t"
                    ),
                    {"t": tenant_id},
                ).one()
                event = session.execute(
                    text(
                        "SELECT constants_in_effect FROM tenant_lifecycle_events "
                        "WHERE tenant_id = :t AND event_type = 'onboarding_live'"
                    ),
                    {"t": tenant_id},
                ).scalar_one()
            assert tuple(job) == ("first_week_checkin", "pending", True)
            assert event["FIRST_WEEK_CHECKIN_DAYS"] == 7

            twice = client.post(
                f"/admin/tenants/{tenant_id}/go-live",
                headers=console.headers(),
                json={"setup_fee_amount": "750.00", "setup_fee_billing": "stripe"},
            )
            assert twice.status_code == 409 and twice.json()["detail"]["code"] == "ONB-006"
            assert len(billing.calls) == 1
        finally:
            _cleanup_jobs_and_alerts(tenant_id)


@requires_go_live_schema
@requires_console_schema
def test_a_setup_fee_invoiced_by_hand_is_not_sent_to_stripe(client, stripe, supabase_links, queue, billing):
    with _Console() as console:
        tenant_id = _ready_to_go_live(client, console)
        try:
            response = client.post(
                f"/admin/tenants/{tenant_id}/go-live",
                headers=console.headers(),
                json={
                    "setup_fee_amount": "1500",
                    "setup_fee_billing": "invoiced_manually",
                    "setup_fee_note": "Test invoice 1001",
                },
            )
            assert response.status_code == 200, response.text
            assert billing.calls[0]["setup_fee"] is None
            assert billing.calls[0]["promo_monthly_price"] is None  # founding not ticked
            tenant = console.overview(client, tenant_id)
            assert tenant["setup_fee_amount"] == "1500.00"
            assert tenant["setup_fee_billing"] == "invoiced_manually"
        finally:
            _cleanup_jobs_and_alerts(tenant_id)


@requires_go_live_schema
@requires_console_schema
def test_a_stripe_failure_leaves_the_tenant_not_live(client, stripe, supabase_links, queue, billing):
    with _Console() as console:
        tenant_id = _ready_to_go_live(client, console)
        billing.fail = True
        response = client.post(
            f"/admin/tenants/{tenant_id}/go-live",
            headers=console.headers(),
            json={"setup_fee_amount": "750", "setup_fee_billing": "stripe"},
        )
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "ONB-008"
        tenant = console.overview(client, tenant_id)
        assert tenant["onboarding_status"] == "test_batch_complete"
        assert tenant["intake_address_active"] is False
        assert "go_live" not in [e["template"] for e in console.outbox(client, tenant_id)]
        assert _scalar("SELECT count(*) FROM scheduled_jobs WHERE tenant_id = :t", t=tenant_id) == 0


@pytest.mark.parametrize("fee", [None, "", "abc", "-5", "10.005", "NaN"])
@requires_go_live_schema
@requires_console_schema
def test_the_setup_fee_must_be_a_real_amount(client, stripe, queue, billing, fee):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        body = {"setup_fee_billing": "stripe"}
        if fee is not None:
            body["setup_fee_amount"] = fee
        response = client.post(f"/admin/tenants/{tenant_id}/go-live", headers=console.headers(), json=body)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "ONB-007"
        assert billing.calls == []


@requires_go_live_schema
@requires_console_schema
def test_go_live_before_the_test_batch_is_complete_is_refused(client, stripe, queue, billing):
    with _Console() as console:
        tenant_id = _tenant_with_catalog(client, console)
        response = client.post(
            f"/admin/tenants/{tenant_id}/go-live",
            headers=console.headers(),
            json={"setup_fee_amount": "750", "setup_fee_billing": "stripe"},
        )
        assert response.status_code == 409 and response.json()["detail"]["code"] == "ONB-005"
        assert billing.calls == []


@requires_go_live_schema
@requires_console_schema
def test_the_onboarding_routes_are_404_to_a_tenant_user(client):
    for method, path in (
        ("get", "/admin/tenants/{t}/test-batch"),
        ("post", "/admin/tenants/{t}/test-batch/run"),
        ("post", "/admin/tenants/{t}/test-batch/complete"),
        ("get", "/admin/tenants/{t}/go-live"),
        ("post", "/admin/tenants/{t}/go-live"),
    ):
        response = getattr(client, method)(path.format(t=UUID(int=1)))
        assert response.status_code == 404
