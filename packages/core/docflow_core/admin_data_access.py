"""
adminDataAccess (CLAUDE.md Section 7.15.1) -- the ONE place cross-tenant
queries are allowed to happen.

Import restriction (enforced by apps/api/tests/test_admin_import_boundary.py,
a dependency-graph test that fails CI if violated): this module may only be
imported from:
  - apps/api/app/routers/admin.py (and other /admin/* route handlers, as they're added)
  - apps/worker's nightly rollup task

Every function here writes exactly one `admin_actions` row per call, before
returning data, in the same transaction as any write it performs. There is
no free-form query surface -- each function is a named, specific action.

This module never keeps a tenant_id in the caller's control unchecked: the
platform-admin identity is required as an explicit parameter on every call,
and the caller (a route handler) is responsible for having already verified,
via `require_platform_admin`, that the identity is real.

Outbound calls (Stripe, Supabase Auth) are imported inside the functions
that make them, so importing this module never pulls in an HTTP client --
the worker's rollup task imports it too, and the worker has no need of one.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from docflow_core import deal_terms
from docflow_core.db import platform_session

logger = logging.getLogger(__name__)


class ConsoleError(Exception):
    """A Console request that cannot be carried out. Carries a founder-audience
    catalog code (CON-0xx): Section 7.16.5 puts every user-facing failure in
    the one catalog, founder-facing ones included."""

    def __init__(self, code: str, detail: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


def _record_admin_action(
    session,
    *,
    platform_admin_user_id: UUID,
    action: str,
    target_tenant_id: UUID | None = None,
    target_type: str | None = None,
    target_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO admin_actions
                (id, platform_admin_user_id, action, target_tenant_id, target_type, target_id, payload, created_at)
            VALUES
                (:id, :platform_admin_user_id, :action, :target_tenant_id, :target_type, :target_id, :payload, :created_at)
            """
        ),
        {
            "id": str(uuid4()),
            "platform_admin_user_id": str(platform_admin_user_id),
            "action": action,
            "target_tenant_id": str(target_tenant_id) if target_tenant_id else None,
            "target_type": target_type,
            "target_id": str(target_id) if target_id else None,
            "payload": payload,
            "created_at": datetime.now(timezone.utc),
        },
    )


def get_tenant(*, platform_admin_user_id: UUID, tenant_id: UUID) -> dict[str, Any] | None:
    """Cross-tenant read of a single tenant row. Logs one admin_actions row."""
    with platform_session() as session:
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="read",
            target_tenant_id=tenant_id,
            target_type="tenant",
            target_id=tenant_id,
        )
        row = session.execute(
            text("SELECT * FROM tenants WHERE id = :tenant_id"),
            {"tenant_id": str(tenant_id)},
        ).mappings().first()
        return dict(row) if row else None


def get_tenant_overview(*, platform_admin_user_id: UUID, tenant_id: UUID) -> dict[str, Any] | None:
    """
    The tenant page's Overview tab: the tenant, its tier, its owner and
    invite state, and its intake address. One admin_actions row.
    """
    with platform_session() as session:
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="read",
            target_tenant_id=tenant_id,
            target_type="tenant_overview",
            target_id=tenant_id,
        )
        tenant = session.execute(
            text(
                """
                SELECT t.id, t.name, t.primary_currency, t.timezone, t.status, t.onboarding_status,
                       t.went_live_at, t.invite_sent_at, t.intake_address_active,
                       t.stripe_customer_id, t.stripe_subscription_status, t.created_at,
                       t.onboarding_intake_id, t.test_batch_completed_at,
                       t.setup_fee_amount, t.setup_fee_billing, t.setup_fee_note, t.founding_price,
                       tr.code AS tier_code, tr.name AS tier_name, tr.version AS tier_version,
                       tr.monthly_price AS tier_monthly_price,
                       tr.promo_monthly_price AS tier_promo_monthly_price,
                       tr.promo_days AS tier_promo_days,
                       tr.document_allowance AS tier_document_allowance,
                       sp.code AS setup_fee_preset, sp.name AS setup_fee_preset_name,
                       (SELECT count(*) FROM buyer_merge_candidates c
                          JOIN buyers n ON n.id = c.buyer_id AND n.deleted_at IS NULL
                          JOIN buyers e ON e.id = c.existing_buyer_id AND e.deleted_at IS NULL
                         WHERE c.tenant_id = t.id AND c.status = 'open' AND c.deleted_at IS NULL
                       ) AS open_merge_candidates,
                       (SELECT count(*) FROM learned_rules r
                         WHERE r.tenant_id = t.id AND r.deleted_at IS NULL
                           AND r.status IN ('active', 'disabled')
                       ) AS learned_rules
                FROM tenants t
                LEFT JOIN tiers tr ON tr.id = t.tier_id
                LEFT JOIN setup_fee_presets sp ON sp.id = t.setup_fee_preset_id
                WHERE t.id = :id
                """
            ),
            {"id": str(tenant_id)},
        ).mappings().first()
        if tenant is None:
            return None
        owner = session.execute(
            text(
                "SELECT id, email, auth_user_id IS NOT NULL AS has_login, invite_sent_at "
                "FROM users WHERE tenant_id = :id AND role = 'owner' AND deleted_at IS NULL "
                "ORDER BY created_at LIMIT 1"
            ),
            {"id": str(tenant_id)},
        ).mappings().first()
        address = session.execute(
            text(
                "SELECT address FROM intake_addresses WHERE tenant_id = :id AND status = 'active'"
            ),
            {"id": str(tenant_id)},
        ).scalar()
        return {**dict(tenant), "owner": dict(owner) if owner else None, "intake_address": address}


def list_tenants(*, platform_admin_user_id: UUID, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """
    Cross-tenant read of the tenant list (dashboard, Section 7.15.3). One
    admin_actions row for the whole request, with the filter recorded in the
    payload -- not one row per tenant returned (Section 7.15.1 granularity rule).
    """
    with platform_session() as session:
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="read",
            target_type="tenant_list",
            payload=filters or {},
        )
        rows = session.execute(
            text(
                """
                WITH month_usage AS (
                    -- A linked duplicate counts once (7.16.1), but its AI cost was still
                    -- spent, so only the COUNT excludes it (docflow_core.usage).
                    SELECT d.tenant_id,
                           count(*) FILTER (WHERE d.duplicate_of_document_id IS NULL) AS used,
                           max(d.created_at) AS last_document_at,
                           coalesce(sum(d.est_cost_usd), 0) AS ai_cost_month
                    FROM documents d JOIN tenants t ON t.id = d.tenant_id
                    WHERE NOT d.is_test_batch AND d.deleted_at IS NULL
                      AND d.status NOT IN ('failed', 'quarantined')
                      AND (d.created_at AT TIME ZONE coalesce(nullif(t.timezone, ''), 'UTC'))
                          >= date_trunc('month', now() AT TIME ZONE coalesce(nullif(t.timezone, ''), 'UTC'))
                    GROUP BY d.tenant_id
                ), review_queue AS (
                    SELECT tenant_id, count(*) AS needs_review,
                           extract(epoch FROM (now() - min(created_at))) / 86400 AS oldest_days
                    FROM documents
                    WHERE status = 'needs_review' AND deleted_at IS NULL AND NOT is_test_batch
                    GROUP BY tenant_id
                ), confidence AS (
                    SELECT tenant_id,
                           sum(confidence_sum) FILTER (
                               WHERE day > (now() AT TIME ZONE 'UTC')::date - 30
                           ) AS sum_30,
                           sum(confidence_count) FILTER (
                               WHERE day > (now() AT TIME ZONE 'UTC')::date - 30
                           ) AS count_30,
                           sum(confidence_sum) FILTER (
                               WHERE day > (now() AT TIME ZONE 'UTC')::date - 7
                           ) AS sum_7,
                           sum(confidence_count) FILTER (
                               WHERE day > (now() AT TIME ZONE 'UTC')::date - 7
                           ) AS count_7
                    FROM tenant_daily_metrics
                    WHERE day > (now() AT TIME ZONE 'UTC')::date - 30
                    GROUP BY tenant_id
                )
                SELECT t.*, tr.code AS tier_code, tr.name AS tier_name,
                       tr.monthly_price AS tier_monthly_price,
                       tr.document_allowance AS tier_document_allowance,
                       coalesce(u.used, 0) AS documents_this_month,
                       u.last_document_at,
                       coalesce(u.ai_cost_month, 0) AS ai_cost_this_month,
                       coalesce(q.needs_review, 0) AS needs_review,
                       q.oldest_days AS needs_review_oldest_days,
                       CASE WHEN c.count_30 > 0 THEN round(c.sum_30 / c.count_30, 4) END
                           AS mean_confidence_30,
                       CASE WHEN c.count_7 > 0 THEN round(c.sum_7 / c.count_7, 4) END
                           AS mean_confidence_7
                FROM tenants t
                LEFT JOIN tiers tr ON tr.id = t.tier_id
                LEFT JOIN month_usage u ON u.tenant_id = t.id
                LEFT JOIN review_queue q ON q.tenant_id = t.id
                LEFT JOIN confidence c ON c.tenant_id = t.id
                WHERE t.deleted_at IS NULL
                ORDER BY t.created_at DESC
                """
            )
        ).mappings().all()
        return [dict(r) for r in rows]


# ── Dashboard (Section 7.15.3; D-121) ───────────────────────────────────────


def dashboard(*, platform_admin_user_id: UUID, days: int = 30) -> dict[str, Any]:
    """
    The Console home in one request: the health strip's live numbers, the KPI
    cards from the nightly rollup, and when that rollup last ran.

    One `admin_actions` row for the whole page, not one per number
    (Section 7.15.1's granularity rule). Every KPI comes from
    `tenant_daily_metrics`; nothing here scans documents (Section 7.15.3,
    "Rollup, not live scans"). The health strip is the exception by design --
    each of its numbers is one indexed query about right now, not a period.
    """
    from docflow_core import metrics

    with platform_session() as session:
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="read",
            target_type="dashboard",
            payload={"days": days},
        )
        health = dict(
            session.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM documents
                          WHERE status = 'pending' AND deleted_at IS NULL) AS pending,
                        (SELECT count(*) FROM documents
                          WHERE status = 'processing' AND deleted_at IS NULL) AS processing,
                        (SELECT extract(epoch FROM (now() - min(created_at))) / 60
                           FROM documents
                          WHERE status IN ('pending', 'processing') AND deleted_at IS NULL
                        ) AS oldest_waiting_minutes,
                        (SELECT count(*) FROM documents
                          WHERE created_at >= date_trunc('day', now()) AND deleted_at IS NULL
                            AND NOT is_test_batch) AS documents_today,
                        (SELECT count(*) FROM documents
                          WHERE status = 'needs_review' AND deleted_at IS NULL
                            AND NOT is_test_batch) AS needs_review,
                        (SELECT count(*) FROM extraction_runs
                          WHERE created_at >= now() - interval '1 hour') AS model_calls_hour,
                        (SELECT count(*) FROM extraction_runs
                          WHERE created_at >= now() - interval '1 hour'
                            AND NOT succeeded) AS model_failures_hour,
                        (SELECT coalesce(sum(est_cost_usd), 0) FROM documents
                          WHERE created_at >= date_trunc('day', now())
                            AND NOT is_test_batch) AS spend_today,
                        (SELECT coalesce(sum(est_cost_usd), 0) FROM documents
                          WHERE created_at >= date_trunc('day', now()) - interval '1 day'
                            AND created_at < date_trunc('day', now())
                            AND NOT is_test_batch) AS spend_yesterday,
                        (SELECT max(processed_at) FROM documents) AS last_document_processed_at
                    """
                )
            ).mappings().one()
        )

        rows = session.execute(
            text(
                """
                SELECT * FROM tenant_daily_metrics
                WHERE day > (now() AT TIME ZONE 'UTC')::date - :days
                """
            ),
            {"days": days},
        ).mappings().all()
        previous = session.execute(
            text(
                """
                SELECT * FROM tenant_daily_metrics
                WHERE day > (now() AT TIME ZONE 'UTC')::date - (:days * 2)
                  AND day <= (now() AT TIME ZONE 'UTC')::date - :days
                """
            ),
            {"days": days},
        ).mappings().all()

        money = session.execute(
            text(
                """
                SELECT
                    (SELECT coalesce(sum(tr.monthly_price), 0)
                       FROM tenants t JOIN tiers tr ON tr.id = t.tier_id
                      WHERE t.status = 'active' AND t.deleted_at IS NULL
                        AND t.stripe_subscription_status IN ('active', 'trialing')) AS mrr,
                    (SELECT count(*) FROM tenants
                      WHERE went_live_at IS NOT NULL AND deleted_at IS NULL) AS live_tenants,
                    (SELECT count(*) FROM tenants
                      WHERE went_live_at <= now() - interval '60 days'
                        AND deleted_at IS NULL) AS live_60,
                    (SELECT count(*) FROM tenants
                      WHERE went_live_at <= now() - interval '60 days'
                        AND status = 'active' AND deleted_at IS NULL) AS retained_60,
                    (SELECT count(*) FROM tenants
                      WHERE went_live_at <= now() - interval '90 days'
                        AND deleted_at IS NULL) AS live_90,
                    (SELECT count(*) FROM tenants
                      WHERE went_live_at <= now() - interval '90 days'
                        AND status = 'active' AND deleted_at IS NULL) AS retained_90,
                    (SELECT coalesce(sum(est_cost_usd), 0) FROM tenant_daily_metrics
                      WHERE day >= date_trunc('month', (now() AT TIME ZONE 'UTC')::date)
                    ) AS ai_cost_this_month
                """
            )
        ).mappings().one()
        run = metrics.last_run(session)

    current = metrics.summarise([dict(r) for r in rows])
    before = metrics.summarise([dict(r) for r in previous])
    return {
        "health": health,
        "kpis": current,
        "previous": before,
        "money": dict(money),
        "rollup": run,
        "days": days,
    }


def tenant_metrics(*, platform_admin_user_id: UUID, tenant_id: UUID, days: int = 30) -> dict[str, Any]:
    """The same KPI shape for one tenant (the tenant page's Overview)."""
    from docflow_core import metrics

    with platform_session() as session:
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="read",
            target_tenant_id=tenant_id,
            target_type="tenant_metrics",
            target_id=tenant_id,
            payload={"days": days},
        )
        rows = session.execute(
            text(
                "SELECT * FROM tenant_daily_metrics WHERE tenant_id = :t "
                "AND day > (now() AT TIME ZONE 'UTC')::date - :days"
            ),
            {"t": str(tenant_id), "days": days},
        ).mappings().all()
    return {"days": days, "kpis": metrics.summarise([dict(r) for r in rows])}


# ── Tiers (Section 7.15.2) ──────────────────────────────────────────────────


def list_current_tiers(*, platform_admin_user_id: UUID) -> list[dict[str, Any]]:
    with platform_session() as session:
        _record_admin_action(
            session, platform_admin_user_id=platform_admin_user_id, action="read", target_type="tiers"
        )
        rows = session.execute(
            text(
                "SELECT id, code, version, name, monthly_price, promo_monthly_price, promo_days, "
                "document_allowance FROM tiers WHERE is_current ORDER BY monthly_price"
            )
        ).mappings().all()
        return [dict(r) for r in rows]


def list_setup_fee_presets(*, platform_admin_user_id: UUID) -> list[dict[str, Any]]:
    """The current setup-fee presets (D-117), for the deal-terms form."""
    with platform_session() as session:
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="read",
            target_type="setup_fee_presets",
        )
        return deal_terms.list_current_presets(session)


# ── Intake staging (Section 7.15.2 Step 1) ──────────────────────────────────


def create_intake(
    *,
    platform_admin_user_id: UUID,
    prospect_name: str,
    contact_email: str | None,
    source: str,
    notes: str | None,
) -> UUID:
    intake_id = uuid4()
    with platform_session() as session:
        session.execute(
            text(
                """
                INSERT INTO onboarding_intakes
                    (id, prospect_name, contact_email, source, notes, created_by)
                VALUES (:id, :prospect_name, :contact_email, :source, :notes, :created_by)
                """
            ),
            {
                "id": str(intake_id),
                "prospect_name": prospect_name,
                "contact_email": contact_email,
                "source": source,
                "notes": notes,
                "created_by": str(platform_admin_user_id),
            },
        )
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="intake_create",
            target_type="onboarding_intake",
            target_id=intake_id,
        )
    return intake_id


def add_intake_file(
    *,
    platform_admin_user_id: UUID,
    intake_id: UUID,
    original_filename: str,
    content: bytes,
    detected_type: str,
) -> UUID:
    """
    Store one ALREADY-VALIDATED file in staging. The caller runs the same
    Section 7.11 allowlist check as production intake first (a prospect's
    catalog is an untrusted file); this function is not the place that
    decides what is safe.
    """
    from docflow_core.storage import delete_file, save_staging_file

    file_id = uuid4()
    storage_path = save_staging_file(intake_id, original_filename, content)
    try:
        with platform_session() as session:
            intake = session.execute(
                text(
                    "SELECT linked_tenant_id FROM onboarding_intakes "
                    "WHERE id = :id AND deleted_at IS NULL"
                ),
                {"id": str(intake_id)},
            ).mappings().first()
            if intake is None:
                raise ConsoleError("CON-001")
            if intake["linked_tenant_id"] is not None:
                raise ConsoleError("CON-002")
            session.execute(
                text(
                    """
                    INSERT INTO onboarding_intake_files
                        (id, intake_id, original_filename, storage_path, sha256, byte_size,
                         detected_type)
                    VALUES (:id, :intake_id, :original_filename, :storage_path, :sha256,
                            :byte_size, :detected_type)
                    """
                ),
                {
                    "id": str(file_id),
                    "intake_id": str(intake_id),
                    "original_filename": original_filename,
                    "storage_path": storage_path,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "byte_size": len(content),
                    "detected_type": detected_type,
                },
            )
            _record_admin_action(
                session,
                platform_admin_user_id=platform_admin_user_id,
                action="intake_file_add",
                target_type="onboarding_intake",
                target_id=intake_id,
                payload={"file_id": str(file_id), "detected_type": detected_type},
            )
    except Exception:
        delete_file(storage_path)
        raise
    return file_id


def list_intakes(*, platform_admin_user_id: UUID) -> list[dict[str, Any]]:
    """Every intake, unlinked first."""
    with platform_session() as session:
        _record_admin_action(
            session, platform_admin_user_id=platform_admin_user_id, action="read", target_type="intake_list"
        )
        rows = session.execute(
            text(
                """
                SELECT i.id, i.prospect_name, i.contact_email, i.received_at, i.source,
                       i.linked_tenant_id, t.name AS linked_tenant_name,
                       (SELECT count(*) FROM onboarding_intake_files f
                         WHERE f.intake_id = i.id AND f.deleted_at IS NULL) AS file_count
                FROM onboarding_intakes i
                LEFT JOIN tenants t ON t.id = i.linked_tenant_id
                WHERE i.deleted_at IS NULL
                ORDER BY (i.linked_tenant_id IS NOT NULL), i.received_at DESC
                """
            )
        ).mappings().all()
        return [dict(r) for r in rows]


def get_intake(*, platform_admin_user_id: UUID, intake_id: UUID) -> dict[str, Any] | None:
    with platform_session() as session:
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="read",
            target_type="onboarding_intake",
            target_id=intake_id,
        )
        intake = session.execute(
            text(
                "SELECT id, prospect_name, contact_email, received_at, source, notes, "
                "linked_tenant_id, linked_at FROM onboarding_intakes "
                "WHERE id = :id AND deleted_at IS NULL"
            ),
            {"id": str(intake_id)},
        ).mappings().first()
        if intake is None:
            return None
        files = session.execute(
            text(
                "SELECT id, original_filename, sha256, byte_size, detected_type, created_at "
                "FROM onboarding_intake_files WHERE intake_id = :id AND deleted_at IS NULL "
                "ORDER BY created_at"
            ),
            {"id": str(intake_id)},
        ).mappings().all()
        return {**dict(intake), "files": [dict(f) for f in files]}


# ── Tenant creation (Section 7.15.2 Step 2) ─────────────────────────────────


def create_tenant(
    *,
    platform_admin_user_id: UUID,
    name: str,
    primary_currency: str,
    timezone_name: str,
    owner_email: str,
    tier_code: str = "starter",
    intake_id: UUID | None = None,
    deal: deal_terms.DealTerms | None = None,
    create_stripe_customer: bool = True,
) -> dict[str, Any]:
    """
    Onboarding Step 2 (CLAUDE.md Section 7.15.2), "in a single transaction":
    the tenant (`active`, `tenant_created`) on the CURRENT version of the
    chosen tier; the first owner with a pending invite; the intake address
    with its unguessable token (not live until go-live --
    `intake_address_active` stays false); the linked intake's files moved
    from `staging/` to `tenants/{id}/onboarding/`; the Stripe customer (test
    mode); an `admin_actions` row and a `tenant_lifecycle_events` row.

    "Any failure rolls back everything, including the storage move." Files
    are COPIED into the tenant before the transaction commits and the copies
    deleted if it fails; the staging originals are removed only after it has
    committed. A Stripe customer created for a transaction that then fails is
    deleted again.

    One function, extended from Phase 0 rather than duplicated (Section 10).
    `create_stripe_customer=False` is for tests and for a machine without a
    Stripe key.

    `deal` (D-117) records the price agreed before onboarding -- tier,
    founding price, setup fee -- so go-live never asks. Its tier wins over
    `tier_code`. Without it the tenant has no setup fee yet, and go-live
    refuses (ONB-010) until the Overview's Deal terms are saved.
    """
    from docflow_core.config import get_settings
    from docflow_core.storage import copy_into_tenant, delete_file

    tenant_id = uuid4()
    owner_user_id = uuid4()
    intake_token = uuid4().hex
    settings = get_settings()
    copied: list[str] = []
    originals: list[str] = []
    stripe_customer_id: str | None = None

    try:
        with platform_session() as session:
            resolved = None
            if deal is not None:
                try:
                    resolved = deal_terms.resolve(session, deal)
                except deal_terms.DealTermsError as exc:
                    raise ConsoleError(exc.code, exc.detail) from exc
                tier_code = resolved.tier_code
                tier = {"id": resolved.tier_id}
            else:
                tier = session.execute(
                    text("SELECT id FROM tiers WHERE code = :code AND is_current"),
                    {"code": tier_code},
                ).mappings().first()
            if tier is None:
                raise ConsoleError("CON-003")

            session.execute(
                text(
                    """
                    INSERT INTO tenants
                        (id, name, primary_currency, timezone, status, onboarding_status,
                         tier_id, onboarding_intake_id, created_at, updated_at, status_changed_at)
                    VALUES
                        (:id, :name, :primary_currency, :timezone, 'active', 'tenant_created',
                         :tier_id, :intake_id, now(), now(), now())
                    """
                ),
                {
                    "id": str(tenant_id),
                    "name": name,
                    "primary_currency": primary_currency,
                    "timezone": timezone_name,
                    "tier_id": str(tier["id"]),
                    "intake_id": str(intake_id) if intake_id else None,
                },
            )
            if resolved is not None:
                session.execute(
                    text(
                        """
                        UPDATE tenants SET
                            setup_fee_preset_id = :setup_fee_preset_id,
                            setup_fee_amount = :setup_fee_amount,
                            setup_fee_billing = :setup_fee_billing,
                            setup_fee_note = :setup_fee_note,
                            founding_price = :founding_price
                        WHERE id = :id
                        """
                    ),
                    {**resolved.columns(), "id": str(tenant_id)},
                )
            session.execute(
                text(
                    """
                    INSERT INTO users (id, tenant_id, email, role, is_active, created_at, updated_at)
                    VALUES (:id, :tenant_id, :email, 'owner', true, now(), now())
                    """
                ),
                {"id": str(owner_user_id), "tenant_id": str(tenant_id), "email": owner_email},
            )
            # invite_sent_at stays NULL until "Send invite" (Step 3). The
            # address exists now but is not live: intake_address_active stays
            # false until go-live (Step 9).
            session.execute(
                text(
                    """
                    INSERT INTO intake_addresses (id, tenant_id, token, address, status, created_at)
                    VALUES (:id, :tenant_id, :token, :address, 'active', now())
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "token": intake_token,
                    "address": f"orders+{intake_token}@{settings.intake_email_domain}",
                },
            )

            moved_files = 0
            if intake_id is not None:
                intake = session.execute(
                    text(
                        "SELECT linked_tenant_id FROM onboarding_intakes "
                        "WHERE id = :id AND deleted_at IS NULL FOR UPDATE"
                    ),
                    {"id": str(intake_id)},
                ).mappings().first()
                if intake is None:
                    raise ConsoleError("CON-001")
                if intake["linked_tenant_id"] is not None:
                    raise ConsoleError("CON-002")
                files = session.execute(
                    text(
                        "SELECT id, storage_path FROM onboarding_intake_files "
                        "WHERE intake_id = :id AND deleted_at IS NULL"
                    ),
                    {"id": str(intake_id)},
                ).mappings().all()
                for f in files:
                    new_path = copy_into_tenant(f["storage_path"], tenant_id, area="onboarding")
                    copied.append(new_path)
                    originals.append(f["storage_path"])
                    session.execute(
                        text("UPDATE onboarding_intake_files SET storage_path = :p WHERE id = :id"),
                        {"p": new_path, "id": str(f["id"])},
                    )
                moved_files = len(files)
                session.execute(
                    text(
                        "UPDATE onboarding_intakes SET linked_tenant_id = :tid, linked_at = now() "
                        "WHERE id = :id"
                    ),
                    {"tid": str(tenant_id), "id": str(intake_id)},
                )

            session.execute(
                text(
                    """
                    INSERT INTO tenant_lifecycle_events
                        (id, tenant_id, event_type, actor_user_id, payload, created_at)
                    VALUES (:id, :tenant_id, 'created', :actor, :payload, now())
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "actor": str(platform_admin_user_id),
                    "payload": {
                        "tier": tier_code,
                        "intake_id": str(intake_id) if intake_id else None,
                        "deal": resolved.summary() if resolved else None,
                    },
                },
            )
            _record_admin_action(
                session,
                platform_admin_user_id=platform_admin_user_id,
                action="tenant_create",
                target_tenant_id=tenant_id,
                target_type="tenant",
                target_id=tenant_id,
                payload={
                    "name": name,
                    "owner_email": owner_email,
                    "tier": tier_code,
                    "intake_id": str(intake_id) if intake_id else None,
                    "files_moved": moved_files,
                    "deal": resolved.summary() if resolved else None,
                },
            )

            # Last, so a failure in anything above never leaves a Stripe
            # customer behind. If the commit itself fails, it is deleted below.
            if create_stripe_customer:
                from docflow_core.external_services import create_stripe_customer as _create

                stripe_customer_id = _create(tenant_id=tenant_id, name=name, email=owner_email)
                session.execute(
                    text("UPDATE tenants SET stripe_customer_id = :c WHERE id = :id"),
                    {"c": stripe_customer_id, "id": str(tenant_id)},
                )
    except Exception:
        for path in copied:
            delete_file(path)
        if stripe_customer_id:
            try:
                from docflow_core.external_services import delete_stripe_customer

                delete_stripe_customer(stripe_customer_id)
            except Exception:  # noqa: BLE001 -- logged; the tenant never existed
                logger.error("stripe_customer_cleanup_failed tenant_id=%s", tenant_id)
        raise

    # Committed: the staging originals can go.
    for path in originals:
        delete_file(path)

    return {"tenant_id": tenant_id, "owner_user_id": owner_user_id}


def update_deal_terms(
    *, platform_admin_user_id: UUID, tenant_id: UUID, deal: deal_terms.DealTerms
) -> dict[str, Any]:
    """
    The Overview's "Deal terms" (D-117): change the agreed tier, founding
    price or setup fee any time before go-live. Refused once live (ONB-011)
    -- a live customer's plan is a tier change, with billing. Writes
    `admin_actions` and a `tenant_lifecycle_events` row holding the before
    and after, so a price that moved after the first conversation shows.
    """
    with platform_session() as session:
        row = session.execute(
            text(
                "SELECT onboarding_status, tier_id, setup_fee_preset_id, setup_fee_amount, "
                "setup_fee_billing, founding_price FROM tenants WHERE id = :id FOR UPDATE"
            ),
            {"id": str(tenant_id)},
        ).mappings().first()
        if row is None:
            raise ConsoleError("CON-001")
        if row["onboarding_status"] == "live":
            raise ConsoleError("ONB-011")
        try:
            resolved = deal_terms.resolve(
                session,
                deal,
                current_tier_id=row["tier_id"],
                current_preset_id=row["setup_fee_preset_id"],
            )
        except deal_terms.DealTermsError as exc:
            raise ConsoleError(exc.code, exc.detail) from exc
        before = {
            "tier_id": str(row["tier_id"]) if row["tier_id"] else None,
            "setup_fee_preset_id": str(row["setup_fee_preset_id"]) if row["setup_fee_preset_id"] else None,
            "setup_fee_amount": str(row["setup_fee_amount"]) if row["setup_fee_amount"] is not None else None,
            "setup_fee_billing": row["setup_fee_billing"],
            "founding_price": row["founding_price"],
        }
        session.execute(
            text(
                """
                UPDATE tenants SET
                    tier_id = :tier_id,
                    setup_fee_preset_id = :setup_fee_preset_id,
                    setup_fee_amount = :setup_fee_amount,
                    setup_fee_billing = :setup_fee_billing,
                    setup_fee_note = :setup_fee_note,
                    founding_price = :founding_price,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {**resolved.columns(), "id": str(tenant_id)},
        )
        payload = {"before": before, "after": resolved.summary()}
        session.execute(
            text(
                """
                INSERT INTO tenant_lifecycle_events
                    (id, tenant_id, event_type, actor_user_id, payload, created_at)
                VALUES (:id, :tenant_id, 'deal_terms_changed', :actor, :payload, now())
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": str(tenant_id),
                "actor": str(platform_admin_user_id),
                "payload": payload,
            },
        )
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="deal_terms_update",
            target_tenant_id=tenant_id,
            target_type="tenant",
            target_id=tenant_id,
            payload=payload,
        )
        return resolved.summary()


# ── Invite (Section 7.15.2 Step 3) ──────────────────────────────────────────


def send_invite(*, platform_admin_user_id: UUID, tenant_id: UUID) -> dict[str, Any]:
    """
    Email the tenant's owner a one-time "set your password" link. Re-sendable.

    The link comes from Supabase's admin API and goes out through DocFlow's
    own outbox (D-105) -- held, and readable in the Console, until an email
    provider is configured. The Supabase Auth user is linked to the local
    user row now (`users.auth_user_id`, D-012), so the first sign-in lands in
    the right tenant with the right role.
    """
    from docflow_core import email_outbox, invites

    with platform_session() as session:
        row = session.execute(
            text(
                """
                SELECT t.name AS tenant_name, u.id AS user_id, u.email, u.auth_user_id
                FROM tenants t
                JOIN users u ON u.tenant_id = t.id AND u.role = 'owner' AND u.deleted_at IS NULL
                WHERE t.id = :id
                ORDER BY u.created_at
                LIMIT 1
                """
            ),
            {"id": str(tenant_id)},
        ).mappings().first()
        if row is None:
            raise ConsoleError("CON-004")

        # The same path the Team page's invites take (D-132).
        try:
            outbox_id = invites.issue(
                session,
                tenant_id=tenant_id,
                user_id=row["user_id"],
                email=row["email"],
                existing_auth_user_id=row["auth_user_id"],
                template="invite",
                params={"tenant_name": row["tenant_name"]},
            )
        except invites.InviteLinkedElsewhere:
            raise ConsoleError("CON-005") from None
        session.execute(
            text("UPDATE tenants SET invite_sent_at = now(), updated_at = now() WHERE id = :id"),
            {"id": str(tenant_id)},
        )
        session.execute(
            text(
                """
                INSERT INTO tenant_lifecycle_events
                    (id, tenant_id, event_type, actor_user_id, payload, created_at)
                VALUES (:id, :tenant_id, 'invite_sent', :actor, :payload, now())
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": str(tenant_id),
                "actor": str(platform_admin_user_id),
                "payload": {"user_id": str(row["user_id"]), "email_outbox_id": str(outbox_id)},
            },
        )
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="invite_send",
            target_tenant_id=tenant_id,
            target_type="user",
            target_id=row["user_id"],
        )
    return {"email_outbox_id": outbox_id, "held": not email_outbox.provider_configured()}


# ── Founder alerts and the outbox (Section 7.9 / 7.15.3) ────────────────────


def list_open_alerts(*, platform_admin_user_id: UUID) -> list[dict[str, Any]]:
    """The attention panel: unacknowledged, most severe first."""
    with platform_session() as session:
        _record_admin_action(
            session, platform_admin_user_id=platform_admin_user_id, action="read", target_type="alerts"
        )
        rows = session.execute(
            text(
                """
                SELECT a.id, a.type, a.severity, a.tenant_id, t.name AS tenant_name, a.payload,
                       a.created_at
                FROM founder_alerts a LEFT JOIN tenants t ON t.id = a.tenant_id
                WHERE a.acknowledged_at IS NULL
                ORDER BY array_position(ARRAY['critical','high','warning','info'], a.severity),
                         a.created_at
                """
            )
        ).mappings().all()
        return [dict(r) for r in rows]


def acknowledge_alert(*, platform_admin_user_id: UUID, alert_id: UUID) -> bool:
    with platform_session() as session:
        result = session.execute(
            text(
                "UPDATE founder_alerts SET acknowledged_at = now(), acknowledged_by = :by "
                "WHERE id = :id AND acknowledged_at IS NULL"
            ),
            {"id": str(alert_id), "by": str(platform_admin_user_id)},
        )
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="alert_acknowledge",
            target_type="founder_alert",
            target_id=alert_id,
        )
        return result.rowcount == 1


def list_outbox(
    *, platform_admin_user_id: UUID, tenant_id: UUID | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    with platform_session() as session:
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="read",
            target_tenant_id=tenant_id,
            target_type="email_outbox",
        )
        rows = session.execute(
            text(
                """
                SELECT o.id, o.tenant_id, t.name AS tenant_name, o.to_address, o.template,
                       o.subject, o.body_text, o.status, o.error, o.created_at, o.sent_at
                FROM email_outbox o LEFT JOIN tenants t ON t.id = o.tenant_id
                WHERE (CAST(:tenant_id AS uuid) IS NULL OR o.tenant_id = CAST(:tenant_id AS uuid))
                ORDER BY o.created_at DESC
                LIMIT :limit
                """
            ),
            {"tenant_id": str(tenant_id) if tenant_id else None, "limit": limit},
        ).mappings().all()
        return [dict(r) for r in rows]


# ── Acting inside a tenant (Section 7.15.1) ─────────────────────────────────


def record_console_action(
    *,
    platform_admin_user_id: UUID,
    action: str,
    tenant_id: UUID,
    target_type: str,
    target_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> bool:
    """
    The `admin_actions` row for a Console action that then runs through the
    tenant's own code path with `acting_as_tenant_id` (Section 7.15.1:
    "Acting-as, not impersonation"). Written BEFORE the work, like every
    other Console call. Returns False if the tenant doesn't exist, so the
    route can 404 without doing anything.
    """
    with platform_session() as session:
        exists = session.execute(
            text("SELECT 1 FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
        ).first()
        if exists is None:
            return False
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action=action,
            target_tenant_id=tenant_id,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
        )
    return True


def record_platform_action(
    *,
    platform_admin_user_id: UUID,
    action: str,
    target_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """An `admin_actions` row for a Console action that is about the platform
    rather than one tenant -- recomputing the rollup, for instance. Same rule
    as every other Console write: the row goes in before the work."""
    with platform_session() as session:
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action=action,
            target_type=target_type,
            payload=payload,
        )


def list_tenant_intake_files(*, platform_admin_user_id: UUID, tenant_id: UUID) -> list[dict[str, Any]]:
    """The files moved into this tenant from its intake (Step 2), newest first."""
    with platform_session() as session:
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="read",
            target_tenant_id=tenant_id,
            target_type="tenant_intake_files",
        )
        rows = session.execute(
            text(
                """
                SELECT f.id, f.original_filename, f.detected_type, f.byte_size, f.sha256,
                       f.storage_path, f.created_at
                FROM onboarding_intake_files f
                JOIN onboarding_intakes i ON i.id = f.intake_id
                WHERE i.linked_tenant_id = :tid AND f.deleted_at IS NULL AND i.deleted_at IS NULL
                ORDER BY f.created_at DESC
                """
            ),
            {"tid": str(tenant_id)},
        ).mappings().all()
        return [dict(r) for r in rows]


# ── Lifecycle: wind-down / ready-to-delete queues and hard delete ──────────
# (Section 7.15.4). Cancel and reactivate themselves are NOT here: like
# go-live, they run through the tenant's own session with acting_as_tenant_id
# (Section 7.15.1) -- see docflow_core.lifecycle, called directly from the
# /admin router exactly as onboarding.py already is. These three are
# genuinely cross-tenant (a queue spanning every tenant, and a delete that
# has no single tenant session left to act "as" once its business data is
# gone), so they belong here.


def list_wind_down_queue(*, platform_admin_user_id: UUID) -> list[dict[str, Any]]:
    """Tenants in pending_deletion, soonest deletion first (Section 7.15.4:
    "tenants in pending_deletion with days remaining")."""
    with platform_session() as session:
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="read",
            target_type="wind_down_queue",
        )
        rows = session.execute(
            text(
                """
                SELECT id, name, status, cancellation_reason, cancellation_effective_at,
                       deletion_scheduled_at,
                       GREATEST(0, CEIL(EXTRACT(EPOCH FROM (deletion_scheduled_at - now())) / 86400))
                           AS days_remaining
                FROM tenants
                WHERE status = 'pending_deletion' AND deleted_at IS NULL
                ORDER BY deletion_scheduled_at
                """
            )
        ).mappings().all()
        return [dict(r) for r in rows]


def list_ready_to_delete(*, platform_admin_user_id: UUID) -> list[dict[str, Any]]:
    """pending_deletion tenants whose window has already elapsed -- the only
    ones `delete_tenant` will accept (Section 7.14: "surfaces tenants whose
    window has elapsed as 'ready to delete' and waits")."""
    with platform_session() as session:
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="read",
            target_type="ready_to_delete_queue",
        )
        rows = session.execute(
            text(
                """
                SELECT id, name, cancellation_reason, deletion_scheduled_at
                FROM tenants
                WHERE status = 'pending_deletion' AND deletion_scheduled_at <= now()
                    AND deleted_at IS NULL
                ORDER BY deletion_scheduled_at
                """
            )
        ).mappings().all()
        return [dict(r) for r in rows]


# Business tables purged on hard delete, children before parents (Section
# 7.14: "removes the tenant's business data and storage objects"). Every
# document-child table (headers, lines, warnings, review_actions, snapshots,
# extraction_runs, exports) cascades from `documents`, so deleting documents
# is enough for those. `tenant_lifecycle_events` and `admin_actions` are
# deliberately excluded -- the lifecycle log and the founder's own audit
# trail must outlive the tenant (Section 7.14: "the fact that a tenant
# existed and was removed is retained for accounting purposes even though
# their business data is gone").
#
# The order matters wherever one purged table points at another without
# cascade, and a test (`test_purge_order_respects_every_foreign_key`) checks it
# against the live schema, because getting it wrong once rolled back a real
# delete: `founder_alerts` point at the email each alert sent, and
# `buyer_merges` at the suggestion they came from, so both go first.
_PURGE_TABLES = (
    "documents",
    "intake_rejections",
    "allowance_notices",
    "raw_emails",
    "buyer_merges",
    "buyer_merge_candidates",
    "buyers",
    "items",
    "learned_rules",
    "catalog_imports",
    "import_mapping_templates",
    "intake_addresses",
    "founder_alerts",
    "email_outbox",
    "scheduled_jobs",
    "tenant_daily_metrics",
    "tenant_field_schemas",
    "users",
)


def delete_tenant(
    *, platform_admin_user_id: UUID, tenant_id: UUID, confirm_name: str, reason: str
) -> None:
    """
    Section 7.14's hard delete: "The founder confirmation requires typing
    the tenant name ... and records who, when, and why in an immutable
    deletion-event log that itself is not deleted." Refuses anything not
    already in the ready-to-delete queue (LIFE-006) -- this is the only path
    that can ever purge a tenant's business data, and it is never automatic.

    The `tenants` row itself is never dropped, only soft-deleted: several
    tables that must survive (`tenant_lifecycle_events`, `admin_actions`)
    reference it without ON DELETE CASCADE by design, so a real row delete
    would simply fail its own foreign keys -- soft delete is not a
    convenience here, it is the only state the schema allows.

    Storage objects are removed after the transaction commits, the same
    order as every other cleanup in this module: never delete files for a
    change that might still roll back.
    """
    if len(confirm_name.strip()) == 0 or len(reason.strip()) < 10:
        raise ConsoleError("LIFE-005")
    with platform_session() as session:
        row = session.execute(
            text(
                "SELECT name, status, deletion_scheduled_at FROM tenants "
                "WHERE id = :id FOR UPDATE"
            ),
            {"id": str(tenant_id)},
        ).mappings().first()
        if row is None:
            raise ConsoleError("CON-001")
        if (
            row["status"] != "pending_deletion"
            or row["deletion_scheduled_at"] is None
            or row["deletion_scheduled_at"] > datetime.now(timezone.utc)
        ):
            raise ConsoleError("LIFE-006")
        if confirm_name.strip() != row["name"]:
            raise ConsoleError("LIFE-005")

        # The lifecycle log outlives the tenant, but the tenant's own people do
        # not: an event one of them caused (a Team-page invite, D-132) keeps
        # its row and loses the name. The founder's own events keep theirs.
        session.execute(
            text(
                "UPDATE tenant_lifecycle_events SET actor_user_id = NULL "
                "WHERE tenant_id = :id AND actor_user_id IN (SELECT id FROM users WHERE tenant_id = :id)"
            ),
            {"id": str(tenant_id)},
        )
        counts: dict[str, int] = {}
        for table in _PURGE_TABLES:
            result = session.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = :id"), {"id": str(tenant_id)}
            )
            counts[table] = result.rowcount
        session.execute(
            text("UPDATE onboarding_intakes SET linked_tenant_id = NULL WHERE linked_tenant_id = :id"),
            {"id": str(tenant_id)},
        )
        session.execute(
            text(
                """
                UPDATE tenants SET
                    status = 'deleted',
                    status_changed_at = now(),
                    deleted_at = now(),
                    deleted_by = :by,
                    deletion_reason = :reason,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {"id": str(tenant_id), "by": str(platform_admin_user_id), "reason": reason},
        )
        session.execute(
            text(
                """
                INSERT INTO tenant_lifecycle_events
                    (id, tenant_id, event_type, actor_user_id, payload, created_at)
                VALUES (:id, :tenant_id, 'deleted', :actor, CAST(:payload AS jsonb), now())
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": str(tenant_id),
                "actor": str(platform_admin_user_id),
                "payload": json.dumps({"reason": reason, "rows_deleted": counts}, default=str),
            },
        )
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="tenant_delete",
            target_tenant_id=tenant_id,
            target_type="tenant",
            target_id=tenant_id,
            payload={"reason": reason, "rows_deleted": counts},
        )

    from docflow_core.storage import delete_tenant_storage

    delete_tenant_storage(tenant_id)
