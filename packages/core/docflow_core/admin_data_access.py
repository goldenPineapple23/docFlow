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
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from docflow_core.db import platform_session

logger = logging.getLogger(__name__)


class ConsoleError(Exception):
    """A Console request that cannot be carried out. Carries a founder-audience
    catalog code (CON-0xx): Section 7.16.5 puts every user-facing failure in
    the one catalog, founder-facing ones included."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


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
                       t.onboarding_intake_id,
                       tr.code AS tier_code, tr.name AS tier_name, tr.version AS tier_version,
                       tr.monthly_price AS tier_monthly_price,
                       tr.document_allowance AS tier_document_allowance
                FROM tenants t LEFT JOIN tiers tr ON tr.id = t.tier_id
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
        rows = session.execute(text("SELECT * FROM tenants ORDER BY created_at DESC")).mappings().all()
        return [dict(r) for r in rows]


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
    from docflow_core import email_outbox
    from docflow_core.config import get_settings
    from docflow_core.external_services import generate_invite_link

    settings = get_settings()
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

        link = generate_invite_link(
            email=row["email"], redirect_to=f"{settings.app_base_url}/auth/accept"
        )
        if row["auth_user_id"] is not None and str(row["auth_user_id"]) != str(link.auth_user_id):
            raise ConsoleError("CON-005")
        session.execute(
            text(
                "UPDATE users SET auth_user_id = :auth_user_id, invite_sent_at = now(), "
                "updated_at = now() WHERE id = :id"
            ),
            {"auth_user_id": str(link.auth_user_id), "id": str(row["user_id"])},
        )
        session.execute(
            text("UPDATE tenants SET invite_sent_at = now(), updated_at = now() WHERE id = :id"),
            {"id": str(tenant_id)},
        )
        outbox_id = email_outbox.enqueue(
            session,
            tenant_id=tenant_id,
            to_address=row["email"],
            template="invite",
            params={"tenant_name": row["tenant_name"], "invite_link": link.url},
            related_type="user",
            related_id=row["user_id"],
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
