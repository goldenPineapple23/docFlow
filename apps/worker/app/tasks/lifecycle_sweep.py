"""
The lifecycle sweep (CLAUDE.md Section 7.15.4: "A scheduled job moves
cancelling tenants to suspended at their effective date"). Celery beat sends
this periodically; the work is docflow_core.lifecycle, so its tests need no
queue.

Independent passes, each cross-tenant only for the read (lifecycle_session
-- see docflow_core.db) with every write inside that tenant's own
tenant_session, exactly like the nightly rollup and the scheduled-jobs sweep:

  0. Stripe cancels still owed (Stage 3a): a tenant whose suspension
     committed but whose Stripe billing was never confirmed cancelled -- the
     cancel failed, or the worker was killed between the two -- is retried
     (lifecycle.process_pending_cancel re-checks under a row lock that the
     tenant is still suspended first).
  1. Tenants whose cancellation_effective_at has passed: claimed one at a
     time (lifecycle.claim_for_suspend does the entire in-database
     transition, D-123, and marks the Stripe cancel as owed in the same
     transaction), then Stripe is called through the same
     process_pending_cancel.
  2. Tenants already in pending_deletion whose window has elapsed: alerted
     (deduped) so the founder sees them in the Ready to delete queue.
  3. Intake housekeeping (Section 7.16.3, D-126): rotated addresses whose
     grace period ended are retired, and held documents past their retention
     period raise one deduplicated alert. Nothing is ever deleted.

A time box (LIFECYCLE_SWEEP_TIME_BOX_SECONDS, Stage 3a): once it closes, no
pass takes another tenant, and the rest wait for the next tick. So the
task's hard time limit can't land in the middle of a tenant in normal
running: after the box closes, the most still in flight is one tenant's
Stripe calls.
"""

from __future__ import annotations

import logging
import time

from docflow_core import intake_admin, lifecycle
from docflow_core.constants import (
    LIFECYCLE_SWEEP_TASK_TIME_LIMIT_SECONDS,
    LIFECYCLE_SWEEP_TIME_BOX_SECONDS,
)
from docflow_core.db import lifecycle_session, tenant_session

from app.celery_app import celery_app

logger = logging.getLogger(__name__)

# The sweep's own duration, measured on this process's monotonic clock. It is
# never compared with a database time, so it is not a clock D-170 governs.
# A module attribute so a test can drive it.
_monotonic = time.monotonic


@celery_app.task(name="docflow.run_lifecycle_sweep", time_limit=LIFECYCLE_SWEEP_TASK_TIME_LIMIT_SECONDS)
def run_lifecycle_sweep() -> dict:
    started = _monotonic()

    def box_closed() -> bool:
        return _monotonic() - started >= LIFECYCLE_SWEEP_TIME_BOX_SECONDS

    counts = {"cancels_retried": 0, "suspended": 0, "ready_alerted": 0, "addresses_retired": 0}
    stopped_early = False

    with lifecycle_session() as session:
        owed_ids = lifecycle.pending_cancels(session)
    for tenant_id in owed_ids:
        if box_closed():
            stopped_early = True
            break
        if lifecycle.process_pending_cancel(tenant_id) != "none":
            counts["cancels_retried"] += 1

    if not stopped_early:
        with lifecycle_session() as session:
            due_ids = lifecycle.due_for_suspend(session)
        for tenant_id in due_ids:
            if box_closed():
                stopped_early = True
                break
            with tenant_session(tenant_id) as session:
                claim = lifecycle.claim_for_suspend(session, tenant_id)
            if claim is None:
                continue  # already handled by an earlier tick, or reactivated in the meantime
            counts["suspended"] += 1
            # A failure here is alerted and the cancel stays owed: pass 0 of
            # the next tick retries it. The suspension already committed.
            lifecycle.process_pending_cancel(tenant_id)

    if not stopped_early:
        with lifecycle_session() as session:
            ready_ids = lifecycle.due_for_ready_to_delete_alert(session)
        for tenant_id in ready_ids:
            if box_closed():
                stopped_early = True
                break
            with tenant_session(tenant_id) as session:
                lifecycle.raise_ready_to_delete_alert(session, tenant_id)
            counts["ready_alerted"] += 1

    if not stopped_early:
        with lifecycle_session() as session:
            housekeeping_ids = lifecycle.tenants_for_intake_housekeeping(session)
        for tenant_id in housekeeping_ids:
            if box_closed():
                stopped_early = True
                break
            with tenant_session(tenant_id) as session:
                counts["addresses_retired"] += intake_admin.housekeeping(session, tenant_id)[
                    "retired_addresses"
                ]

    logger.info(
        "lifecycle_sweep_complete cancels_retried=%d suspended=%d ready_alerted=%d "
        "addresses_retired=%d stopped_at_time_box=%s",
        counts["cancels_retried"],
        counts["suspended"],
        counts["ready_alerted"],
        counts["addresses_retired"],
        stopped_early,
    )
    return {**counts, "stopped_at_time_box": stopped_early}
