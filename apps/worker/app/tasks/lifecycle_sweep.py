"""
The lifecycle sweep (CLAUDE.md Section 7.15.4: "A scheduled job moves
cancelling tenants to suspended at their effective date"). Celery beat sends
this periodically; the work is docflow_core.lifecycle, so its tests need no
queue.

Two independent passes, each cross-tenant only for the read
(lifecycle_session -- see docflow_core.db) with every write inside that
tenant's own tenant_session, exactly like the nightly rollup and the
scheduled-jobs sweep:

  1. Tenants whose cancellation_effective_at has passed: claimed one at a
     time (lifecycle.claim_for_suspend does the entire in-database
     transition, D-123), then their Stripe subscription is cancelled
     outside the database transaction -- a failure here is alerted, not
     retried inline; the tenant's own suspension already committed.
  2. Tenants already in pending_deletion whose window has elapsed: alerted
     (deduped) so the founder sees them in the Ready to delete queue.
"""

from __future__ import annotations

import logging

from docflow_core import founder_alerts, lifecycle
from docflow_core.db import lifecycle_session, tenant_session
from docflow_core.external_services import (
    ExternalServiceError,
    cancel_subscription,
    void_pending_setup_fee,
)

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="docflow.run_lifecycle_sweep")
def run_lifecycle_sweep() -> dict:
    suspended = 0
    with lifecycle_session() as session:
        due_ids = lifecycle.due_for_suspend(session)
    for tenant_id in due_ids:
        with tenant_session(tenant_id) as session:
            claim = lifecycle.claim_for_suspend(session, tenant_id)
        if claim is None:
            continue  # already handled by an earlier tick, or reactivated in the meantime
        suspended += 1
        if claim.stripe_subscription_id or claim.stripe_customer_id:
            try:
                if claim.stripe_subscription_id:
                    cancel_subscription(claim.stripe_subscription_id)
                if claim.stripe_customer_id:
                    # A tenant cancelled mid-trial (D-125) may still have a
                    # pending, never-invoiced setup-fee item -- clear it so
                    # it can't land on some unrelated future invoice.
                    void_pending_setup_fee(
                        customer_id=claim.stripe_customer_id, tenant_id=tenant_id
                    )
            except ExternalServiceError as exc:
                logger.error(
                    "stripe_cancel_failed tenant_id=%s error=%s", tenant_id, type(exc).__name__
                )
                with tenant_session(tenant_id) as session:
                    founder_alerts.raise_alert(
                        session,
                        alert_type="stripe_cancel_failed",
                        severity="high",
                        tenant_id=tenant_id,
                        payload={"subscription_id": claim.stripe_subscription_id},
                        dedupe_key=f"stripe_cancel_failed:{tenant_id}",
                    )

    ready_alerted = 0
    with lifecycle_session() as session:
        ready_ids = lifecycle.due_for_ready_to_delete_alert(session)
    for tenant_id in ready_ids:
        with tenant_session(tenant_id) as session:
            lifecycle.raise_ready_to_delete_alert(session, tenant_id)
        ready_alerted += 1

    logger.info("lifecycle_sweep_complete suspended=%d ready_alerted=%d", suspended, ready_alerted)
    return {"suspended": suspended, "ready_alerted": ready_alerted}
