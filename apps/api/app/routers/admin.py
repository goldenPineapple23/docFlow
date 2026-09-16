"""
Founder Console routes (CLAUDE.md Section 7.15). Every route here depends on
`require_platform_admin`, which 404s anyone without an active
platform_admins row. This is the ONLY router allowed to import
docflow_core.admin_data_access (enforced by
apps/api/tests/test_admin_import_boundary.py).

Phase 0 scope: the minimal tenant-creation entry point (Onboarding Step 2,
CLAUDE.md Section 7.15.2) and a cross-tenant read, to prove the
adminDataAccess path end to end. The full nine-step setup tool, dashboard,
and lifecycle actions are built in Phase 5.
"""

from __future__ import annotations

from uuid import UUID

from docflow_core import admin_data_access
from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr

from app.deps import AuthenticatedIdentity, require_platform_admin

router = APIRouter(prefix="/admin", tags=["admin"])


class CreateTenantRequest(BaseModel):
    name: str
    primary_currency: str = "USD"
    timezone: str = "UTC"
    owner_email: EmailStr


@router.post("/tenants/new")
def create_tenant(
    body: CreateTenantRequest,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    result = admin_data_access.create_tenant(
        platform_admin_user_id=identity.local_user_id,
        name=body.name,
        primary_currency=body.primary_currency,
        timezone_name=body.timezone,
        owner_email=body.owner_email,
    )
    return {"tenant_id": str(result["tenant_id"]), "owner_user_id": str(result["owner_user_id"])}


@router.get("/tenants")
def list_tenants(identity: AuthenticatedIdentity = Depends(require_platform_admin)) -> list[dict]:
    tenants = admin_data_access.list_tenants(platform_admin_user_id=identity.local_user_id)
    return [{**t, "id": str(t["id"])} for t in tenants]


@router.get("/tenants/{tenant_id}")
def get_tenant(
    tenant_id: UUID,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict | None:
    tenant = admin_data_access.get_tenant(platform_admin_user_id=identity.local_user_id, tenant_id=tenant_id)
    if tenant:
        tenant = {**tenant, "id": str(tenant["id"])}
    return tenant
