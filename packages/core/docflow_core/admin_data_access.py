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
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from docflow_core.db import platform_session


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


def create_tenant(
    *,
    platform_admin_user_id: UUID,
    name: str,
    primary_currency: str,
    timezone_name: str,
    owner_email: str,
) -> dict[str, Any]:
    """
    Phase 0 minimal version of Onboarding Step 2 (CLAUDE.md Section 7.15.2).
    Creates the tenant, its first owner user (pending invite), and its
    intake address, all in one transaction. Any failure rolls back
    everything -- there is no partial tenant.

    Phase 5 extends this same function with the Stripe customer, intake
    linking, and file-move steps from the full nine-step setup tool; it does
    not duplicate it (CLAUDE.md Section 10: "write a second ... entry point
    for the Console").
    """
    tenant_id = uuid4()
    owner_user_id = uuid4()
    intake_token = uuid4().hex

    with platform_session() as session:
        session.execute(
            text(
                """
                INSERT INTO tenants (id, name, primary_currency, timezone, status, onboarding_status, created_at, updated_at, status_changed_at)
                VALUES (:id, :name, :primary_currency, :timezone, 'active', 'tenant_created', now(), now(), now())
                """
            ),
            {
                "id": str(tenant_id),
                "name": name,
                "primary_currency": primary_currency,
                "timezone": timezone_name,
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
        # invite_sent_at stays NULL until the "Send invite" action (Onboarding Step 3) runs.
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
                "address": f"orders+{intake_token}@mail.docflow.example",
            },
        )
        session.execute(
            text(
                """
                INSERT INTO tenant_lifecycle_events (id, tenant_id, event_type, actor_user_id, payload, created_at)
                VALUES (:id, :tenant_id, 'created', NULL, :payload, now())
                """
            ),
            {"id": str(uuid4()), "tenant_id": str(tenant_id), "payload": {"created_by_platform_admin": str(platform_admin_user_id)}},
        )
        _record_admin_action(
            session,
            platform_admin_user_id=platform_admin_user_id,
            action="tenant_create",
            target_tenant_id=tenant_id,
            target_type="tenant",
            target_id=tenant_id,
            payload={"name": name, "owner_email": owner_email},
        )

    return {"tenant_id": tenant_id, "owner_user_id": owner_user_id}
