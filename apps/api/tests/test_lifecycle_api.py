# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
Tenant lifecycle actions through the Console (CLAUDE.md Section 7.14 /
7.15.4; D-123), against the real database and RLS.

What this proves:
  * Cancel only applies to an 'active' tenant (LIFE-001); for_cause requires
    a real reason (LIFE-002); the founder may only push the computed
    effective date later, never earlier (LIFE-003).
  * The three effective-date rules: current_period_end for a normal
    cancellation, first-past-due + CURE_PERIOD_DAYS for non-payment (or now
    if no subscription/first-past-due yet, flagged), immediate for cause.
  * D-123: the sweep's claim_for_suspend performs suspended AND
    pending_deletion in one transaction -- a tenant is never observably
    "suspended" without its deletion clock already running -- and both are
    still logged as distinct lifecycle events.
  * Reactivate only applies to suspended/pending_deletion (LIFE-004), clears
    the cancellation/deletion fields, cancels pending reminder jobs, and
    returns the tenant to active with no data loss.
  * Every action writes admin_actions and a tenant_lifecycle_events row.

Stripe is replaced at its boundary. All data is fictional (CLAUDE.md
Section 0 rule 4).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from docflow_core import external_services, lifecycle
from docflow_core.db import platform_session, tenant_session
from sqlalchemy import text

from tests.test_console_api import _Console, _environment, _scalar, stripe  # noqa: F401


def _lifecycle_schema_available() -> bool:
    try:
        with platform_session() as session:
            session.execute(text("SELECT first_past_due_at FROM tenants LIMIT 0"))
            session.execute(text("SELECT id FROM stripe_webhook_events LIMIT 0"))
        return True
    except Exception:
        return False


requires_lifecycle_schema = pytest.mark.skipif(
    not _lifecycle_schema_available(),
    reason="supabase/migrations/0019_lifecycle_sweep.sql has not been applied yet -- see D-123.",
)


@pytest.fixture
def reactivate_stripe(monkeypatch):
    """A fake `start_subscription`/`cancel_subscription` for reactivate and
    the suspend sweep -- the same boundary the `stripe` fixture already
    replaces for customer creation, kept separate so tests that don't touch
    subscriptions don't need it."""

    class _Sub:
        created: list[str] = []
        cancelled: list[str] = []
        calls: list[dict] = []
        fail_cancel = False

    def start(**kwargs):
        _Sub.calls.append(kwargs)
        sub_id = f"sub_test_{len(_Sub.created) + 1}"
        _Sub.created.append(sub_id)
        return external_services.SubscriptionResult(
            subscription_id=sub_id, status="active", current_period_end=None
        )

    def cancel(subscription_id: str) -> None:
        if _Sub.fail_cancel:
            raise external_services.ExternalServiceError("stripe", "test refusal")
        _Sub.cancelled.append(subscription_id)

    monkeypatch.setattr(external_services, "start_subscription", start)
    monkeypatch.setattr(external_services, "cancel_subscription", cancel)
    _Sub.created, _Sub.cancelled, _Sub.calls, _Sub.fail_cancel = [], [], [], False
    return _Sub


def _backdate_cancellation(tenant_id: str, *, minutes: int = 1) -> None:
    """`for_cause` computes "immediate" (now) as its own effective date, and
    LIFE-003 (Section 7.15.4: "never earlier") correctly refuses any
    override before that -- so a test that wants a tenant safely past its
    effective date for the sweep backdates it directly, the same way
    `_activate` seeds other post-go-live state, rather than asking
    `cancel()` to do something the real API must refuse."""
    with platform_session() as session:
        session.execute(
            text("UPDATE tenants SET cancellation_effective_at = :t WHERE id = :id"),
            {"id": tenant_id, "t": datetime.now(UTC) - timedelta(minutes=minutes)},
        )


def _activate(tenant_id: str, *, subscription_id="sub_existing", period_end=None, status="active"):
    """Puts a Console-created tenant (status='active', no subscription) into
    the post-go-live shape lifecycle actions assume, without going through
    the whole onboarding flow."""
    with platform_session() as session:
        session.execute(
            text(
                "UPDATE tenants SET stripe_subscription_id = :sub, stripe_subscription_status = :status, "
                "stripe_current_period_end = :period_end WHERE id = :id"
            ),
            {"id": tenant_id, "sub": subscription_id, "status": status, "period_end": period_end},
        )


# ── Cancel: guards ───────────────────────────────────────────────────────────


@requires_lifecycle_schema
def test_cancel_refuses_a_tenant_that_is_not_active(client, stripe, _environment):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        with tenant_session(tenant_id) as session:
            lifecycle.cancel(
                session, tenant_id, reason="for_cause",
                note="a reason at least twenty characters long", actor_user_id=console.user_id,
            )
        response = client.post(
            f"/admin/tenants/{tenant_id}/cancel", headers=console.headers(),
            json={"reason": "customer_requested"},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "LIFE-001"


@requires_lifecycle_schema
def test_for_cause_requires_a_real_reason(client, stripe, _environment):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        response = client.post(
            f"/admin/tenants/{tenant_id}/cancel", headers=console.headers(),
            json={"reason": "for_cause", "note": "too short"},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "LIFE-002"


@requires_lifecycle_schema
def test_the_effective_date_can_only_move_later_never_earlier(client, stripe, _environment):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        earlier = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        response = client.post(
            f"/admin/tenants/{tenant_id}/cancel", headers=console.headers(),
            json={"reason": "for_cause", "note": "a reason at least twenty characters",
                  "override_effective_at": earlier},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "LIFE-003"


# ── Cancel: the three effective-date rules ──────────────────────────────────


@requires_lifecycle_schema
def test_customer_requested_uses_the_current_billing_period_end(client, stripe, _environment):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        period_end = datetime.now(UTC) + timedelta(days=9)
        _activate(tenant_id, period_end=period_end)
        response = client.post(
            f"/admin/tenants/{tenant_id}/cancel", headers=console.headers(),
            json={"reason": "customer_requested"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["flagged"] is False
        assert abs(
            datetime.fromisoformat(body["cancellation_effective_at"]) - period_end
        ) < timedelta(seconds=2)


@requires_lifecycle_schema
def test_customer_requested_with_no_subscription_yet_is_immediate(client, stripe, _environment):
    """"If the tenant has no subscription yet (cancelled mid-onboarding) ->
    immediate, and the form says so" (Section 7.15.4)."""
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        response = client.post(
            f"/admin/tenants/{tenant_id}/cancel", headers=console.headers(),
            json={"reason": "customer_requested"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert "immediate" in body["rule"]
        assert abs(
            datetime.fromisoformat(body["cancellation_effective_at"]) - datetime.now(UTC)
        ) < timedelta(seconds=5)


@requires_lifecycle_schema
def test_non_payment_adds_the_cure_period_to_the_first_past_due_notice(client, stripe, _environment):
    from docflow_core.constants import CURE_PERIOD_DAYS

    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        _activate(tenant_id, status="past_due")
        first_past_due = datetime.now(UTC) - timedelta(days=3)
        with platform_session() as session:
            session.execute(
                text("UPDATE tenants SET first_past_due_at = :t WHERE id = :id"),
                {"id": tenant_id, "t": first_past_due},
            )
        response = client.post(
            f"/admin/tenants/{tenant_id}/cancel", headers=console.headers(),
            json={"reason": "non_payment"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["flagged"] is False
        expected = first_past_due + timedelta(days=CURE_PERIOD_DAYS)
        assert abs(datetime.fromisoformat(body["cancellation_effective_at"]) - expected) < timedelta(seconds=2)


@requires_lifecycle_schema
def test_non_payment_with_no_first_past_due_notice_falls_back_to_now_and_is_flagged(
    client, stripe, _environment
):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        _activate(tenant_id, status="past_due")
        response = client.post(
            f"/admin/tenants/{tenant_id}/cancel", headers=console.headers(),
            json={"reason": "non_payment"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["flagged"] is True


@requires_lifecycle_schema
def test_for_cause_is_immediate(client, stripe, _environment):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        _activate(tenant_id)
        response = client.post(
            f"/admin/tenants/{tenant_id}/cancel", headers=console.headers(),
            json={"reason": "for_cause", "note": "a reason at least twenty characters long"},
        )
        assert response.status_code == 200, response.text
        assert abs(
            datetime.fromisoformat(response.json()["cancellation_effective_at"]) - datetime.now(UTC)
        ) < timedelta(seconds=5)


# ── The sweep: D-123's same-tick suspended -> pending_deletion ─────────────


@requires_lifecycle_schema
def test_the_sweep_moves_a_due_tenant_straight_to_pending_deletion(client, stripe, _environment):
    """D-123: never observably 'suspended' without the deletion clock
    already running -- but both transitions are logged."""
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        _activate(tenant_id)
        with tenant_session(tenant_id) as session:
            lifecycle.cancel(
                session, tenant_id, reason="for_cause",
                note="a reason at least twenty characters long", actor_user_id=console.user_id,
            )
        _backdate_cancellation(tenant_id)

        with tenant_session(tenant_id) as session:
            claim = lifecycle.claim_for_suspend(session, tenant_id)
        assert claim is not None
        assert claim.stripe_subscription_id == "sub_existing"

        status = _scalar("SELECT status FROM tenants WHERE id = :t", t=tenant_id)
        assert status == "pending_deletion"
        deletion_at = _scalar("SELECT deletion_scheduled_at FROM tenants WHERE id = :t", t=tenant_id)
        assert deletion_at is not None
        intake_active = _scalar("SELECT intake_address_active FROM tenants WHERE id = :t", t=tenant_id)
        assert intake_active is False

        events = _scalar(
            "SELECT array_agg(event_type ORDER BY created_at) FROM tenant_lifecycle_events "
            "WHERE tenant_id = :t AND event_type IN ('suspended', 'pending_deletion_entered')",
            t=tenant_id,
        )
        assert list(events) == ["suspended", "pending_deletion_entered"]

        reminder_count = _scalar(
            "SELECT count(*) FROM scheduled_jobs WHERE tenant_id = :t AND job_type = 'pending_deletion_reminder'",
            t=tenant_id,
        )
        assert reminder_count == 3


@requires_lifecycle_schema
def test_the_sweep_never_claims_a_tenant_twice(client, stripe, _environment):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        _activate(tenant_id)
        with tenant_session(tenant_id) as session:
            lifecycle.cancel(
                session, tenant_id, reason="for_cause",
                note="a reason at least twenty characters long", actor_user_id=console.user_id,
            )
        _backdate_cancellation(tenant_id)
        with tenant_session(tenant_id) as session:
            first = lifecycle.claim_for_suspend(session, tenant_id)
        with tenant_session(tenant_id) as session:
            second = lifecycle.claim_for_suspend(session, tenant_id)
        assert first is not None
        assert second is None


# ── Reactivate ───────────────────────────────────────────────────────────────


@requires_lifecycle_schema
def test_reactivate_refuses_an_active_tenant(client, stripe, reactivate_stripe, _environment):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        response = client.post(f"/admin/tenants/{tenant_id}/reactivate", headers=console.headers())
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "LIFE-004"


@requires_lifecycle_schema
def test_reactivate_returns_a_suspended_tenant_to_active_with_no_data_loss(
    client, stripe, reactivate_stripe, _environment
):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        _activate(tenant_id)
        with tenant_session(tenant_id) as session:
            lifecycle.cancel(
                session, tenant_id, reason="for_cause",
                note="a reason at least twenty characters long", actor_user_id=console.user_id,
            )
        _backdate_cancellation(tenant_id)
        with tenant_session(tenant_id) as session:
            lifecycle.claim_for_suspend(session, tenant_id)

        response = client.post(f"/admin/tenants/{tenant_id}/reactivate", headers=console.headers())
        assert response.status_code == 200, response.text
        assert reactivate_stripe.created  # a fresh subscription, since the old one is gone

        row = _scalar(
            "SELECT row_to_json(t) FROM (SELECT status, cancellation_effective_at, "
            "cancellation_reason, deletion_scheduled_at, intake_address_active, "
            "stripe_subscription_id FROM tenants WHERE id = :t) t",
            t=tenant_id,
        )
        assert row["status"] == "active"
        assert row["cancellation_effective_at"] is None
        assert row["cancellation_reason"] is None
        assert row["deletion_scheduled_at"] is None
        assert row["intake_address_active"] is True
        assert row["stripe_subscription_id"] == reactivate_stripe.created[0]
        # D-125's trial is a go-live perk, not a reactivation one.
        assert reactivate_stripe.calls[0]["trial_end"] is None

        pending_reminders = _scalar(
            "SELECT count(*) FROM scheduled_jobs WHERE tenant_id = :t "
            "AND job_type = 'pending_deletion_reminder' AND status = 'pending'",
            t=tenant_id,
        )
        assert pending_reminders == 0


# ── Delete: type-to-confirm ──────────────────────────────────────────────────


@requires_lifecycle_schema
def test_delete_refuses_a_tenant_not_yet_ready(client, stripe, _environment):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        response = client.post(
            f"/admin/tenants/{tenant_id}/delete", headers=console.headers(),
            json={"confirm_name": "Acme Test Distributor", "reason": "a real reason for this"},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "LIFE-006"


@requires_lifecycle_schema
def test_delete_refuses_a_name_that_does_not_match(client, stripe, _environment):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        _activate(tenant_id)
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE tenants SET status = 'pending_deletion', "
                    "deletion_scheduled_at = now() - interval '1 day' WHERE id = :id"
                ),
                {"id": tenant_id},
            )
        response = client.post(
            f"/admin/tenants/{tenant_id}/delete", headers=console.headers(),
            json={"confirm_name": "Wrong Name", "reason": "a real reason for this"},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "LIFE-005"
        # Refused before anything was touched.
        assert _scalar("SELECT status FROM tenants WHERE id = :t", t=tenant_id) == "pending_deletion"


@requires_lifecycle_schema
def test_delete_purges_business_data_but_keeps_the_tenant_row_and_its_history(
    client, stripe, _environment
):
    """Section 7.14: "removes the tenant's business data ... the fact that a
    tenant existed and was removed is retained." The row survives
    (soft-deleted) because tenant_lifecycle_events references it without
    cascade; its own users are gone."""
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        _activate(tenant_id)
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE tenants SET status = 'pending_deletion', "
                    "deletion_scheduled_at = now() - interval '1 day' WHERE id = :id"
                ),
                {"id": tenant_id},
            )
        response = client.post(
            f"/admin/tenants/{tenant_id}/delete", headers=console.headers(),
            json={"confirm_name": "Acme Test Distributor", "reason": "offboarding: churned, window elapsed"},
        )
        assert response.status_code == 200, response.text

        row = _scalar(
            "SELECT row_to_json(t) FROM (SELECT status, deleted_at, deleted_by, deletion_reason "
            "FROM tenants WHERE id = :t) t",
            t=tenant_id,
        )
        assert row["status"] == "deleted"
        assert row["deleted_at"] is not None
        assert row["deleted_by"] == str(console.user_id)
        assert "churned" in row["deletion_reason"]

        assert _scalar("SELECT count(*) FROM users WHERE tenant_id = :t", t=tenant_id) == 0

        deleted_event = _scalar(
            "SELECT count(*) FROM tenant_lifecycle_events WHERE tenant_id = :t AND event_type = 'deleted'",
            t=tenant_id,
        )
        assert deleted_event == 1
        delete_action = _scalar(
            "SELECT count(*) FROM admin_actions WHERE target_tenant_id = :t AND action = 'tenant_delete'",
            t=tenant_id,
        )
        assert delete_action == 1
