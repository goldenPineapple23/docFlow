"""
The dispatcher: the only thing that puts a waiting document on the queue
(Stage 3d, review H4; BUILD-STATUS "3d detailed design" item 2 and "3d --
APPROVED WITH CHANGES").

Before 3d every intake path sent its documents straight to the queue, so one
tenant's 500-file upload put 500 jobs ahead of every other tenant's next
order. Now a document waits as `pending` with `dispatched_at` NULL, and a
pass of the dispatcher decides what goes next:

1. A transaction-level advisory lock: passes never overlap; a second one
   returns at once.
2. The heartbeat is written -- on every pass, holding or not, so a dispatcher
   holding for a provider outage never looks like one that has stopped.
3. If the model provider is down, nothing goes except one probe every
   PROVIDER_PROBE_MINUTES: the oldest document waiting on the provider.
4. Otherwise: free slots = the in-flight target (DISPATCH_IN_FLIGHT_TARGET,
   the worker's document slots) minus what is in flight (dispatched or
   processing). Each slot goes to the tenant with the **fewest documents in
   flight**; a tie goes to the tenant **served least recently** (never served
   first, then the earliest latest dispatch), and only then to the tenant
   whose oldest ready document arrived first. Within a tenant: interactive
   lane before bulk, then oldest first.
   (Found while building, flagged to the founder: the agreed tie-break was
   "oldest ready document first". With one slot -- staging -- both tenants
   have 0 in flight whenever the slot frees, so that rule handed every slot
   to the backfill's older documents and a newcomer waited behind all of
   them. "Served least recently" is what makes the turns go round.)
   **A tenant at TENANT_IN_FLIGHT_CAP is passed over while another tenant
   has a document that may go now** (gap 3: `retry_at` NULL or past, so a
   document sitting out a backoff never blocks anyone). When every tenant
   with something ready is at the cap -- or only one has anything -- the slot
   still goes, fewest in flight first: no slot sits idle while a document
   can go.
5. Each chosen document is marked dispatched by compare-and-set, the
   transaction commits, and only then is it sent (a job must never run
   against a state it can't see). A send that fails puts it back to waiting.

Every read and write across tenants goes through migration 0035's SECURITY
DEFINER functions (founder, Q8), which return ids, counts and times only.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import founder_alerts, model_provider
from docflow_core.config import get_settings
from docflow_core.constants import DISPATCHER_STALE_MIN, PROVIDER_PROBE_MINUTES, TENANT_IN_FLIGHT_CAP
from docflow_core.db import dispatcher_session, function_session

logger = logging.getLogger(__name__)

# The pass's advisory lock. Distinct from the test suites' lock (5_150_292_026).
DISPATCH_LOCK_KEY = 3_352_026_100

Send = Callable[[UUID, UUID], None]


@dataclass(frozen=True)
class Ready:
    tenant_id: UUID
    document_id: UUID
    lane: str
    arrived_at: datetime


@dataclass
class TenantQueue:
    in_flight: int = 0
    ready: list[Ready] = field(default_factory=list)
    # When this tenant was last given a slot (its latest dispatched_at);
    # None if never.
    last_dispatched_at: datetime | None = None


@dataclass
class PassResult:
    ran: bool = False
    holding: bool = False
    probe: bool = False
    sent: list[tuple[UUID, UUID]] = field(default_factory=list)
    send_failed: list[UUID] = field(default_factory=list)


def choose(tenants: dict[UUID, TenantQueue], free_slots: int, cap: int = TENANT_IN_FLIGHT_CAP) -> list[Ready]:
    """
    The turn-taking rule (module docstring, step 4), pure so it can be tested
    on its own. `tenants[t].ready` is already in the tenant's own order.
    """
    in_flight = {t: q.in_flight for t, q in tenants.items()}
    remaining = {t: list(q.ready) for t, q in tenants.items() if q.ready}
    oldest = {t: min(r.arrived_at for r in ready) for t, ready in remaining.items()}
    # Least recently served first: never served (0, ...), then by the time of
    # the last dispatch (1, ...); a tenant served in this pass goes after all
    # of those (2, n), in the order it was served.
    served: dict[UUID, tuple[int, float]] = {
        t: (0, 0.0) if q.last_dispatched_at is None else (1, q.last_dispatched_at.timestamp())
        for t, q in tenants.items()
    }
    chosen: list[Ready] = []
    while free_slots > 0 and remaining:
        below_cap = [t for t in remaining if in_flight[t] < cap]
        # A tenant at the cap waits while anyone else has something ready;
        # if no one is below it, the slot is not left idle.
        pool = below_cap or list(remaining)
        tenant = min(pool, key=lambda t: (in_flight[t], served[t], oldest[t], str(t)))
        chosen.append(remaining[tenant].pop(0))
        in_flight[tenant] += 1
        served[tenant] = (2, float(len(chosen)))
        free_slots -= 1
        if not remaining[tenant]:
            del remaining[tenant]
    return chosen


def _read_queues(session: Session, per_tenant: int) -> dict[UUID, TenantQueue]:
    tenants: dict[UUID, TenantQueue] = {}
    rows = session.execute(
        text(
            "SELECT tenant_id, in_flight, last_dispatched_at, document_id, lane, arrived_at "
            "FROM public.dispatch_candidates(:n)"
        ),
        {"n": per_tenant},
    ).mappings().all()
    for row in rows:
        tenant_id = UUID(str(row["tenant_id"]))
        queue = tenants.setdefault(tenant_id, TenantQueue())
        queue.in_flight = int(row["in_flight"])
        queue.last_dispatched_at = row["last_dispatched_at"]
        if row["document_id"] is not None:
            queue.ready.append(
                Ready(tenant_id, UUID(str(row["document_id"])), str(row["lane"]), row["arrived_at"])
            )
    order = {"interactive": 0, "bulk": 1}
    for queue in tenants.values():
        queue.ready.sort(key=lambda r: (order.get(r.lane, 1), r.arrived_at, str(r.document_id)))
    return tenants


def run_pass(send: Send, *, target: int | None = None, cap: int = TENANT_IN_FLIGHT_CAP) -> PassResult:
    """One pass (module docstring). `send(tenant_id, document_id)` puts the
    document task on the queue; it is called only after the commit."""
    target = get_settings().dispatch_in_flight_target if target is None else target
    result = PassResult()
    to_send: list[Ready] = []
    with dispatcher_session() as session:
        got = session.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": DISPATCH_LOCK_KEY}
        ).scalar_one()
        if not got:
            return result
        result.ran = True
        session.execute(text("SELECT public.dispatcher_heartbeat()"))
        provider = session.execute(
            text("SELECT status FROM public.provider_state(:p)"), {"p": model_provider.PROVIDER}
        ).first()
        if provider is not None and provider[0] == "down":
            result.holding = True
            probe_due = session.execute(
                text("SELECT public.provider_take_probe(:p, :minutes)"),
                {"p": model_provider.PROVIDER, "minutes": PROVIDER_PROBE_MINUTES},
            ).scalar_one()
            if probe_due:
                row = session.execute(
                    text("SELECT tenant_id, document_id FROM public.probe_candidate()")
                ).first()
                if row is not None:
                    to_send = [Ready(UUID(str(row[0])), UUID(str(row[1])), "interactive", datetime.min)]
                    result.probe = True
        else:
            tenants = _read_queues(session, max(target, 1))
            in_flight = sum(q.in_flight for q in tenants.values())
            to_send = choose(tenants, target - in_flight, cap)
        marked: list[Ready] = []
        for item in to_send:
            if session.execute(
                text("SELECT public.mark_dispatched(:id)"), {"id": str(item.document_id)}
            ).scalar_one():
                marked.append(item)

    for item in marked:
        try:
            send(item.tenant_id, item.document_id)
            result.sent.append((item.tenant_id, item.document_id))
        except Exception as exc:  # noqa: BLE001 -- back to waiting, logged
            logger.error(
                "dispatch_send_failed document_id=%s error_type=%s", item.document_id, type(exc).__name__
            )
            result.send_failed.append(item.document_id)
            try:
                with dispatcher_session() as session:
                    session.execute(
                        text("SELECT public.clear_dispatched(:id)"), {"id": str(item.document_id)}
                    )
            except Exception:  # noqa: BLE001 -- the stuck sweep returns it after the timeout
                logger.exception("dispatch_unmark_failed document_id=%s", item.document_id)
    # Ids and counts only (Section 7.10).
    if result.sent or result.send_failed or result.probe:
        logger.info(
            "dispatch_pass sent=%d send_failed=%d holding=%s probe=%s",
            len(result.sent),
            len(result.send_failed),
            result.holding,
            result.probe,
        )
    return result


@dataclass(frozen=True)
class DispatcherStatus:
    last_pass_at: datetime | None
    # None when the dispatcher has never run.
    age_seconds: int | None
    waiting: int

    @property
    def stale(self) -> bool:
        return self.age_seconds is None or self.age_seconds > DISPATCHER_STALE_MIN * 60


def status() -> DispatcherStatus:
    with function_session() as session:
        row = session.execute(
            text("SELECT last_pass_at, age_seconds, waiting FROM public.dispatcher_status()")
        ).mappings().first()
    if row is None:
        return DispatcherStatus(None, None, 0)
    age = row["age_seconds"]
    return DispatcherStatus(row["last_pass_at"], None if age is None else int(age), int(row["waiting"]))


def health() -> dict:
    """What /healthz shows (gap 1): the heartbeat's age and whether it is
    stale -- nothing else, since /healthz needs no sign-in."""
    current = status()
    return {"heartbeat_age_seconds": current.age_seconds, "stale": current.stale}


def check_stopped() -> bool:
    """
    The stuck sweep's check (item 3): documents are waiting and the
    dispatcher hasn't run for DISPATCHER_STALE_MIN. One high-severity
    `dispatcher_stopped` alert an hour, with no tenant. Returns whether it
    raised one. Never raises.
    """
    try:
        current = status()
        if current.waiting == 0 or not current.stale:
            return False
        with dispatcher_session() as session:
            raised = founder_alerts.raise_alert(
                session,
                alert_type="dispatcher_stopped",
                severity="high",
                tenant_id=None,
                payload={
                    "heartbeat_age_seconds": current.age_seconds,
                    "waiting_documents": current.waiting,
                    "stale_after_min": DISPATCHER_STALE_MIN,
                },
                dedupe_key="dispatcher_stopped",
                dedupe_per_utc_hour=True,
            )
        if raised:
            logger.error(
                "dispatcher_stopped age_seconds=%s waiting=%d", current.age_seconds, current.waiting
            )
        return raised
    except Exception:  # noqa: BLE001 -- logged; the sweep goes on
        logger.exception("dispatcher_stopped_check_failed")
        return False
