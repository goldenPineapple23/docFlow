"""
The founder dashboard's numbers (CLAUDE.md Section 7.15.3; D-121).

    "Rollup, not live scans. At the 5.1 envelope, computing these on page load
     means scanning ~50,000 documents. Instead, a nightly job (plus an
     on-demand 'recompute' button) writes `tenant_daily_metrics` ... and the
     dashboard reads from it."

Two halves, deliberately separate:

  * `compute_day` -- one tenant, one day, inside that tenant's own session.
    It reads only that tenant's rows, under the ordinary RLS policies, and
    writes one `tenant_daily_metrics` row. Re-running a day overwrites it, so
    the job is idempotent and a backfill is just a loop.
  * the read functions -- sums and shares over the stored rows. No document
    is touched.

Every number excludes `is_test_batch` documents (Section 7.15.2 Step 8), and
days are bucketed in the TENANT'S timezone, the same boundary the monthly
allowance uses (7.16.1).

Nothing here stores or returns customer data: counts, sums, and arrays of
plain numbers (hours, dollars).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core.constants import ABANDONED_REVIEW_CEILING_MIN, ROLLUP_STALE_HOURS

# Section 7.15.3: "Review time <= 2 min -- Share of documents approved where
# approved_at - review_started_at <= 120 s".
REVIEW_TARGET_SECONDS = 120


@dataclass(frozen=True)
class DayResult:
    tenant_id: UUID
    day: date
    documents_received: int
    documents_approved: int


def compute_day(session: Session, tenant_id: UUID, day: date) -> DayResult:
    """
    Recompute one tenant-day from that tenant's own rows and store it.
    `session` must be tenant-scoped for `tenant_id`.
    """
    row = session.execute(
        text(
            """
            WITH tz AS (
                SELECT coalesce(nullif(timezone, ''), 'UTC') AS name FROM tenants WHERE id = :t
            ),
            -- Documents RECEIVED on this day, in the tenant's timezone.
            received AS (
                SELECT d.id, d.status, d.est_cost_usd, d.input_tokens, d.output_tokens,
                       d.overall_confidence
                FROM documents d CROSS JOIN tz
                WHERE d.deleted_at IS NULL AND NOT d.is_test_batch
                  AND (d.created_at AT TIME ZONE tz.name)::date = :day
            ),
            -- Documents APPROVED on this day: a different set, because an
            -- order received yesterday is usually approved today.
            approved AS (
                SELECT d.id, d.created_at, d.approved_at, d.review_started_at
                FROM documents d CROSS JOIN tz
                WHERE d.deleted_at IS NULL AND NOT d.is_test_batch
                  AND d.approved_at IS NOT NULL
                  AND (d.approved_at AT TIME ZONE tz.name)::date = :day
            ),
            lines AS (
                SELECT l.id, l.match_method
                FROM document_lines l JOIN received r ON r.id = l.document_id
                WHERE l.deleted_at IS NULL
            ),
            edits AS (
                SELECT ra.id, ra.document_id
                FROM review_actions ra
                JOIN documents d ON d.id = ra.document_id
                CROSS JOIN tz
                WHERE ra.action = 'edited' AND NOT d.is_test_batch
                  AND d.deleted_at IS NULL
                  AND (ra.created_at AT TIME ZONE tz.name)::date = :day
            ),
            sessions AS (
                SELECT a.id,
                       extract(epoch FROM (a.approved_at - a.review_started_at)) AS seconds
                FROM approved a WHERE a.review_started_at IS NOT NULL
            )
            SELECT
                (SELECT count(*) FROM received) AS documents_received,
                (SELECT count(*) FROM received WHERE status = 'failed') AS documents_failed,
                (SELECT count(*) FROM approved) AS documents_approved,
                (SELECT count(*) FROM received WHERE status = 'exported') AS documents_exported,
                (SELECT count(*) FROM approved a WHERE NOT EXISTS (
                    SELECT 1 FROM review_actions ra
                    WHERE ra.document_id = a.id AND ra.action = 'edited'
                )) AS zero_edit_approvals,
                (SELECT count(*) FROM edits) AS edited_actions,
                (SELECT count(*) FROM lines) AS line_items,
                (SELECT count(*) FROM lines WHERE match_method IS NOT NULL) AS matched_lines,
                (SELECT count(*) FROM lines WHERE match_method = 'learned_rule') AS learned_rule_lines,
                (SELECT coalesce(sum(overall_confidence), 0) FROM received
                  WHERE overall_confidence IS NOT NULL) AS confidence_sum,
                (SELECT count(*) FROM received WHERE overall_confidence IS NOT NULL) AS confidence_count,
                (SELECT count(*) FROM sessions
                  WHERE seconds <= :target AND seconds <= :ceiling) AS review_within_target,
                (SELECT count(*) FROM sessions WHERE seconds <= :ceiling) AS review_sessions,
                (SELECT count(*) FROM sessions WHERE seconds > :ceiling) AS review_sessions_excluded,
                (SELECT coalesce(jsonb_agg(round(
                    (extract(epoch FROM (approved_at - created_at)) / 3600)::numeric, 4
                 )), '[]'::jsonb) FROM approved) AS approval_hours,
                (SELECT coalesce(jsonb_agg(est_cost_usd), '[]'::jsonb) FROM received
                  WHERE est_cost_usd IS NOT NULL) AS document_costs,
                (SELECT coalesce(sum(est_cost_usd), 0) FROM received) AS est_cost_usd,
                (SELECT coalesce(sum(input_tokens), 0) FROM received) AS input_tokens,
                (SELECT coalesce(sum(output_tokens), 0) FROM received) AS output_tokens
            """
        ),
        {
            "t": str(tenant_id),
            "day": day,
            "target": REVIEW_TARGET_SECONDS,
            "ceiling": ABANDONED_REVIEW_CEILING_MIN * 60,
        },
    ).mappings().one()

    session.execute(
        text(
            """
            INSERT INTO tenant_daily_metrics (
                tenant_id, day, documents_received, documents_failed, documents_approved,
                documents_exported, zero_edit_approvals, edited_actions, line_items,
                matched_lines, learned_rule_lines, confidence_sum, confidence_count,
                review_within_target, review_sessions, review_sessions_excluded,
                approval_hours, document_costs, est_cost_usd, input_tokens, output_tokens,
                computed_at
            ) VALUES (
                :tenant_id, :day, :documents_received, :documents_failed, :documents_approved,
                :documents_exported, :zero_edit_approvals, :edited_actions, :line_items,
                :matched_lines, :learned_rule_lines, :confidence_sum, :confidence_count,
                :review_within_target, :review_sessions, :review_sessions_excluded,
                :approval_hours, :document_costs, :est_cost_usd, :input_tokens, :output_tokens,
                now()
            )
            ON CONFLICT (tenant_id, day) DO UPDATE SET
                documents_received = excluded.documents_received,
                documents_failed = excluded.documents_failed,
                documents_approved = excluded.documents_approved,
                documents_exported = excluded.documents_exported,
                zero_edit_approvals = excluded.zero_edit_approvals,
                edited_actions = excluded.edited_actions,
                line_items = excluded.line_items,
                matched_lines = excluded.matched_lines,
                learned_rule_lines = excluded.learned_rule_lines,
                confidence_sum = excluded.confidence_sum,
                confidence_count = excluded.confidence_count,
                review_within_target = excluded.review_within_target,
                review_sessions = excluded.review_sessions,
                review_sessions_excluded = excluded.review_sessions_excluded,
                approval_hours = excluded.approval_hours,
                document_costs = excluded.document_costs,
                est_cost_usd = excluded.est_cost_usd,
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                computed_at = now()
            """
        ),
        {"tenant_id": str(tenant_id), "day": day, **dict(row)},
    )
    return DayResult(
        tenant_id=tenant_id,
        day=day,
        documents_received=row["documents_received"],
        documents_approved=row["documents_approved"],
    )


def tenant_days(session: Session, tenant_id: UUID, days: int) -> list[date]:
    """The last `days` days in the tenant's own timezone, most recent first --
    what a nightly run recomputes (yesterday and today, plus any catch-up)."""
    rows = session.execute(
        text(
            """
            SELECT (generate_series(
                (now() AT TIME ZONE coalesce(nullif(t.timezone, ''), 'UTC'))::date - (:days - 1),
                (now() AT TIME ZONE coalesce(nullif(t.timezone, ''), 'UTC'))::date,
                interval '1 day'
            ))::date AS day
            FROM tenants t WHERE t.id = :t
            """
        ),
        {"t": str(tenant_id), "days": days},
    ).scalars().all()
    return sorted(rows, reverse=True)


# ── Reading (dashboard) ─────────────────────────────────────────────────────


def _percentile(values: list[Decimal], fraction: Decimal) -> Decimal | None:
    """Nearest-rank percentile over stored numbers. Decimal throughout: these
    are dollars and hours, and Section 7.1's no-float rule is applied to every
    stored number, not only money."""
    if not values:
        return None
    ordered = sorted(values)
    index = int((fraction * (len(ordered) - 1)).to_integral_value(rounding="ROUND_HALF_UP"))
    return ordered[index]


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _share(part: int, whole: int) -> Decimal | None:
    return (Decimal(part) / Decimal(whole)).quantize(Decimal("0.0001")) if whole else None


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Turn stored days into the KPI card values. Pure, so the same function
    serves the whole business and one tenant, and can be tested against a
    hand-written query (Section 7.15.3's required test).
    """
    total = {
        key: sum(int(r[key] or 0) for r in rows)
        for key in (
            "documents_received",
            "documents_failed",
            "documents_approved",
            "documents_exported",
            "zero_edit_approvals",
            "edited_actions",
            "line_items",
            "matched_lines",
            "learned_rule_lines",
            "confidence_count",
            "review_within_target",
            "review_sessions",
            "review_sessions_excluded",
        )
    }
    confidence_sum = sum((Decimal(str(r["confidence_sum"] or 0)) for r in rows), Decimal(0))
    cost_sum = sum((Decimal(str(r["est_cost_usd"] or 0)) for r in rows), Decimal(0))
    hours = [Decimal(str(v)) for r in rows for v in (r["approval_hours"] or [])]
    costs = [Decimal(str(v)) for r in rows for v in (r["document_costs"] or [])]

    return {
        **total,
        "est_cost_usd": cost_sum,
        "mean_confidence": (
            (confidence_sum / total["confidence_count"]).quantize(Decimal("0.0001"))
            if total["confidence_count"]
            else None
        ),
        "review_within_target_share": _share(total["review_within_target"], total["review_sessions"]),
        "zero_edit_share": _share(total["zero_edit_approvals"], total["documents_approved"]),
        "mapping_reuse_share": _share(total["learned_rule_lines"], total["matched_lines"]),
        "corrections_per_100_lines": (
            (Decimal(total["edited_actions"]) * 100 / Decimal(total["line_items"])).quantize(
                Decimal("0.01")
            )
            if total["line_items"]
            else None
        ),
        "median_hours_to_approval": _median(hours),
        "mean_cost_per_document": (
            (sum(costs, Decimal(0)) / len(costs)).quantize(Decimal("0.0001")) if costs else None
        ),
        "p95_cost_per_document": _percentile(costs, Decimal("0.95")),
    }


def read_days(
    session: Session, *, since: date, until: date, tenant_id: UUID | None = None
) -> list[dict[str, Any]]:
    """Stored days in a window. In a tenant session RLS already limits this to
    that tenant; `tenant_id` narrows a platform-session read to one."""
    rows = session.execute(
        text(
            """
            SELECT * FROM tenant_daily_metrics
            WHERE day >= :since AND day <= :until
              AND (CAST(:tenant_id AS uuid) IS NULL OR tenant_id = CAST(:tenant_id AS uuid))
            ORDER BY day
            """
        ),
        {"since": since, "until": until, "tenant_id": str(tenant_id) if tenant_id else None},
    ).mappings().all()
    return [dict(r) for r in rows]


def last_run(session: Session) -> dict[str, Any] | None:
    row = session.execute(
        text(
            "SELECT id, started_at, finished_at, trigger, tenants, rows_written, ok, error "
            "FROM rollup_runs ORDER BY started_at DESC LIMIT 1"
        )
    ).mappings().first()
    return dict(row) if row else None


def is_stale(run: dict[str, Any] | None, *, hours: int) -> bool:
    """Section 7.15.3: "a rollup that hasn't run in 36 hours is itself a
    founder_alerts row"."""
    if run is None or run.get("finished_at") is None or not run.get("ok"):
        return True
    return datetime.now(timezone.utc) - run["finished_at"] > timedelta(hours=hours)


# ── Running the rollup ──────────────────────────────────────────────────────


def run_rollup(*, days: int = 2, trigger: str = "nightly", tenant_id: UUID | None = None) -> dict[str, Any]:
    """
    Recompute the last `days` days for every tenant (or one), and record the
    run. Defaults to two days, not one: a nightly run must re-close yesterday
    in every timezone and top up today.

    Cross-tenant, but not the Section 7.15.1 bypass: `rollup_session` can read
    the tenants table and write `rollup_runs`, and every number is computed
    inside that tenant's own session under the ordinary RLS policies.

    Idempotent: re-running a day overwrites it.
    """
    from docflow_core.db import rollup_session, tenant_session

    with rollup_session() as session:
        previous = last_run(session)
        run_id = session.execute(
            text(
                "INSERT INTO rollup_runs (trigger) VALUES (:trigger) RETURNING id"
            ),
            {"trigger": trigger},
        ).scalar_one()
        tenants = [
            UUID(str(row[0]))
            for row in session.execute(
                text(
                    "SELECT id FROM tenants WHERE deleted_at IS NULL "
                    "AND (CAST(:tenant_id AS uuid) IS NULL OR id = CAST(:tenant_id AS uuid)) "
                    "ORDER BY created_at"
                ),
                {"tenant_id": str(tenant_id) if tenant_id else None},
            )
        ]

    written, first_day, last_day, error = 0, None, None, None
    try:
        for tenant in tenants:
            with tenant_session(tenant) as session:
                for day in tenant_days(session, tenant, days):
                    compute_day(session, tenant, day)
                    written += 1
                    first_day = day if first_day is None or day < first_day else first_day
                    last_day = day if last_day is None or day > last_day else last_day
    except Exception as exc:  # noqa: BLE001 -- recorded on the run, then re-raised
        error = type(exc).__name__
        raise
    finally:
        with rollup_session() as session:
            session.execute(
                text(
                    "UPDATE rollup_runs SET finished_at = now(), tenants = :tenants, "
                    "rows_written = :rows, ok = :ok, day_from = :day_from, day_to = :day_to, "
                    "error = :error WHERE id = :id"
                ),
                {
                    "id": str(run_id),
                    "tenants": len(tenants),
                    "rows": written,
                    "ok": error is None,
                    "day_from": first_day,
                    "day_to": last_day,
                    "error": error,
                },
            )
            # The run that just finished proves the job is alive; the one
            # before it is what tells us it had stopped (7.15.3).
            if trigger == "nightly" and is_stale(previous, hours=ROLLUP_STALE_HOURS):
                _raise_stale_alert(session, previous)

    return {
        "run_id": run_id,
        "tenants": len(tenants),
        "rows_written": written,
        "day_from": first_day,
        "day_to": last_day,
    }


def _raise_stale_alert(session: Session, previous: dict[str, Any] | None) -> None:
    from docflow_core.founder_alerts import raise_alert

    raise_alert(
        session,
        alert_type="rollup_stale",
        severity="warning",
        tenant_id=None,
        payload={
            "last_finished_at": (
                previous["finished_at"].isoformat()
                if previous and previous.get("finished_at")
                else None
            ),
            "stale_after_hours": ROLLUP_STALE_HOURS,
        },
        dedupe_key="rollup_stale",
    )
