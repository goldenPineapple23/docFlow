"""
Founder Console routes (CLAUDE.md Section 7.15). Every route here depends on
`require_platform_admin`, which 404s anyone without an active
platform_admins row. This is the ONLY router allowed to import
docflow_core.admin_data_access (enforced by
apps/api/tests/test_admin_import_boundary.py).

Slice 5.1: tiers, intake staging (Step 1), tenant creation with tier and
intake (Step 2), the invite (Step 3), the attention panel's alerts, and the
email outbox. Every call writes its `admin_actions` row inside
admin_data_access; nothing here queries the database itself.

Failures are catalog entries (CON-0xx, founder audience), like every other
user-facing failure (Section 7.16.5).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from docflow_core import admin_data_access, file_types
from docflow_core.admin_data_access import ConsoleError
from docflow_core.external_services import ExternalServiceError
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, EmailStr, Field

from app.deps import AuthenticatedIdentity, require_platform_admin
from app.errors import catalog_error

router = APIRouter(prefix="/admin", tags=["admin"])

# Same cap as customer uploads: a prospect's file is untrusted (Section 7.11).
_MAX_UPLOAD_BYTES = file_types.MAX_FILE_SIZE_BYTES


def _admin_id(identity: AuthenticatedIdentity) -> UUID:
    if identity.local_user_id is None:
        raise HTTPException(status_code=404)
    return identity.local_user_id


def _console_error(exc: ConsoleError) -> HTTPException:
    status = 404 if exc.code == "CON-001" else 409
    return catalog_error(exc.code, status_code=status)


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, UUID):
            out[key] = str(value)
        elif hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif isinstance(value, dict):
            out[key] = _jsonable(value)
        elif isinstance(value, list):
            out[key] = [_jsonable(v) if isinstance(v, dict) else v for v in value]
        elif value is not None and type(value).__name__ == "Decimal":
            # Money as a string, all the way to the client (Section 7.1).
            out[key] = str(value)
        else:
            out[key] = value
    return out


# ── Tiers ───────────────────────────────────────────────────────────────────


@router.get("/tiers")
def tiers(identity: AuthenticatedIdentity = Depends(require_platform_admin)) -> dict:
    rows = admin_data_access.list_current_tiers(platform_admin_user_id=_admin_id(identity))
    return {"tiers": [_jsonable(r) for r in rows]}


# ── Intake staging (Step 1) ─────────────────────────────────────────────────


class CreateIntakeRequest(BaseModel):
    prospect_name: str = Field(min_length=1, max_length=200)
    contact_email: EmailStr | None = None
    source: Literal["email", "other"] = "email"
    notes: str | None = Field(default=None, max_length=2000)


@router.get("/intakes")
def intakes(identity: AuthenticatedIdentity = Depends(require_platform_admin)) -> dict:
    rows = admin_data_access.list_intakes(platform_admin_user_id=_admin_id(identity))
    return {"intakes": [_jsonable(r) for r in rows]}


@router.post("/intakes", status_code=201)
def create_intake(
    body: CreateIntakeRequest, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    intake_id = admin_data_access.create_intake(
        platform_admin_user_id=_admin_id(identity),
        prospect_name=body.prospect_name,
        contact_email=body.contact_email,
        source=body.source,
        notes=body.notes,
    )
    return {"intake_id": str(intake_id)}


@router.get("/intakes/{intake_id}")
def intake(intake_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)) -> dict:
    row = admin_data_access.get_intake(platform_admin_user_id=_admin_id(identity), intake_id=intake_id)
    if row is None:
        raise catalog_error("CON-001", status_code=404)
    return {"intake": _jsonable(row)}


@router.post("/intakes/{intake_id}/files", status_code=201)
async def upload_intake_file(
    intake_id: UUID,
    file: UploadFile = File(...),
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    """
    Staging upload. "All 7.11 file-hardening rules apply to staging uploads
    exactly as to production intake" -- the same `validate_upload` the
    customer upload endpoint uses (magic bytes, allowlist, size and
    decompression limits). Nothing is parsed here; parsing happens in the
    worker when the file is used (catalog import, test batch).
    """
    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    filename = file.filename or "upload"
    validation = file_types.validate_upload(content, filename)
    if not validation.ok or validation.file_type is None:
        raise catalog_error(
            "CON-007", status_code=422, extra={"file_error_code": validation.error_code}
        )
    try:
        file_id = admin_data_access.add_intake_file(
            platform_admin_user_id=_admin_id(identity),
            intake_id=intake_id,
            original_filename=filename,
            content=content,
            detected_type=validation.file_type.name.value,
        )
    except ConsoleError as exc:
        raise _console_error(exc) from exc
    return {"file_id": str(file_id)}


# ── Tenants (Steps 2 and 3) ─────────────────────────────────────────────────


class CreateTenantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    primary_currency: str = Field(default="USD", min_length=3, max_length=3)
    timezone: str = "UTC"
    owner_email: EmailStr
    tier: Literal["starter", "growth", "scale"] = "starter"
    intake_id: UUID | None = None


@router.post("/tenants/new")
def create_tenant(
    body: CreateTenantRequest,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    try:
        result = admin_data_access.create_tenant(
            platform_admin_user_id=_admin_id(identity),
            name=body.name,
            primary_currency=body.primary_currency.upper(),
            timezone_name=body.timezone,
            owner_email=body.owner_email,
            tier_code=body.tier,
            intake_id=body.intake_id,
        )
    except ConsoleError as exc:
        raise _console_error(exc) from exc
    except ExternalServiceError as exc:
        raise catalog_error("CON-006", status_code=502, extra={"service": exc.service}) from exc
    return {"tenant_id": str(result["tenant_id"]), "owner_user_id": str(result["owner_user_id"])}


@router.get("/tenants")
def list_tenants(identity: AuthenticatedIdentity = Depends(require_platform_admin)) -> list[dict]:
    tenants = admin_data_access.list_tenants(platform_admin_user_id=_admin_id(identity))
    return [_jsonable(t) for t in tenants]


@router.get("/tenants/{tenant_id}")
def get_tenant(
    tenant_id: UUID,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict | None:
    tenant = admin_data_access.get_tenant(platform_admin_user_id=_admin_id(identity), tenant_id=tenant_id)
    return _jsonable(tenant) if tenant else None


@router.get("/tenants/{tenant_id}/overview")
def tenant_overview(
    tenant_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    row = admin_data_access.get_tenant_overview(
        platform_admin_user_id=_admin_id(identity), tenant_id=tenant_id
    )
    if row is None:
        raise HTTPException(status_code=404)
    return {"tenant": _jsonable(row)}


@router.post("/tenants/{tenant_id}/invite")
def send_invite(tenant_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)) -> dict:
    try:
        result = admin_data_access.send_invite(
            platform_admin_user_id=_admin_id(identity), tenant_id=tenant_id
        )
    except ConsoleError as exc:
        raise _console_error(exc) from exc
    except ExternalServiceError as exc:
        raise catalog_error("CON-006", status_code=502, extra={"service": exc.service}) from exc
    return {"email_outbox_id": str(result["email_outbox_id"]), "held": result["held"]}


# ── Attention panel and outbox (Section 7.9 / 7.15.3) ───────────────────────


@router.get("/alerts")
def alerts(identity: AuthenticatedIdentity = Depends(require_platform_admin)) -> dict:
    rows = admin_data_access.list_open_alerts(platform_admin_user_id=_admin_id(identity))
    return {"alerts": [_jsonable(r) for r in rows]}


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge(alert_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)) -> dict:
    changed = admin_data_access.acknowledge_alert(
        platform_admin_user_id=_admin_id(identity), alert_id=alert_id
    )
    return {"acknowledged": changed}


@router.get("/outbox")
def outbox(
    tenant_id: UUID | None = None, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    rows = admin_data_access.list_outbox(
        platform_admin_user_id=_admin_id(identity), tenant_id=tenant_id
    )
    return {"emails": [_jsonable(r) for r in rows]}
