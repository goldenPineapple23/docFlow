# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
The inbound Stripe webhook (CLAUDE.md Section 7.12; D-123; H11 / D-173), against
the real database and RLS.

What this proves:
- a signature is required and verified, and one stamped more than 300 s in the
  past OR the future is refused (the tolerance is a cross-clock comparison,
  D-170, so both directions are pinned);
- the same event id processed twice only applies once, and the event id is
  recorded in the same transaction as its effect (migration 0029's function);
- event order is respected: an event older than the saved state is recorded
  and not applied; a NULL saved time (every existing tenant) applies;
- an event in the same second as the saved state is not guessed at: Stripe's
  current state is fetched with NO transaction open (a lock probe, plus the
  negative check that it would fail inside one), a crash between the fetch and
  the save records nothing, and a newer event saved during the fetch is not
  overwritten;
- first_past_due_at is Stripe's event time, and `unpaid` does not reset it;
- an event stamped further into the future than the clock tolerance (D-176)
  is not applied and alerts the founder; one inside it applies;
- no tenant session can write `stripe_webhook_events`, and the function
  refuses a customer that is not the session's tenant's.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from docflow_core import billing_webhooks, external_services
from docflow_core.config import get_settings
from docflow_core.constants import STRIPE_CLOCK_TOLERANCE_SECONDS
from docflow_core.db import stripe_webhook_session, tenant_session
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from tests.test_console_api import _Console, _environment, _scalar, stripe  # noqa: F401
from tests.test_lifecycle_api import requires_lifecycle_schema  # noqa: F401

WEBHOOK_SECRET = "whsec_test_fixed"
FUNCTION_SIGNATURE = (
    "record_stripe_subscription_event(text, text, timestamptz, text, text, timestamptz, text)"
)


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _post(client, event: dict, *, signed_at: int | None = None):
    payload = json.dumps(event).encode()
    ts = int(time.time()) if signed_at is None else signed_at
    sig = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/stripe", content=payload, headers={"Stripe-Signature": f"t={ts},v1={sig}"}
    )


def _event_id(label: str) -> str:
    """`stripe_webhook_events` is global and idempotency-keyed on this id
    (Section 7.12) -- there's no tenant to scope a cleanup to, and it's
    never wiped between runs against the real staging database, so a fixed
    literal id would collide with the row its own previous run left behind.
    Unique per call instead; the tiny leftover rows are harmless, exactly
    like admin_actions or tenant_lifecycle_events already accumulating."""
    return f"evt_test_{label}_{uuid4().hex[:12]}"


def _subscription_event(
    *, event_id: str, customer: str, status: str, created: int, period_end: int | None = None
) -> dict:
    return {
        "id": event_id,
        "type": "customer.subscription.updated",
        "created": created,
        "data": {
            "object": {
                "object": "subscription",
                "id": "sub_evt",
                "customer": customer,
                "status": status,
                "current_period_end": period_end,
            }
        },
    }


def _ev(label: str, customer: str, status: str, created: int, period_end: int | None = None) -> dict:
    return _subscription_event(
        event_id=_event_id(label), customer=customer, status=status, created=created, period_end=period_end
    )


def _at(unix_seconds: int) -> datetime:
    return datetime.fromtimestamp(unix_seconds, tz=UTC)


def _tenant(client, console) -> tuple[str, str]:
    tenant_id = console.create_tenant(client).json()["tenant_id"]
    customer = _scalar("SELECT stripe_customer_id FROM tenants WHERE id = :t", t=tenant_id)
    return tenant_id, customer


def _column(tenant_id: str, column: str):
    return _scalar(f"SELECT {column} FROM tenants WHERE id = :t", t=tenant_id)


def _recorded(event_id: str) -> bool:
    # 0029's platform_admin_read: the only session that can read the table.
    return bool(_scalar("SELECT count(*) FROM stripe_webhook_events WHERE id = :e", e=event_id))


def _row_lock_is_free(tenant_id: str) -> bool:
    """The lock probe: from a second connection, try to take the tenant row
    FOR UPDATE NOWAIT. True if it could; False if something holds it."""
    try:
        with tenant_session(UUID(tenant_id)) as session:
            session.execute(
                text("SELECT id FROM tenants WHERE id = :id FOR UPDATE NOWAIT"), {"id": tenant_id}
            )
        return True
    except OperationalError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":  # lock_not_available
            return False
        raise


class _ForceRollback(Exception):
    """Raised inside a session to guarantee it rolls back even if the statement
    that should have been refused was not (D-165: a test that expects the
    database to refuse a write must never be able to leave a row behind)."""


def _fetched(status: str, period_end: int | None = None) -> external_services.SubscriptionResult:
    return external_services.SubscriptionResult(
        subscription_id="sub_evt", status=status, current_period_end=period_end
    )


# ── Signature ─────────────────────────────────────────────────────────────────


def test_a_request_with_no_signature_is_refused(client, _environment):
    response = client.post("/webhooks/stripe", content=b"{}")
    assert response.status_code == 400


def test_an_incorrectly_signed_request_is_refused(client, _environment):
    response = client.post(
        "/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "t=1,v1=deadbeef"}
    )
    assert response.status_code == 400


_NOT_A_SUBSCRIPTION = {
    "id": "evt_test_invoice",
    "type": "invoice.paid",
    "data": {"object": {"object": "invoice"}},
}


def test_a_signature_stamped_more_than_300_seconds_ago_is_refused(client, _environment):
    response = _post(client, _NOT_A_SUBSCRIPTION, signed_at=int(time.time()) - 301)
    assert response.status_code == 400


def test_a_signature_stamped_more_than_300_seconds_in_the_future_is_refused(client, _environment):
    """The tolerance compares Stripe's clock with ours (D-170). A future stamp is
    as suspect as a stale one -- it would let a captured request be replayed
    for longer -- so the check is symmetric, and this pins it."""
    response = _post(client, _NOT_A_SUBSCRIPTION, signed_at=int(time.time()) + 301)
    assert response.status_code == 400


def test_a_signature_inside_the_tolerance_is_accepted_either_side(client, _environment):
    for offset in (-290, 290):
        response = _post(client, _NOT_A_SUBSCRIPTION, signed_at=int(time.time()) + offset)
        assert response.status_code == 200, offset
        assert response.json()["outcome"] == "ignored"


def test_an_event_without_a_created_time_is_not_guessed_at(client, _environment):
    event = _ev("nocreated", "cus_x", "active", 0)
    del event["created"]
    assert _post(client, event).json()["outcome"] == "ignored"


# ── Matching, idempotency, the first event ────────────────────────────────────


@requires_lifecycle_schema
def test_an_event_for_an_unknown_customer_is_processed_but_matches_nothing(client, _environment):
    event = _subscription_event(
        event_id=_event_id("unmatched"), customer="cus_nobody", status="active", created=int(time.time())
    )
    response = _post(client, event)
    assert response.status_code == 200
    assert response.json()["outcome"] == "unmatched"


@requires_lifecycle_schema
def test_the_same_event_id_twice_only_applies_once(client, stripe, _environment):
    with _Console() as console:
        tenant_id, customer = _tenant(client, console)
        event = _subscription_event(
            event_id=_event_id("dup"), customer=customer, status="past_due", created=int(time.time())
        )
        first = _post(client, event)
        assert first.status_code == 200 and first.json()["outcome"] == "applied"
        assert _recorded(event["id"])
        second = _post(client, event)
        assert second.status_code == 200 and second.json()["outcome"] == "duplicate"

        alerts = _scalar(
            "SELECT count(*) FROM founder_alerts WHERE tenant_id = :t "
            "AND type = 'stripe_subscription_past_due'",
            t=tenant_id,
        )
        assert alerts == 1  # not raised twice


@requires_lifecycle_schema
def test_a_tenant_with_no_saved_event_time_applies_the_first_event(client, stripe, _environment):
    """Every tenant that existed before 0029 has stripe_status_event_at NULL."""
    with _Console() as console:
        tenant_id, customer = _tenant(client, console)
        assert _column(tenant_id, "stripe_status_event_at") is None
        created = int(time.time()) - 60
        event = _ev("first", customer, "active", created)
        assert _post(client, event).json()["outcome"] == "applied"
        assert _column(tenant_id, "stripe_subscription_status") == "active"
        assert _column(tenant_id, "stripe_status_event_at") == _at(created)


# ── Ordering ──────────────────────────────────────────────────────────────────


@requires_lifecycle_schema
def test_an_event_older_than_the_saved_state_is_recorded_and_not_applied(client, stripe, _environment):
    with _Console() as console:
        tenant_id, customer = _tenant(client, console)
        now = int(time.time())
        newer = _ev("newer", customer, "active", now)
        older = _ev("older", customer, "past_due", now - 30)
        assert _post(client, newer).json()["outcome"] == "applied"
        assert _post(client, older).json()["outcome"] == "stale"

        assert _column(tenant_id, "stripe_subscription_status") == "active"
        assert _column(tenant_id, "stripe_status_event_at") == _at(now)
        assert _column(tenant_id, "first_past_due_at") is None
        # Recorded as seen, so Stripe's retry of it is a no-op too.
        assert _recorded(older["id"])
        assert _post(client, older).json()["outcome"] == "duplicate"


@requires_lifecycle_schema
def test_the_event_id_is_recorded_in_the_same_transaction_as_the_status_write(client, stripe, _environment):
    """H11's defect was an id committed in one transaction and the update in
    another. Here the function has applied the event inside a transaction that
    is then rolled back: neither the status nor the event id survives. Committed,
    both land. They cannot come apart."""
    with _Console() as console:
        tenant_id, customer = _tenant(client, console)
        event = _ev("atomic", customer, "past_due", int(time.time()))

        with pytest.raises(_ForceRollback):
            with tenant_session(UUID(tenant_id)) as session:
                outcome = session.execute(
                    text(
                        "SELECT record_stripe_subscription_event("
                        ":e, 'customer.subscription.updated', :c, :cust, 'past_due', NULL, 'event')"
                    ),
                    {"e": event["id"], "c": _at(event["created"]), "cust": customer},
                ).scalar_one()
                assert outcome == "applied"
                raise _ForceRollback
        assert _column(tenant_id, "stripe_subscription_status") != "past_due"
        assert not _recorded(event["id"])

        assert billing_webhooks.process_event(event) == "applied"
        assert _column(tenant_id, "stripe_subscription_status") == "past_due"
        assert _recorded(event["id"])


# ── Stripe's clock against ours (D-170, D-176) ────────────────────────────────


@requires_lifecycle_schema
def test_an_event_stamped_beyond_the_clock_tolerance_is_not_applied_and_the_founder_is_told(
    client, stripe, _environment
):
    """Every accepted delivery proves Stripe's clock is within
    STRIPE_CLOCK_TOLERANCE_SECONDS of ours, so an event from further in the
    future is not a real one -- and saving its time would make every genuine
    event "stale" until our clock caught up. Checked on the database's clock."""
    with _Console() as console:
        tenant_id, customer = _tenant(client, console)
        future = int(time.time()) + STRIPE_CLOCK_TOLERANCE_SECONDS + 300
        event = _ev("future", customer, "canceled", future)

        assert _post(client, event).json()["outcome"] == "future_dated"
        assert _column(tenant_id, "stripe_subscription_status") != "canceled"
        assert _column(tenant_id, "stripe_status_event_at") is None
        assert not _recorded(event["id"])  # a corrected redelivery can still apply
        alerts = _scalar(
            "SELECT count(*) FROM founder_alerts WHERE tenant_id = :t AND type = 'stripe_event_future_dated'",
            t=tenant_id,
        )
        assert alerts == 1


@requires_lifecycle_schema
def test_an_event_inside_the_clock_tolerance_applies(client, stripe, _environment):
    with _Console() as console:
        tenant_id, customer = _tenant(client, console)
        slightly_ahead = int(time.time()) + STRIPE_CLOCK_TOLERANCE_SECONDS - 120
        assert _post(client, _ev("ahead", customer, "active", slightly_ahead)).json()["outcome"] == "applied"
        assert _column(tenant_id, "stripe_status_event_at") == _at(slightly_ahead)


# ── first_past_due_at ─────────────────────────────────────────────────────────


@requires_lifecycle_schema
def test_the_cure_clock_starts_at_stripes_event_time_and_unpaid_does_not_reset_it(
    client, stripe, _environment
):
    """7.15.4: non_payment's effective date is the first past_due event + the
    cure period. The event's time, not the moment this server processed it
    (D-170) -- here three days apart -- and not restarted by `unpaid`, which
    until 2c cleared it just as Stripe's retries ran out."""
    with _Console() as console:
        tenant_id, customer = _tenant(client, console)
        t1 = int((datetime.now(UTC) - timedelta(days=3)).timestamp())

        _post(client, _ev("pd1", customer, "past_due", t1))
        assert _column(tenant_id, "first_past_due_at") == _at(t1)

        # Still failing a day later: the clock does not move.
        _post(
            client,
            _ev("pd2", customer, "past_due", t1 + 86400),
        )
        assert _column(tenant_id, "first_past_due_at") == _at(t1)

        # Retries exhausted: unpaid keeps the original notice.
        _post(
            client,
            _ev("up", customer, "unpaid", t1 + 2 * 86400),
        )
        assert _column(tenant_id, "stripe_subscription_status") == "unpaid"
        assert _column(tenant_id, "first_past_due_at") == _at(t1)

        # Recovery clears it.
        period_end = int((datetime.now(UTC) + timedelta(days=30)).timestamp())
        _post(
            client,
            _ev("ok", customer, "active", t1 + 3 * 86400, period_end=period_end),
        )
        assert _column(tenant_id, "first_past_due_at") is None
        assert _column(tenant_id, "stripe_subscription_status") == "active"
        assert _column(tenant_id, "stripe_current_period_end") == _at(period_end)


# ── Same second: fetch outside any transaction ────────────────────────────────


@requires_lifecycle_schema
def test_a_same_second_event_fetches_stripes_state_with_no_transaction_holding_the_row(
    client, stripe, monkeypatch, _environment
):
    with _Console() as console:
        tenant_id, customer = _tenant(client, console)
        now = int(time.time())
        _post(client, _ev("s1", customer, "active", now))

        probe: dict = {}

        def fake_fetch(subscription_id):
            probe["lock_free"] = _row_lock_is_free(tenant_id)
            return _fetched("past_due")

        monkeypatch.setattr(external_services, "retrieve_subscription", fake_fetch)
        second = _ev("s2", customer, "canceled", now)
        assert billing_webhooks.process_event(second) == "refetched"

        assert probe == {"lock_free": True}
        # What Stripe said is saved -- not the event's own status, not a guess.
        assert _column(tenant_id, "stripe_subscription_status") == "past_due"
        assert _recorded(second["id"])


@requires_lifecycle_schema
def test_the_lock_probe_does_fail_while_the_decision_transaction_is_open(client, stripe, _environment):
    """The negative check for the test above: had the fetch run inside the
    transaction that decides, the probe would have seen the row locked. Without
    this, a probe that can never fail would prove nothing."""
    with _Console() as console:
        tenant_id, customer = _tenant(client, console)
        now = int(time.time())
        _post(client, _ev("n1", customer, "active", now))

        with tenant_session(UUID(tenant_id)) as session:
            outcome = session.execute(
                text(
                    "SELECT record_stripe_subscription_event("
                    ":e, 'customer.subscription.updated', :c, :cust, 'past_due', NULL, 'event')"
                ),
                {"e": _event_id("n2"), "c": _at(now), "cust": customer},
            ).scalar_one()
            assert outcome == "same_second"
            assert _row_lock_is_free(tenant_id) is False


@requires_lifecycle_schema
def test_a_crash_between_the_fetch_and_the_save_records_nothing_and_a_replay_applies(
    client, stripe, monkeypatch, _environment
):
    with _Console() as console:
        tenant_id, customer = _tenant(client, console)
        now = int(time.time())
        _post(client, _ev("c1", customer, "active", now))
        second = _ev("c2", customer, "past_due", now)

        def crashing_fetch(subscription_id):
            _fetched("past_due")  # the fetch itself succeeded ...
            raise RuntimeError("process died before saving")  # ... and nothing after it ran

        monkeypatch.setattr(external_services, "retrieve_subscription", crashing_fetch)
        with pytest.raises(RuntimeError):
            billing_webhooks.process_event(second)
        assert _column(tenant_id, "stripe_subscription_status") == "active"
        assert not _recorded(second["id"])

        # Stripe retries the same event: it is not a "duplicate", it applies.
        monkeypatch.setattr(external_services, "retrieve_subscription", lambda _id: _fetched("past_due"))
        assert billing_webhooks.process_event(second) == "refetched"
        assert _column(tenant_id, "stripe_subscription_status") == "past_due"
        assert _recorded(second["id"])


@requires_lifecycle_schema
def test_a_newer_event_saved_during_the_fetch_is_not_overwritten(client, stripe, monkeypatch, _environment):
    with _Console() as console:
        tenant_id, customer = _tenant(client, console)
        now = int(time.time())
        _post(client, _ev("o1", customer, "active", now))
        same_second = _ev("o2", customer, "past_due", now)
        newer = _ev("o3", customer, "canceled", now + 10)

        def fetch_overtaken_by_a_newer_event(subscription_id):
            assert billing_webhooks.process_event(newer) == "applied"
            return _fetched("past_due")  # what Stripe said a moment before `newer`

        monkeypatch.setattr(external_services, "retrieve_subscription", fetch_overtaken_by_a_newer_event)
        assert billing_webhooks.process_event(same_second) == "superseded"

        assert _column(tenant_id, "stripe_subscription_status") == "canceled"
        assert _column(tenant_id, "stripe_status_event_at") == _at(now + 10)
        assert _recorded(same_second["id"])  # seen; its retry is a no-op


# ── Who can write the idempotency table ───────────────────────────────────────


@requires_lifecycle_schema
def test_no_session_can_insert_into_stripe_webhook_events_directly(client, stripe, _environment):
    """Only the function writes it (D-173). A tenant session inserting an event
    id would mark a real Stripe event as seen and turn its retry into a no-op."""
    with _Console() as console:
        tenant_id, _customer = _tenant(client, console)
        insert = text("INSERT INTO stripe_webhook_events (id, type) VALUES (:id, 'forged')")

        with pytest.raises(DBAPIError):
            with tenant_session(UUID(tenant_id)) as session:
                session.execute(insert, {"id": _event_id("forged_tenant")})
                raise _ForceRollback
        with pytest.raises(DBAPIError):
            with stripe_webhook_session() as session:
                session.execute(insert, {"id": _event_id("forged_lookup")})
                raise _ForceRollback


@requires_lifecycle_schema
def test_the_function_refuses_a_customer_that_is_not_the_session_tenants(client, stripe, _environment):
    with _Console() as console:
        tenant_a, _customer_a = _tenant(client, console)
        _tenant_b, customer_b = _tenant(client, console)
        with pytest.raises(DBAPIError, match="not this tenant"):
            with tenant_session(UUID(tenant_a)) as session:
                session.execute(
                    text(
                        "SELECT record_stripe_subscription_event("
                        ":e, 'customer.subscription.updated', now(), :cust, 'canceled', NULL, 'event')"
                    ),
                    {"e": _event_id("cross"), "cust": customer_b},
                )
                raise _ForceRollback
        assert _column(_tenant_b, "stripe_subscription_status") != "canceled"


@requires_lifecycle_schema
def test_the_function_refuses_to_run_outside_a_tenant_session(_environment):
    with pytest.raises(DBAPIError, match="inside a tenant session"):
        with stripe_webhook_session() as session:
            session.execute(
                text(
                    "SELECT record_stripe_subscription_event("
                    ":e, 'customer.subscription.updated', now(), 'cus_x', 'active', NULL, 'event')"
                ),
                {"e": _event_id("nosession")},
            )
            raise _ForceRollback


@requires_lifecycle_schema
def test_only_the_app_role_may_execute_the_function(_environment):
    """REVOKE from PUBLIC, and from Supabase's anon/authenticated roles, which
    otherwise get EXECUTE on every new function in `public` -- i.e. callable
    from the browser's public key through the REST API."""
    with stripe_webhook_session() as session:
        acl = session.execute(
            text("SELECT proacl::text[] FROM pg_proc WHERE proname = 'record_stripe_subscription_event'")
        ).scalar_one()
        roles = [
            r for (r,) in session.execute(
                text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")
            )
        ]
        can = {
            role: session.execute(
                text("SELECT has_function_privilege(:role, :fn, 'EXECUTE')"),
                {"role": role, "fn": FUNCTION_SIGNATURE},
            ).scalar_one()
            for role in [*roles, "docflow_app"]
        }
    assert not any(entry.startswith("=") for entry in acl or []), acl  # no PUBLIC grant
    assert can.pop("docflow_app") is True
    assert not any(can.values()), can  # anon / authenticated, where they exist
