"""
Stage 3d: the dispatcher's turn-taking rule (`dispatch.choose`) and the
provider backoff (`model_provider.retry_delay_seconds`), as pure functions.

The same rules against the real database and a real Celery worker are in
apps/worker/tests/test_dispatch_db.py and test_dispatch_real_worker.py; these
pin the arithmetic so a change to it is a visible diff.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from docflow_core import dispatch, model_provider
from docflow_core.constants import PROVIDER_RETRY_MINUTES, TENANT_IN_FLIGHT_CAP
from docflow_core.dispatch import Ready, TenantQueue, choose

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _queue(tenant: UUID, n: int, *, in_flight: int = 0, start_min: int = 0, lane: str = "interactive"):
    return TenantQueue(
        in_flight=in_flight,
        ready=[Ready(tenant, uuid4(), lane, T0 + timedelta(minutes=start_min + i)) for i in range(n)],
    )


def test_a_newcomer_gets_the_next_slot_ahead_of_a_backfill():
    """H4: tenant B arrives during A's 500-file upload and goes first."""
    a, b = uuid4(), uuid4()
    tenants = {a: _queue(a, 500, in_flight=1), b: _queue(b, 1, start_min=60)}
    chosen = choose(tenants, free_slots=1)
    assert [c.tenant_id for c in chosen] == [b]


def test_with_one_slot_the_turns_go_round_instead_of_draining_the_backfill():
    """The defect found while building 3d: with one slot (staging), whenever
    the slot frees both tenants have 0 in flight. Tie-breaking on the oldest
    ready document gave every slot to the backfill; the tenant served least
    recently must go instead."""
    a, b = uuid4(), uuid4()
    backfill = _queue(a, 300, start_min=0)
    backfill.last_dispatched_at = T0 + timedelta(hours=5)  # A was given the last slot
    newcomer = _queue(b, 1, start_min=200)  # arrived later than A's next document
    assert [c.tenant_id for c in choose({a: backfill, b: newcomer}, free_slots=1)] == [b]

    # Both served before: the one served longer ago goes first.
    newcomer.last_dispatched_at = T0 + timedelta(hours=1)
    assert [c.tenant_id for c in choose({a: backfill, b: newcomer}, free_slots=1)] == [b]


def test_slots_alternate_fewest_in_flight_first_ties_to_the_oldest():
    a, b = uuid4(), uuid4()
    tenants = {a: _queue(a, 5), b: _queue(b, 5, start_min=1)}
    chosen = choose(tenants, free_slots=4)
    assert [c.tenant_id for c in chosen] == [a, b, a, b]


def test_a_lone_backfill_uses_every_free_slot():
    """No one else is ready: the cap never leaves a slot idle."""
    a = uuid4()
    chosen = choose({a: _queue(a, 50)}, free_slots=8)
    assert len(chosen) == 8 and {c.tenant_id for c in chosen} == {a}


def test_a_tenant_at_the_cap_waits_while_another_has_something_ready():
    a, b = uuid4(), uuid4()
    tenants = {
        a: _queue(a, 50, in_flight=TENANT_IN_FLIGHT_CAP),
        b: _queue(b, 3, in_flight=TENANT_IN_FLIGHT_CAP - 1, start_min=30),
    }
    chosen = choose(tenants, free_slots=1)
    assert [c.tenant_id for c in chosen] == [b]


def test_when_everyone_ready_is_at_the_cap_the_slot_still_goes_fewest_first():
    """Settled while building (BUILD-STATUS 3d): no slot idles while a
    document can go -- the cap only orders who goes first."""
    a, b = uuid4(), uuid4()
    tenants = {
        a: _queue(a, 5, in_flight=TENANT_IN_FLIGHT_CAP + 1),
        b: _queue(b, 5, in_flight=TENANT_IN_FLIGHT_CAP, start_min=10),
    }
    chosen = choose(tenants, free_slots=1)
    assert [c.tenant_id for c in chosen] == [b]


def test_gap_3_a_tenant_with_nothing_ready_now_never_blocks_a_capped_one():
    """Founder's gap 3: "waiting" in the cap rule means ready now. A tenant
    whose only document sits out a backoff (retry_at ahead) is not a
    candidate at all -- dispatch_candidates leaves it out -- so a capped
    tenant uses the idle slots."""
    a, b = uuid4(), uuid4()
    tenants = {
        a: _queue(a, 10, in_flight=TENANT_IN_FLIGHT_CAP),
        b: TenantQueue(in_flight=0, ready=[]),  # only a document with a future retry_at
    }
    chosen = choose(tenants, free_slots=3)
    assert [c.tenant_id for c in chosen] == [a, a, a]


def test_within_a_tenant_interactive_goes_before_its_own_bulk():
    """Q3: the order comes from dispatch_candidates (interactive first, then
    oldest) and choose keeps it."""
    a = uuid4()
    old_bulk = Ready(a, uuid4(), "bulk", T0)
    new_single = Ready(a, uuid4(), "interactive", T0 + timedelta(hours=2))
    tenants = {a: TenantQueue(in_flight=0, ready=[new_single, old_bulk])}
    chosen = [c.document_id for c in choose(tenants, free_slots=2)]
    assert chosen == [new_single.document_id, old_bulk.document_id]


def test_no_free_slot_sends_nothing():
    a = uuid4()
    assert choose({a: _queue(a, 3)}, free_slots=0) == []
    assert choose({a: _queue(a, 3)}, free_slots=-2) == []


def test_the_provider_backoff_steps_1_2_4_8_then_every_15_minutes():
    assert PROVIDER_RETRY_MINUTES == (1, 2, 4, 8, 15)
    # (seconds already waited, minutes until the next try)
    for waited, minutes in [(None, 1), (0, 1), (65, 2), (185, 4), (425, 8), (905, 15), (3 * 3600, 15)]:
        assert model_provider.retry_delay_seconds(waited) == minutes * 60, waited


def test_a_429s_retry_after_is_the_floor_of_the_backoff():
    assert model_provider.retry_delay_seconds(0, retry_after_seconds=300) == 300
    assert model_provider.retry_delay_seconds(0, retry_after_seconds=5) == 60


def test_the_heartbeat_is_stale_past_the_limit_or_when_it_never_ran():
    from docflow_core.constants import DISPATCHER_STALE_MIN

    assert dispatch.DispatcherStatus(None, None, 0).stale
    assert not dispatch.DispatcherStatus(T0, DISPATCHER_STALE_MIN * 60, 3).stale
    assert dispatch.DispatcherStatus(T0, DISPATCHER_STALE_MIN * 60 + 1, 3).stale
