# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
The founder dashboard (Section 7.15.3; D-121), against the real database and
RLS (migration 0017).

The required test from the section, first: "for a seeded staging dataset,
every KPI card must equal an independent hand-written SQL query within
rounding". `test_every_kpi_equals_an_independent_query` does exactly that --
it never calls the rollup's own code to check the rollup.

Also proved here:
  * the rollup is idempotent (running a day twice leaves the same row);
  * test-batch documents are in no number (Section 7.15.2 Step 8);
  * one tenant's documents never reach another tenant's row;
  * the dashboard reads the rollup, not documents, and reports staleness;
  * recompute queues the same job the nightly beat runs;
  * the tenant list carries the section's columns;
  * the routes are 404 to anyone but a platform admin.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from docflow_core import metrics
from docflow_core.db import platform_session, tenant_session
from sqlalchemy import text

from tests.conftest import requires_console_schema, requires_review_schema
from tests.test_console_api import _Console, _environment  # noqa: F401 -- fixtures
from tests.test_review_api import CLEAN_LINES, _ReviewTenant, _secrets  # noqa: F401 -- fixture


def _metrics_schema_available() -> bool:
    try:
        with platform_session() as session:
            session.execute(text("SELECT tenant_id FROM tenant_daily_metrics LIMIT 0"))
        return True
    except Exception:
        return False


requires_metrics_schema = pytest.mark.skipif(
    not _metrics_schema_available(),
    reason="supabase/migrations/0017_daily_metrics.sql has not been applied yet -- see D-121.",
)


@pytest.fixture
def queue(monkeypatch):
    sent: list[tuple[str, list]] = []

    class _FakeCelery:
        def send_task(self, name, args=None, queue=None):
            sent.append((name, args))

    monkeypatch.setattr("app.routers.admin.celery_client", _FakeCelery())
    return sent


def _today(tenant_id: UUID) -> date:
    with platform_session() as session:
        return session.execute(
            text(
                "SELECT (now() AT TIME ZONE coalesce(nullif(timezone, ''), 'UTC'))::date "
                "FROM tenants WHERE id = :t"
            ),
            {"t": str(tenant_id)},
        ).scalar_one()


def _seed_order(
    tenant: _ReviewTenant,
    *,
    approved_minutes: int | None = None,
    review_seconds: int | None = None,
    edited: bool = False,
    cost: str = "0.05",
    confidence: str = "0.90",
    is_test_batch: bool = False,
    match_method: str | None = "learned_rule",
    created_hour: int = 1,
) -> UUID:
    """
    One finished order, positioned in time, written straight to the database
    -- the rollup has to read what the pipeline leaves behind.

    Times are pinned to a fixed hour of TODAY rather than "n hours ago", so a
    run near midnight can't push an approval into tomorrow's row and make the
    test disagree with itself.
    """
    document = tenant.create_document(
        header={"po_number": f"TEST-{uuid4().hex[:6]}"}, lines=CLEAN_LINES
    )
    with platform_session() as session:
        session.execute(
            text(
                """
                UPDATE documents SET
                    created_at = date_trunc('day', now()) + make_interval(hours => CAST(:hour AS integer)),
                    est_cost_usd = CAST(:cost AS numeric),
                    overall_confidence = CAST(:confidence AS numeric),
                    is_test_batch = CAST(:test_batch AS boolean),
                    input_tokens = 1000, output_tokens = 200,
                    status = CASE WHEN CAST(:approved_minutes AS integer) IS NULL
                                  THEN 'needs_review' ELSE 'approved' END,
                    approved_at = CASE WHEN CAST(:approved_minutes AS integer) IS NULL THEN NULL ELSE
                        date_trunc('day', now()) + make_interval(hours => CAST(:hour AS integer))
                        + make_interval(mins => CAST(:approved_minutes AS integer)) END,
                    review_started_at = CASE WHEN CAST(:review_seconds AS integer) IS NULL THEN NULL ELSE
                        date_trunc('day', now()) + make_interval(hours => CAST(:hour AS integer))
                        + make_interval(mins => CAST(:approved_minutes AS integer))
                        - make_interval(secs => CAST(:review_seconds AS integer)) END,
                    approved_by = CASE WHEN CAST(:approved_minutes AS integer) IS NULL
                                       THEN NULL ELSE CAST(:user AS uuid) END,
                    approved_json = CASE WHEN CAST(:approved_minutes AS integer) IS NULL
                                         THEN NULL ELSE '{}'::jsonb END,
                    approved_snapshot_hash = CASE WHEN CAST(:approved_minutes AS integer) IS NULL
                                                  THEN NULL ELSE 'test' END
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {
                "id": str(document),
                "hour": created_hour,
                "approved_minutes": approved_minutes,
                "review_seconds": review_seconds,
                "cost": cost,
                "confidence": confidence,
                "test_batch": is_test_batch,
                "user": str(tenant.user_id),
            },
        )
        if match_method is not None:
            # A match method needs the item it matched (the schema says so),
            # so the tenant gets one catalog item to point at.
            item = session.execute(
                text(
                    "INSERT INTO items (id, tenant_id, sku, description) "
                    "VALUES (gen_random_uuid(), :t, :sku, 'Test item') RETURNING id"
                ),
                {"t": str(tenant.tenant_id), "sku": f"TEST-{uuid4().hex[:6]}"},
            ).scalar_one()
            session.execute(
                text(
                    "UPDATE document_lines SET match_method = :m, matched_item_id = :item, "
                    "match_score = 1 WHERE document_id = :d"
                ),
                {"m": match_method, "item": str(item), "d": str(document)},
            )
        if edited:
            session.execute(
                text(
                    """
                    INSERT INTO review_actions
                        (id, tenant_id, document_id, user_id, action, changes, created_at)
                    VALUES (:id, :t, :d, :u, 'edited',
                            '[{"field": "po_number", "before": "A", "after": "B"}]'::jsonb,
                            date_trunc('day', now()) + make_interval(hours => CAST(:hour AS integer)))
                    """
                ),
                {
                    "id": str(uuid4()),
                    "t": str(tenant.tenant_id),
                    "d": str(document),
                    "u": str(tenant.user_id),
                    "hour": created_hour,
                },
            )
    return document


def _roll(tenant_id: UUID, days: int = 2) -> None:
    with tenant_session(tenant_id) as session:
        for day in metrics.tenant_days(session, tenant_id, days):
            metrics.compute_day(session, tenant_id, day)


def _row(tenant_id: UUID, day: date) -> dict:
    with platform_session() as session:
        return dict(
            session.execute(
                text("SELECT * FROM tenant_daily_metrics WHERE tenant_id = :t AND day = :d"),
                {"t": str(tenant_id), "d": day},
            )
            .mappings()
            .one()
        )


def _cleanup(tenant_id: UUID) -> None:
    with platform_session() as session:
        # Lines point at the seeded items; unlink before the tenant fixture
        # deletes the documents.
        session.execute(
            text(
                "UPDATE document_lines SET matched_item_id = NULL, match_method = NULL WHERE tenant_id = :t"
            ),
            {"t": str(tenant_id)},
        )
        session.execute(text("DELETE FROM items WHERE tenant_id = :t"), {"t": str(tenant_id)})
        session.execute(text("DELETE FROM tenant_daily_metrics WHERE tenant_id = :t"), {"t": str(tenant_id)})
        session.execute(text("DELETE FROM admin_actions WHERE target_tenant_id = :t"), {"t": str(tenant_id)})


# ── The section's required test ─────────────────────────────────────────────


@requires_metrics_schema
@requires_review_schema
def test_every_kpi_equals_an_independent_query():
    """Section 7.15.3: "for a seeded staging dataset, every KPI card must
    equal an independent hand-written SQL query within rounding"."""
    with _ReviewTenant("Acme Test Distributor -- kpi") as tenant:
        try:
            # Three approved orders: two reviewed quickly, one slowly; one edited.
            _seed_order(tenant, approved_minutes=30, review_seconds=45, cost="0.04")
            _seed_order(tenant, approved_minutes=60, review_seconds=90, cost="0.06")
            _seed_order(tenant, approved_minutes=120, review_seconds=600, cost="0.20", edited=True)
            # One still waiting, and one abandoned review (over the ceiling).
            _seed_order(tenant, cost="0.10", match_method=None)
            _seed_order(tenant, approved_minutes=200, review_seconds=3600, cost="0.08")
            _roll(tenant.tenant_id)

            day = _today(tenant.tenant_id)
            stored = [_row(tenant.tenant_id, day)]
            kpis = metrics.summarise(stored)

            # The independent check: plain SQL over the documents themselves,
            # written without looking at the rollup's query.
            with platform_session() as session:
                truth = (
                    session.execute(
                        text(
                            """
                        SELECT
                            count(*) FILTER (WHERE approved_at IS NOT NULL) AS approved,
                            count(*) FILTER (
                                WHERE approved_at IS NOT NULL AND NOT EXISTS (
                                    SELECT 1 FROM review_actions ra
                                    WHERE ra.document_id = d.id AND ra.action = 'edited')
                            ) AS zero_edit,
                            count(*) FILTER (
                                WHERE approved_at IS NOT NULL
                                  AND extract(epoch FROM (approved_at - review_started_at)) <= 120
                            ) AS quick,
                            count(*) FILTER (
                                WHERE approved_at IS NOT NULL
                                  AND extract(epoch FROM (approved_at - review_started_at)) <= 1800
                            ) AS measured,
                            count(*) FILTER (
                                WHERE approved_at IS NOT NULL
                                  AND extract(epoch FROM (approved_at - review_started_at)) > 1800
                            ) AS abandoned,
                            sum(est_cost_usd) AS cost,
                            count(*) AS received,
                            percentile_cont(0.5) WITHIN GROUP (
                                ORDER BY extract(epoch FROM (approved_at - created_at)) / 3600
                            ) FILTER (WHERE approved_at IS NOT NULL) AS median_hours,
                            avg(est_cost_usd) AS mean_cost
                        FROM documents d
                        WHERE tenant_id = :t AND NOT is_test_batch AND deleted_at IS NULL
                        """
                        ),
                        {"t": str(tenant.tenant_id)},
                    )
                    .mappings()
                    .one()
                )
                lines = (
                    session.execute(
                        text(
                            """
                        SELECT count(*) AS total,
                               count(*) FILTER (WHERE match_method IS NOT NULL) AS matched,
                               count(*) FILTER (WHERE match_method = 'learned_rule') AS learned
                        FROM document_lines l JOIN documents d ON d.id = l.document_id
                        WHERE l.tenant_id = :t AND NOT d.is_test_batch AND l.deleted_at IS NULL
                        """
                        ),
                        {"t": str(tenant.tenant_id)},
                    )
                    .mappings()
                    .one()
                )
                edits = session.execute(
                    text("SELECT count(*) FROM review_actions WHERE tenant_id = :t AND action = 'edited'"),
                    {"t": str(tenant.tenant_id)},
                ).scalar_one()

            assert kpis["documents_received"] == truth["received"]
            assert kpis["documents_approved"] == truth["approved"]
            assert kpis["zero_edit_approvals"] == truth["zero_edit"]
            assert kpis["review_within_target"] == truth["quick"]
            assert kpis["review_sessions"] == truth["measured"]
            assert kpis["review_sessions_excluded"] == truth["abandoned"]
            assert kpis["line_items"] == lines["total"]
            assert kpis["matched_lines"] == lines["matched"]
            assert kpis["learned_rule_lines"] == lines["learned"]
            assert kpis["edited_actions"] == edits
            assert kpis["est_cost_usd"] == Decimal(truth["cost"])
            # Shares, from the independent counts.
            assert kpis["zero_edit_share"] == (
                Decimal(truth["zero_edit"]) / Decimal(truth["approved"])
            ).quantize(Decimal("0.0001"))
            assert kpis["mapping_reuse_share"] == (
                Decimal(lines["learned"]) / Decimal(lines["matched"])
            ).quantize(Decimal("0.0001"))
            assert kpis["corrections_per_100_lines"] == (
                Decimal(edits) * 100 / Decimal(lines["total"])
            ).quantize(Decimal("0.01"))
            # Within rounding: the rollup stores hours to four places.
            assert abs(kpis["median_hours_to_approval"] - Decimal(str(truth["median_hours"]))) < Decimal(
                "0.001"
            )
            assert abs(kpis["mean_cost_per_document"] - Decimal(str(truth["mean_cost"]))) < Decimal("0.0001")
        finally:
            _cleanup(tenant.tenant_id)


# ── The rollup itself ───────────────────────────────────────────────────────


@requires_metrics_schema
@requires_review_schema
def test_running_a_day_twice_leaves_the_same_row():
    with _ReviewTenant("Acme Test Distributor -- idempotent") as tenant:
        try:
            _seed_order(tenant, approved_minutes=20, review_seconds=60)
            _roll(tenant.tenant_id)
            day = _today(tenant.tenant_id)
            first = _row(tenant.tenant_id, day)
            _roll(tenant.tenant_id)
            second = _row(tenant.tenant_id, day)
            assert {k: v for k, v in first.items() if k != "computed_at"} == {
                k: v for k, v in second.items() if k != "computed_at"
            }
            assert second["computed_at"] >= first["computed_at"]
        finally:
            _cleanup(tenant.tenant_id)


@requires_metrics_schema
@requires_review_schema
def test_test_batch_documents_are_in_no_number():
    with _ReviewTenant("Acme Test Distributor -- test batch") as tenant:
        try:
            _seed_order(tenant, approved_minutes=10, review_seconds=30, cost="0.05")
            _seed_order(tenant, approved_minutes=10, review_seconds=30, cost="9.99", is_test_batch=True)
            _roll(tenant.tenant_id)
            row = _row(tenant.tenant_id, _today(tenant.tenant_id))
            assert row["documents_received"] == 1
            assert row["documents_approved"] == 1
            assert Decimal(row["est_cost_usd"]) == Decimal("0.0500")
        finally:
            _cleanup(tenant.tenant_id)


@requires_metrics_schema
@requires_review_schema
def test_one_tenants_orders_never_reach_another_tenants_row():
    with (
        _ReviewTenant("Acme Test Distributor -- roll A") as a,
        _ReviewTenant("Acme Test Distributor -- roll B") as b,
    ):
        try:
            _seed_order(a, approved_minutes=10, review_seconds=30, cost="0.05")
            _seed_order(b, approved_minutes=10, review_seconds=30, cost="0.07")
            _seed_order(b, approved_minutes=10, review_seconds=30, cost="0.07")
            _roll(a.tenant_id)
            _roll(b.tenant_id)
            assert _row(a.tenant_id, _today(a.tenant_id))["documents_received"] == 1
            assert _row(b.tenant_id, _today(b.tenant_id))["documents_received"] == 2
        finally:
            _cleanup(a.tenant_id)
            _cleanup(b.tenant_id)


# ── The dashboard ───────────────────────────────────────────────────────────


@requires_metrics_schema
@requires_console_schema
@requires_review_schema
def test_the_dashboard_reads_the_rollup_and_reports_its_freshness(client, queue):
    with _Console() as console, _ReviewTenant("Acme Test Distributor -- dash") as tenant:
        try:
            _seed_order(tenant, approved_minutes=15, review_seconds=45, cost="0.05")
            _roll(tenant.tenant_id)

            body = client.get("/admin/dashboard", headers=console.headers()).json()
            assert body["kpis"]["documents_received"] >= 1
            assert body["health"]["needs_review"] >= 0
            assert body["rollup_stale_hours"] == 36
            # `rollup_runs` is global and other tests/manual runs may have
            # left a real row in it, so this checks the wiring -- the
            # endpoint's answer must agree with the same staleness function
            # applied to the actual last run -- rather than assuming no run
            # has ever happened on this database.
            with platform_session() as session:
                expected_stale = metrics.is_stale(metrics.last_run(session), hours=36)
            assert body["rollup_is_stale"] is expected_stale
            assert set(body["queues"]) == {"interactive", "bulk"}

            queued = client.post("/admin/rollup/recompute", headers=console.headers(), json={"days": 3})
            assert queued.status_code == 202
            assert queue[-1] == ("docflow.run_daily_rollup", [3, "manual"])
            with platform_session() as session:
                logged = session.execute(
                    text(
                        "SELECT count(*) FROM admin_actions WHERE platform_admin_user_id = :u "
                        "AND action = 'rollup_recompute'"
                    ),
                    {"u": str(console.user_id)},
                ).scalar_one()
            assert logged == 1
        finally:
            _cleanup(tenant.tenant_id)


@requires_metrics_schema
@requires_console_schema
@requires_review_schema
def test_the_tenant_list_carries_the_dashboard_columns(client):
    with _Console() as console, _ReviewTenant("Acme Test Distributor -- columns") as tenant:
        try:
            _seed_order(tenant, cost="0.11")  # still needs review
            _seed_order(tenant, approved_minutes=10, review_seconds=30, cost="0.09")
            _roll(tenant.tenant_id)

            rows = client.get("/admin/tenants", headers=console.headers()).json()
            row = next(r for r in rows if r["id"] == str(tenant.tenant_id))
            assert row["documents_this_month"] == 2
            assert row["needs_review"] == 1
            assert Decimal(row["ai_cost_this_month"]) == Decimal("0.20")
            assert row["last_document_at"] is not None
            assert row["mean_confidence_30"] is not None
        finally:
            _cleanup(tenant.tenant_id)


@requires_metrics_schema
@requires_console_schema
@requires_review_schema
def test_a_tenants_own_metrics_are_readable_from_the_console(client):
    with _Console() as console, _ReviewTenant("Acme Test Distributor -- per tenant") as tenant:
        try:
            _seed_order(tenant, approved_minutes=10, review_seconds=30, cost="0.05")
            _roll(tenant.tenant_id)
            body = client.get(f"/admin/tenants/{tenant.tenant_id}/metrics", headers=console.headers()).json()
            assert body["days"] == 30
            assert body["kpis"]["documents_approved"] == 1
        finally:
            _cleanup(tenant.tenant_id)


def test_the_dashboard_routes_are_404_to_everyone_else(client):
    assert client.get("/admin/dashboard").status_code == 404
    assert client.post("/admin/rollup/recompute", json={"days": 2}).status_code == 404
    assert client.get(f"/admin/tenants/{UUID(int=1)}/metrics").status_code == 404
