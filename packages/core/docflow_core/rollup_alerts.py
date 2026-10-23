"""
The three attention-panel alerts the nightly rollup raises (CLAUDE.md
Section 7.15.3 and 7.15.2 Step 1; Phase 5.5 Stage 5, M7; DECISIONS.md D-214):

  * review_backlog -- "a tenant with `needs_review` documents older than a
    configurable number of days" (REVIEW_BACKLOG_ALERT_DAYS);
  * confidence_drift -- "a tenant whose 7-day mean overall confidence is more
    than a configurable margin below its 30-day mean" (CONFIDENCE_DRIFT_MARGIN);
  * staging_ttl_elapsed -- "staging files not linked to a tenant within
    STAGING_TTL_DAYS appear in the attention panel ... never auto-deleted".

Each check takes the session it runs in; `metrics.run_rollup` opens them
(nightly runs only, never the Console's Recompute) and runs each check in its
own transaction, so one that fails is logged and the rest carry on. Nothing
here changes or deletes a row other than the alert and its e-mail.

The founder's decisions (2026-10-21): test-batch documents never count
toward the backlog, and only `active` and `cancelling` tenants are checked
for it. Drift compares the last 7 complete days with the 30 complete days
before them (days 8 to 37, no overlap), in the tenant's own timezone, the
rollup's day boundary; below CONFIDENCE_DRIFT_MIN_DOCUMENTS documents in
either window nothing is raised. None of the three has a catalog code: none
is a failure, and none reaches a tenant.

Each is an ordinary dedup key: at most one open alert per key, and once the
founder acknowledges it the next nightly run raises it again if it still
holds. Payloads carry ids, counts and numbers only (Section 7.10): the
staging alert never names the prospect, because the payload is e-mailed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import founder_alerts
from docflow_core.constants import (
    CONFIDENCE_DRIFT_MARGIN,
    CONFIDENCE_DRIFT_MIN_DOCUMENTS,
    REVIEW_BACKLOG_ALERT_DAYS,
    STAGING_TTL_DAYS,
)

# Section 7.15.3's "7-day mean" and "30-day mean". The 30 days are the ones
# before the 7, not a window that contains them (founder, 2026-10-21).
RECENT_DAYS = 7
BASELINE_DAYS = 30
# Tenants the backlog check covers (founder, 2026-10-21): a suspended tenant's
# review queue is frozen with its account, not a backlog.
BACKLOG_STATUSES = ("active", "cancelling")


def review_backlog(session: Session, tenant_id: UUID) -> bool:
    """The tenant's oldest wait in review, if any document has waited longer
    than REVIEW_BACKLOG_ALERT_DAYS. Waiting is counted from `processed_at`
    (when it reached review), else `created_at`. `session` is the tenant's
    own. True when an alert was written."""
    status = session.execute(
        text("SELECT status FROM tenants WHERE id = :t"), {"t": str(tenant_id)}
    ).scalar_one_or_none()
    if status not in BACKLOG_STATUSES:
        return False
    row = session.execute(
        text(
            """
            SELECT count(*) AS waiting,
                   (array_agg(id ORDER BY coalesce(processed_at, created_at), id))[1] AS oldest_id,
                   floor(extract(epoch FROM now() - min(coalesce(processed_at, created_at))) / 86400)
                       AS oldest_days
            FROM documents
            WHERE tenant_id = :t AND status = 'needs_review' AND deleted_at IS NULL
              AND NOT is_test_batch
              AND coalesce(processed_at, created_at) < now() - make_interval(days => :days)
            """
        ),
        {"t": str(tenant_id), "days": REVIEW_BACKLOG_ALERT_DAYS},
    ).mappings().one()
    if not row["waiting"]:
        return False
    return founder_alerts.raise_alert(
        session,
        alert_type="review_backlog",
        severity="warning",
        tenant_id=tenant_id,
        payload={
            "documents_waiting": int(row["waiting"]),
            "oldest_waiting_days": int(row["oldest_days"]),
            "oldest_document_id": str(row["oldest_id"]),
            "threshold_days": REVIEW_BACKLOG_ALERT_DAYS,
        },
        dedupe_key=f"review_backlog:{tenant_id}",
    )


def drift_windows(session: Session, tenant_id: UUID) -> dict[str, Any]:
    """The two windows' confidence sums and counts, from the stored rollup
    days (test batches already excluded there). Days are the tenant's own:
    the recent window is the RECENT_DAYS days before today, the baseline the
    BASELINE_DAYS days before that."""
    return dict(
        session.execute(
            text(
                """
                WITH today AS (
                    SELECT (now() AT TIME ZONE coalesce(nullif(timezone, ''), 'UTC'))::date AS d
                    FROM tenants WHERE id = :t
                )
                SELECT
                    coalesce(sum(m.confidence_sum) FILTER (
                        WHERE m.day BETWEEN today.d - :recent AND today.d - 1), 0) AS recent_sum,
                    coalesce(sum(m.confidence_count) FILTER (
                        WHERE m.day BETWEEN today.d - :recent AND today.d - 1), 0) AS recent_count,
                    coalesce(sum(m.confidence_sum) FILTER (
                        WHERE m.day BETWEEN today.d - :span AND today.d - :recent - 1), 0) AS baseline_sum,
                    coalesce(sum(m.confidence_count) FILTER (
                        WHERE m.day BETWEEN today.d - :span AND today.d - :recent - 1), 0) AS baseline_count
                FROM today
                LEFT JOIN tenant_daily_metrics m
                       ON m.tenant_id = :t AND m.day BETWEEN today.d - :span AND today.d - 1
                """
            ),
            {"t": str(tenant_id), "recent": RECENT_DAYS, "span": RECENT_DAYS + BASELINE_DAYS},
        ).mappings().one()
    )


def drifted(windows: dict[str, Any]) -> dict[str, Any] | None:
    """The alert's payload when the recent mean is MORE than the margin below
    the baseline mean and both windows hold enough documents; else None.
    Decimal throughout: a drop of exactly the margin is not a drift."""
    recent_count, baseline_count = int(windows["recent_count"]), int(windows["baseline_count"])
    if recent_count < CONFIDENCE_DRIFT_MIN_DOCUMENTS or baseline_count < CONFIDENCE_DRIFT_MIN_DOCUMENTS:
        return None
    recent = Decimal(str(windows["recent_sum"])) / recent_count
    baseline = Decimal(str(windows["baseline_sum"])) / baseline_count
    margin = Decimal(str(CONFIDENCE_DRIFT_MARGIN))
    if baseline - recent <= margin:
        return None
    return {
        "mean_7d": str(recent.quantize(Decimal("0.0001"))),
        "mean_30d": str(baseline.quantize(Decimal("0.0001"))),
        "margin": str(margin),
        "documents_7d": recent_count,
        "documents_30d": baseline_count,
        "min_documents": CONFIDENCE_DRIFT_MIN_DOCUMENTS,
    }


def confidence_drift(session: Session, tenant_id: UUID) -> bool:
    """`session` is the tenant's own. True when an alert was written."""
    payload = drifted(drift_windows(session, tenant_id))
    if payload is None:
        return False
    return founder_alerts.raise_alert(
        session,
        alert_type="confidence_drift",
        severity="warning",
        tenant_id=tenant_id,
        payload=payload,
        dedupe_key=f"confidence_drift:{tenant_id}",
    )


def staged_past_ttl(session: Session) -> list[dict[str, Any]]:
    """Every staged intake still unlinked STAGING_TTL_DAYS after it was
    created, read through migration 0039's function: ids, times and file
    counts only. `session` is the rollup's own, opened by `metrics`."""
    rows = session.execute(
        text(
            """
            SELECT intake_id, files,
                   (created_at AT TIME ZONE 'UTC')::date::text AS staged_on,
                   floor(extract(epoch FROM now() - created_at) / 86400) AS days_unlinked
            FROM staging_intakes_past_ttl(:ttl)
            """
        ),
        {"ttl": STAGING_TTL_DAYS},
    ).mappings().all()
    return [dict(row) for row in rows]


def raise_staging_alert(session: Session, intake: dict[str, Any]) -> bool:
    """One intake's alert, tenant-less, in the rollup's session: 0039 lets its
    rollup_raise policy insert this type. True when an alert was written."""
    return founder_alerts.raise_alert(
        session,
        alert_type="staging_ttl_elapsed",
        severity="warning",
        tenant_id=None,
        payload={
            "intake_id": str(intake["intake_id"]),
            "staged_on": intake["staged_on"],
            "days_unlinked": int(intake["days_unlinked"]),
            "files": int(intake["files"]),
            "ttl_days": STAGING_TTL_DAYS,
        },
        dedupe_key=f"staging_ttl:{intake['intake_id']}",
    )


def staging_ttl(session: Session) -> int:
    """One alert per staged intake past its TTL. Returns how many alerts were
    written; deletes nothing."""
    return sum(raise_staging_alert(session, intake) for intake in staged_past_ttl(session))
