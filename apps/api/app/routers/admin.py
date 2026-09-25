"""
Founder Console routes (CLAUDE.md Section 7.15). Every route here depends on
`require_platform_admin`, which 404s anyone without an active
platform_admins row. This is the ONLY router allowed to import
docflow_core.admin_data_access (enforced by
apps/api/tests/test_admin_import_boundary.py).

Slices 5.1-5.2: tiers, intake staging (Step 1), tenant creation with tier and
intake (Step 2), the invite (Step 3), the attention panel's alerts, and the
email outbox; catalog and customer-list import (Steps 4-5). Every call writes
its `admin_actions` row inside
admin_data_access; nothing here queries the database itself.

Failures are catalog entries (CON-0xx, founder audience), like every other
user-facing failure (Section 7.16.5).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

from docflow_core import (
    admin_data_access,
    buyer_merge,
    catalog_import,
    deal_terms,
    example_prompting,
    external_services,
    field_schema,
    file_types,
    intake_admin,
    learned_rules,
    lifecycle,
    metrics,
    onboarding,
    quarantine,
    usage,
)
from docflow_core.admin_data_access import ConsoleError
from docflow_core.catalog_import import CatalogImportError
from docflow_core.config import get_settings
from docflow_core.constants import (
    ABUSE_CEILING_MULTIPLIER,
    DAILY_AI_COST_CEILING_USD,
    DAILY_TOKEN_CEILING,
    INVOICE_DAYS_UNTIL_DUE,
    ROLLUP_STALE_HOURS,
    TRIAL_PERIOD_DAYS,
)
from docflow_core.db import tenant_session
from docflow_core.errors import get_error
from docflow_core.external_services import ExternalServiceError
from docflow_core.storage import read_file, save_file
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, EmailStr, Field

from app.actor import Actor
from app.celery_client import celery_client
from app.deps import AuthenticatedIdentity, require_platform_admin
from app.errors import catalog_error
from app.routers.documents import ingest_upload

router = APIRouter(prefix="/admin", tags=["admin"])

# Same cap as customer uploads: a prospect's file is untrusted (Section 7.11).
_MAX_UPLOAD_BYTES = file_types.MAX_FILE_SIZE_BYTES


def _admin_id(identity: AuthenticatedIdentity) -> UUID:
    if identity.local_user_id is None:
        raise HTTPException(status_code=404)
    return identity.local_user_id


# Deal-terms refusals are about what was typed, not the tenant's state.
_UNPROCESSABLE = {"ONB-007", "ONB-012", "ONB-013", "ONB-014"}


def _console_error(exc: ConsoleError) -> HTTPException:
    status = 404 if exc.code == "CON-001" else 422 if exc.code in _UNPROCESSABLE else 409
    return catalog_error(exc.code, status_code=status, extra=exc.detail or None)


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


@router.get("/setup-fee-presets")
def setup_fee_presets(identity: AuthenticatedIdentity = Depends(require_platform_admin)) -> dict:
    rows = admin_data_access.list_setup_fee_presets(platform_admin_user_id=_admin_id(identity))
    return {"presets": [_jsonable(r) for r in rows]}


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


class DealTermsBody(BaseModel):
    """The deal agreed before onboarding (D-117). Amounts come from the
    tiers and setup_fee_presets tables; a typed fee must sit inside its
    preset's range."""

    tier: Literal["starter", "growth", "scale"]
    setup_fee_preset: Literal["founding", "standard", "complex", "waived", "custom"]
    # Money as a string (Section 7.1). Blank = the preset's own amount.
    setup_fee_amount: str | None = Field(default=None, max_length=12)
    setup_fee_billing: Literal["stripe", "invoiced_manually"] = "stripe"
    setup_fee_note: str | None = Field(default=None, max_length=500)
    founding_price: bool = False

    def terms(self) -> deal_terms.DealTerms:
        return deal_terms.DealTerms(
            tier_code=self.tier,
            setup_fee_preset=self.setup_fee_preset,
            setup_fee_amount=self.setup_fee_amount,
            setup_fee_billing=self.setup_fee_billing,
            setup_fee_note=self.setup_fee_note,
            founding_price=self.founding_price,
        )


class CreateTenantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    primary_currency: str = Field(default="USD", min_length=3, max_length=3)
    timezone: str = "UTC"
    owner_email: EmailStr
    tier: Literal["starter", "growth", "scale"] = "starter"
    intake_id: UUID | None = None
    # Optional so a tenant can be created before the price is settled; go-live
    # refuses until it is (ONB-010). The Console form always sends it.
    deal: DealTermsBody | None = None


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
            deal=body.deal.terms() if body.deal else None,
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


@router.put("/tenants/{tenant_id}/deal-terms")
def update_deal_terms(
    tenant_id: UUID,
    body: DealTermsBody,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    """The Overview's Deal terms: editable until go-live, locked after (D-117)."""
    try:
        deal = admin_data_access.update_deal_terms(
            platform_admin_user_id=_admin_id(identity), tenant_id=tenant_id, deal=body.terms()
        )
    except ConsoleError as exc:
        raise _console_error(exc) from exc
    return {"deal": deal}


class TierChangeBody(BaseModel):
    tier: str


@router.post("/tenants/{tenant_id}/tier")
def change_tier(
    tenant_id: UUID,
    body: TierChangeBody,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    """A live tenant's plan change, founder only (Section 7.16.1; D-138).
    Before go-live the plan is part of the deal terms instead (BIL-001)."""
    try:
        result = admin_data_access.change_tier(
            platform_admin_user_id=_admin_id(identity), tenant_id=tenant_id, tier_code=body.tier
        )
    except ConsoleError as exc:
        raise _console_error(exc) from exc
    return {"change": result}


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


# ── Catalog and customer-list import (Steps 4-5; D-108) ─────────────────────
#
# Every route here logs its admin_actions row first, then runs the tenant's
# own import code in a tenant-scoped session with acting_as_tenant_id set
# (Section 7.15.1: acting-as, not impersonation). The file is never read in
# this process: upload stores it and enqueues the worker, which parses it.


def _import_error(exc: CatalogImportError) -> HTTPException:
    status = 422 if exc.code in ("IMP-001", "IMP-007", "IMP-008") else 409
    return catalog_error(exc.code, status_code=status)


def _console_act(identity: AuthenticatedIdentity, tenant_id: UUID, action: str, **kw: Any) -> UUID:
    admin_id = _admin_id(identity)
    if not admin_data_access.record_console_action(
        platform_admin_user_id=admin_id, action=action, tenant_id=tenant_id, **kw
    ):
        raise HTTPException(status_code=404)
    return admin_id


# ── Acting-as: the tenant's own review and export routes (D-111) ────────────


# Path parameters that name the thing acted on, in the order preferred for
# the audit row's target_id.
_ACTING_TARGETS = (("document_id", "document"), ("export_id", "export"))


def acting_as_gate(
    request: Request,
    acting_tenant_id: UUID,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> None:
    """
    The Console's way into a tenant's review and export routes (Section
    7.15.1, "Acting-as, not impersonation"). main.py mounts those routers a
    second time under /admin/tenants/{acting_tenant_id}/act with this as a
    router dependency, so it runs before the route's own `current_actor`.

    404 to anyone who is not a platform admin (require_platform_admin), and
    for a tenant that doesn't exist. One `admin_actions` row per request,
    written before the work, naming the route template -- never a value from
    a document. Then the route runs in the tenant's own session as the
    founder, with acting_as_tenant_id on everything it writes.
    """
    route = request.scope.get("route")
    target_type, target_id = "tenant", None
    for param, kind in _ACTING_TARGETS:
        if param in request.path_params:
            try:
                target_id = UUID(str(request.path_params[param]))
            except ValueError:
                raise HTTPException(status_code=404) from None
            target_type = kind
            break
    admin_id = _console_act(
        identity,
        acting_tenant_id,
        "acting_as_read" if request.method == "GET" else "acting_as_write",
        target_type=target_type,
        target_id=target_id,
        payload={"method": request.method, "route": getattr(route, "path_format", None)},
    )
    request.state.acting_actor = Actor(
        tenant_id=acting_tenant_id, user_id=admin_id, role=None, acting_as_tenant_id=acting_tenant_id
    )


def _start_import(
    tenant_id: UUID,
    admin_id: UUID,
    *,
    kind: str,
    source: str,
    original_filename: str,
    storage_path: str,
    content: bytes,
    file_type: str,
    intake_file_id: UUID | None = None,
) -> str:
    with tenant_session(tenant_id) as session:
        import_id = catalog_import.create_import(
            session,
            tenant_id,
            kind=kind,
            source=source,
            original_filename=original_filename,
            storage_path=storage_path,
            file_sha256=hashlib.sha256(content).hexdigest(),
            file_type=file_type,
            created_by=admin_id,
            intake_file_id=intake_file_id,
            acting_as_tenant_id=tenant_id,
        )
    celery_client.send_task(
        "docflow.parse_import", args=[str(tenant_id), str(import_id)], queue="interactive"
    )
    return str(import_id)


@router.get("/tenants/{tenant_id}/intake-files")
def tenant_intake_files(
    tenant_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    rows = admin_data_access.list_tenant_intake_files(
        platform_admin_user_id=_admin_id(identity), tenant_id=tenant_id
    )
    # The storage path never reaches a client (Section 7.4).
    return {"files": [_jsonable({k: v for k, v in r.items() if k != "storage_path"}) for r in rows]}


@router.get("/tenants/{tenant_id}/imports")
def list_imports(
    tenant_id: UUID,
    kind: Literal["catalog", "buyers"],
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    _console_act(identity, tenant_id, "read", target_type=f"{kind}_imports")
    with tenant_session(tenant_id) as session:
        rows = catalog_import.list_imports(session, kind)
    return {"imports": [_jsonable(r) for r in rows]}


@router.post("/tenants/{tenant_id}/imports", status_code=202)
async def upload_import(
    tenant_id: UUID,
    kind: Literal["catalog", "buyers"] = Form(...),
    file: UploadFile = File(...),
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    filename = file.filename or "upload"
    validation = file_types.validate_upload(content, filename)
    if not validation.ok or validation.file_type is None:
        raise catalog_error(
            "CON-007", status_code=422, extra={"file_error_code": validation.error_code}
        )
    file_type = validation.file_type.name.value
    if file_type not in catalog_import.TABLE_FORMATS:
        raise catalog_error("IMP-001", status_code=422)
    admin_id = _console_act(identity, tenant_id, f"{kind}_import_upload", target_type="catalog_import")
    storage_path = save_file(tenant_id, filename, content)
    import_id = _start_import(
        tenant_id,
        admin_id,
        kind=kind,
        source="upload",
        original_filename=filename,
        storage_path=storage_path,
        content=content,
        file_type=file_type,
    )
    return {"import_id": import_id}


class ImportFromIntakeRequest(BaseModel):
    kind: Literal["catalog", "buyers"]
    intake_file_id: UUID


@router.post("/tenants/{tenant_id}/imports/from-intake", status_code=202)
def import_from_intake(
    tenant_id: UUID,
    body: ImportFromIntakeRequest,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    files = admin_data_access.list_tenant_intake_files(
        platform_admin_user_id=_admin_id(identity), tenant_id=tenant_id
    )
    chosen = next((f for f in files if str(f["id"]) == str(body.intake_file_id)), None)
    if chosen is None:
        raise catalog_error("CON-001", status_code=404)
    if chosen["detected_type"] not in catalog_import.TABLE_FORMATS:
        raise catalog_error("IMP-001", status_code=422)
    admin_id = _console_act(
        identity,
        tenant_id,
        f"{body.kind}_import_from_intake",
        target_type="onboarding_intake_file",
        target_id=body.intake_file_id,
    )
    content = read_file(chosen["storage_path"])
    import_id = _start_import(
        tenant_id,
        admin_id,
        kind=body.kind,
        source="intake_file",
        original_filename=chosen["original_filename"],
        storage_path=chosen["storage_path"],
        content=content,
        file_type=chosen["detected_type"],
        intake_file_id=body.intake_file_id,
    )
    return {"import_id": import_id}


@router.get("/tenants/{tenant_id}/imports/{import_id}")
def import_preview(
    tenant_id: UUID, import_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    _console_act(identity, tenant_id, "read", target_type="catalog_import", target_id=import_id)
    with tenant_session(tenant_id) as session:
        try:
            result = catalog_import.preview(session, import_id)
        except LookupError as exc:
            raise HTTPException(status_code=404) from exc
        except CatalogImportError as exc:
            raise _import_error(exc) from exc
    # The screen renders catalog wording as given, never its own (7.16.5).
    if result.get("error_code"):
        result["error"] = _catalog_entry(result["error_code"])
    for finding in result.get("report") or []:
        finding.update(_catalog_entry(finding["code"]))
    return {"import": _jsonable(result)}


def _catalog_entry(code: str) -> dict[str, str]:
    entry = get_error(code)
    return {"code": entry.code, "title": entry.title, "message": entry.message, "action": entry.action}


class MappingRequest(BaseModel):
    mapping: dict[str, int | None]


@router.put("/tenants/{tenant_id}/imports/{import_id}/mapping")
def set_import_mapping(
    tenant_id: UUID,
    import_id: UUID,
    body: MappingRequest,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    _console_act(
        identity, tenant_id, "import_mapping_set", target_type="catalog_import", target_id=import_id
    )
    with tenant_session(tenant_id) as session:
        try:
            catalog_import.set_mapping(session, import_id, body.mapping)
        except LookupError as exc:
            raise HTTPException(status_code=404) from exc
        except CatalogImportError as exc:
            raise _import_error(exc) from exc
    return {"ok": True}


class RowFixRequest(BaseModel):
    field: str
    value: str | None = Field(default=None, max_length=2000)


@router.put("/tenants/{tenant_id}/imports/{import_id}/rows/{row_number}")
def fix_import_row(
    tenant_id: UUID,
    import_id: UUID,
    row_number: int,
    body: RowFixRequest,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    _console_act(
        identity,
        tenant_id,
        "import_row_fix",
        target_type="catalog_import",
        target_id=import_id,
        payload={"row": row_number, "field": body.field},
    )
    with tenant_session(tenant_id) as session:
        try:
            catalog_import.set_override(session, import_id, row_number, body.field, body.value)
        except LookupError as exc:
            raise HTTPException(status_code=404) from exc
        except CatalogImportError as exc:
            raise _import_error(exc) from exc
    return {"ok": True}


@router.post("/tenants/{tenant_id}/imports/{import_id}/commit")
def commit_import(
    tenant_id: UUID, import_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    admin_id = _console_act(
        identity, tenant_id, "import_commit", target_type="catalog_import", target_id=import_id
    )
    with tenant_session(tenant_id) as session:
        try:
            summary = catalog_import.commit_import(
                session, tenant_id, import_id, user_id=admin_id, acting_as_tenant_id=tenant_id
            )
        except LookupError as exc:
            raise HTTPException(status_code=404) from exc
        except CatalogImportError as exc:
            raise _import_error(exc) from exc
    return {"summary": summary}


@router.post("/tenants/{tenant_id}/imports/{import_id}/discard")
def discard_import(
    tenant_id: UUID, import_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    _console_act(
        identity, tenant_id, "import_discard", target_type="catalog_import", target_id=import_id
    )
    with tenant_session(tenant_id) as session:
        try:
            catalog_import.discard_import(session, import_id)
        except LookupError as exc:
            raise HTTPException(status_code=404) from exc
        except CatalogImportError as exc:
            raise _import_error(exc) from exc
    return {"ok": True}


# ── Test batch and go-live (Steps 6-9; D-112, D-113) ────────────────────────


def _onboarding_error(exc: onboarding.OnboardingError) -> HTTPException:
    return catalog_error(exc.code, status_code=409, extra=exc.detail or None)


@router.get("/tenants/{tenant_id}/test-batch")
def get_test_batch(
    tenant_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    """Steps 6-8 on the tenant page: every test document, its status and cost."""
    _console_act(identity, tenant_id, "read", target_type="test_batch")
    with tenant_session(tenant_id) as session:
        documents = onboarding.list_test_batch_documents(session)
    return {"documents": [_jsonable(d) for d in documents]}


@router.post("/tenants/{tenant_id}/test-batch")
async def upload_test_batch(
    tenant_id: UUID,
    files: list[UploadFile] = File(...),
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    """
    Step 6: "Multi-file upload of the 5-10 sample POs through the same upload
    endpoint as production, with is_test_batch = true." The same
    `ingest_upload` a tenant's own upload runs -- all 7.11 checks included --
    with each file stored as 'staged' until Step 7. One file failing its
    checks does not stop the others; each gets its own result.
    """
    admin_id = _console_act(
        identity, tenant_id, "test_batch_upload", target_type="test_batch", payload={"files": len(files)}
    )
    with tenant_session(tenant_id) as session:
        try:
            onboarding.check_test_batch_open(session, tenant_id)
        except onboarding.OnboardingError as exc:
            raise _onboarding_error(exc) from exc

    results: list[dict[str, Any]] = []
    for upload in files:
        content = await upload.read(_MAX_UPLOAD_BYTES + 1)
        filename = upload.filename or "upload"
        try:
            result = ingest_upload(tenant_id, filename, content, is_test_batch=True)
            results.append({"filename": filename, **result})
        except HTTPException as exc:
            results.append({"filename": filename, "error": exc.detail})

    stored = [r["document_id"] for r in results if "document_id" in r]
    if stored:
        with tenant_session(tenant_id) as session:
            onboarding.record_test_batch_upload(session, tenant_id, stored, actor_user_id=admin_id)
    return {"results": results}


@router.post("/tenants/{tenant_id}/test-batch/run")
def run_test_batch(
    tenant_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    """Step 7: the staged documents go through the normal pipeline at
    interactive priority. Enqueued after commit, oldest first."""
    admin_id = _console_act(identity, tenant_id, "test_batch_run", target_type="test_batch")
    with tenant_session(tenant_id) as session:
        try:
            document_ids = onboarding.start_test_batch_run(session, tenant_id, actor_user_id=admin_id)
        except onboarding.OnboardingError as exc:
            raise _onboarding_error(exc) from exc
    for document_id in document_ids:
        celery_client.send_task(
            "docflow.parse_and_extract", args=[str(tenant_id), str(document_id)], queue="interactive"
        )
    return {"started": len(document_ids)}


@router.post("/tenants/{tenant_id}/test-batch/complete")
def complete_test_batch(
    tenant_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    """Step 8: "When every test-batch document is approved, ... 'Mark test
    batch complete'", which unlocks go-live."""
    admin_id = _console_act(identity, tenant_id, "test_batch_complete", target_type="test_batch")
    with tenant_session(tenant_id) as session:
        try:
            onboarding.mark_test_batch_complete(session, tenant_id, actor_user_id=admin_id)
        except onboarding.OnboardingError as exc:
            raise _onboarding_error(exc) from exc
    return {"onboarding_status": "test_batch_complete"}


@router.get("/tenants/{tenant_id}/go-live")
def go_live_plan(
    tenant_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    """What go-live will do and bill, for the summary -- all from the deal
    recorded on the tenant and its tier, never typed in (D-117)."""
    _console_act(identity, tenant_id, "read", target_type="go_live_plan")
    with tenant_session(tenant_id) as session:
        try:
            plan = onboarding.plan_go_live(session, tenant_id)
        except onboarding.OnboardingError as exc:
            raise _onboarding_error(exc) from exc
    promo = plan.promo_monthly_price
    return {
        "tier_name": plan.tier_name,
        "monthly_price": str(plan.monthly_price),
        "promo_monthly_price": str(promo) if promo is not None else None,
        "promo_months": plan.promo_months,
        "document_allowance": plan.document_allowance,
        "invite_sent": plan.invite_sent,
        "invoice_days_until_due": INVOICE_DAYS_UNTIL_DUE,
        "trial_period_days": TRIAL_PERIOD_DAYS,
        "setup_fee_amount": str(plan.setup_fee_amount),
        "setup_fee_billing": plan.setup_fee_billing,
        "setup_fee_note": plan.setup_fee_note,
        "setup_fee_preset_name": plan.setup_fee_preset_name,
        "founding_price": plan.founding_price,
    }


@router.post("/tenants/{tenant_id}/go-live")
def go_live(
    tenant_id: UUID,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    """
    Step 9, in an order that is safe to retry at any point:
      1. Stripe: subscription (+ setup fee, + founding coupon). Retry-safe by
         construction (external_services.start_subscription).
      2. The invite, if it hasn't gone -- re-sendable anyway (Step 3).
      3. One database transaction: intake live, go-live email queued,
         first-week check-in scheduled, onboarding_status 'live'.
    A failure in 1 or 2 leaves the tenant not live, and trying again repeats
    nothing that already happened (ONB-008).

    Nothing about price is sent: go-live bills the deal recorded on the
    tenant (D-117), refusing with ONB-010 if there isn't one.

    The subscription starts on a trial (D-125): the tenant is fully live and
    usable immediately, but Stripe generates no invoice -- for the first
    month or the setup fee -- until TRIAL_PERIOD_DAYS after this moment.
    """
    admin_id = _console_act(identity, tenant_id, "go_live", target_type="tenant", target_id=tenant_id)
    with tenant_session(tenant_id) as session:
        try:
            plan = onboarding.plan_go_live(session, tenant_id)
        except onboarding.OnboardingError as exc:
            raise _onboarding_error(exc) from exc
    founding = plan.founding_price
    fee = plan.setup_fee_amount
    if plan.customer_id is None:
        raise catalog_error("ONB-008", status_code=502, extra={"reason": "no Stripe customer"})

    trial_end = int((datetime.now(UTC) + timedelta(days=TRIAL_PERIOD_DAYS)).timestamp())
    try:
        subscription = external_services.start_subscription(
            tenant_id=tenant_id,
            customer_id=plan.customer_id,
            tier_id=plan.tier_id,
            tier_name=plan.tier_name,
            monthly_price=plan.monthly_price,
            promo_monthly_price=plan.promo_monthly_price if founding else None,
            promo_months=plan.promo_months if founding else None,
            setup_fee=fee if plan.setup_fee_billing == "stripe" and fee > 0 else None,
            days_until_due=INVOICE_DAYS_UNTIL_DUE,
            trial_end=trial_end,
        )
    except ExternalServiceError as exc:
        raise catalog_error("ONB-008", status_code=502) from exc

    if not plan.invite_sent:
        try:
            admin_data_access.send_invite(platform_admin_user_id=admin_id, tenant_id=tenant_id)
        except ConsoleError as exc:
            raise _console_error(exc) from exc
        except ExternalServiceError as exc:
            raise catalog_error("CON-006", status_code=502) from exc

    with tenant_session(tenant_id) as session:
        try:
            onboarding.complete_go_live(
                session,
                tenant_id,
                actor_user_id=admin_id,
                plan=plan,
                billing=onboarding.GoLiveBilling(
                    subscription_id=subscription.subscription_id,
                    subscription_status=subscription.status,
                    current_period_end=subscription.current_period_end,
                    founding_ends_at=subscription.founding_ends_at,
                ),
                app_url=get_settings().app_base_url,
            )
        except onboarding.OnboardingError as exc:
            raise _onboarding_error(exc) from exc
    return {"onboarding_status": "live"}


# ── Lifecycle actions: cancel, reactivate, wind-down, delete (5.6; D-123) ───
# Cancel and reactivate run through the tenant's own session with
# acting_as_tenant_id, exactly like go-live above -- not admin_data_access,
# which is reserved for genuinely cross-tenant reads/writes (Section 7.15.1).


def _lifecycle_error(exc: lifecycle.LifecycleError) -> HTTPException:
    status = 404 if exc.code == "CON-001" else 409
    return catalog_error(exc.code, status_code=status, extra=exc.detail or None)


@router.get("/tenants/{tenant_id}/lifecycle")
def get_lifecycle(
    tenant_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    _console_act(identity, tenant_id, "read", target_type="lifecycle")
    with tenant_session(tenant_id) as session:
        row = lifecycle.status(session, tenant_id)
    if row is None:
        raise HTTPException(status_code=404)
    return _jsonable(row)


class CancelRequest(BaseModel):
    reason: Literal["customer_requested", "non_payment", "for_cause"]
    note: str | None = None
    override_effective_at: datetime | None = None


@router.post("/tenants/{tenant_id}/cancel")
def cancel_tenant(
    tenant_id: UUID,
    body: CancelRequest,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    """Section 7.15.4's cancel form: the effective date is computed from the
    reason and shown before confirmation; the founder may only push it
    later, never earlier (LIFE-003)."""
    admin_id = _console_act(
        identity, tenant_id, "cancel", target_type="tenant", target_id=tenant_id,
        payload={"reason": body.reason},
    )
    with tenant_session(tenant_id) as session:
        try:
            result = lifecycle.cancel(
                session,
                tenant_id,
                reason=body.reason,
                note=body.note,
                actor_user_id=admin_id,
                override_effective_at=body.override_effective_at,
            )
        except lifecycle.LifecycleError as exc:
            raise _lifecycle_error(exc) from exc
    return _jsonable(result)


@router.get("/tenants/{tenant_id}/cancel/preview")
def preview_cancel(
    tenant_id: UUID,
    reason: Literal["customer_requested", "non_payment", "for_cause"],
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    """The computed effective date and the rule that produced it, for the
    cancel form to show before the founder confirms (Section 7.15.4)."""
    _console_act(identity, tenant_id, "read", target_type="cancel_preview")
    with tenant_session(tenant_id) as session:
        try:
            plan = lifecycle.compute_effective_at(session, tenant_id, reason)
        except lifecycle.LifecycleError as exc:
            raise _lifecycle_error(exc) from exc
    return {
        "effective_at": plan.effective_at.isoformat(),
        "rule": plan.rule,
        "flagged": plan.flagged,
    }


@router.post("/tenants/{tenant_id}/reactivate")
def reactivate_tenant(
    tenant_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    """
    Section 7.15.4: "Resumes the Stripe subscription (or creates a new one
    on the tenant's tier version) and returns the tenant to active ... no
    re-onboarding, no data loss." Reuses start_subscription exactly as
    go-live does: it returns the tenant's existing non-cancelled
    subscription if one somehow still exists, or creates a fresh one --
    the suspend sweep already cancelled the old one at Stripe.
    """
    admin_id = _console_act(identity, tenant_id, "reactivate", target_type="tenant", target_id=tenant_id)
    with tenant_session(tenant_id) as session:
        try:
            plan = lifecycle.plan_reactivate(session, tenant_id)
        except lifecycle.LifecycleError as exc:
            raise _lifecycle_error(exc) from exc
    if plan.customer_id is None:
        raise catalog_error("CON-006", status_code=502, extra={"reason": "no Stripe customer"})
    try:
        subscription = external_services.start_subscription(
            tenant_id=tenant_id,
            customer_id=plan.customer_id,
            tier_id=plan.tier_id,
            tier_name=plan.tier_name,
            monthly_price=plan.monthly_price,
            promo_monthly_price=None,
            promo_months=None,
            setup_fee=None,
            days_until_due=INVOICE_DAYS_UNTIL_DUE,
            trial_end=None,  # D-125's trial is a go-live perk, not a reactivation one
        )
    except ExternalServiceError as exc:
        raise catalog_error("CON-006", status_code=502) from exc

    with tenant_session(tenant_id) as session:
        try:
            lifecycle.complete_reactivate(
                session,
                tenant_id,
                actor_user_id=admin_id,
                plan=plan,
                billing=lifecycle.ReactivateBilling(
                    subscription_id=subscription.subscription_id,
                    subscription_status=subscription.status,
                    current_period_end=subscription.current_period_end,
                ),
                app_url=get_settings().app_base_url,
            )
        except lifecycle.LifecycleError as exc:
            raise _lifecycle_error(exc) from exc
    return {"status": "active"}


@router.get("/lifecycle/wind-down")
def wind_down_queue(identity: AuthenticatedIdentity = Depends(require_platform_admin)) -> dict:
    rows = admin_data_access.list_wind_down_queue(platform_admin_user_id=_admin_id(identity))
    return {"tenants": [_jsonable(r) for r in rows]}


@router.get("/lifecycle/ready-to-delete")
def ready_to_delete_queue(identity: AuthenticatedIdentity = Depends(require_platform_admin)) -> dict:
    rows = admin_data_access.list_ready_to_delete(platform_admin_user_id=_admin_id(identity))
    return {"tenants": [_jsonable(r) for r in rows]}


class DeleteTenantRequest(BaseModel):
    confirm_name: str = Field(min_length=1)
    reason: str = Field(min_length=10)


@router.post("/tenants/{tenant_id}/delete")
def delete_tenant(
    tenant_id: UUID,
    body: DeleteTenantRequest,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    """Section 7.14: type-to-confirm, irreversible. Only reachable for a
    tenant already in the Ready to delete queue (LIFE-006)."""
    try:
        admin_data_access.delete_tenant(
            platform_admin_user_id=_admin_id(identity),
            tenant_id=tenant_id,
            confirm_name=body.confirm_name,
            reason=body.reason,
        )
    except ConsoleError as exc:
        raise _console_error(exc) from exc
    return {"status": "deleted"}


# ── Operator screens: buyer merge and learned rules (slice 5.4; D-119) ──────
# Both run in the tenant's own session as the founder, acting-as (Section
# 7.15.1): one admin_actions row per request, and every row they write
# records the founder's user id and acting_as_tenant_id.


class MergeRequest(BaseModel):
    keep_buyer_id: UUID


def _merge_error(exc: buyer_merge.MergeError) -> HTTPException:
    return catalog_error(exc.code, status_code=409, extra=exc.detail or None)


@router.get("/tenants/{tenant_id}/buyer-merges")
def get_buyer_merges(
    tenant_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    """Near-duplicate customers waiting for a decision, and recent merges."""
    _console_act(identity, tenant_id, "read", target_type="buyer_merges")
    with tenant_session(tenant_id) as session:
        candidates = buyer_merge.list_open_candidates(session)
        history = buyer_merge.list_merges(session)
    return {
        "candidates": [_jsonable(c) for c in candidates],
        "history": [
            {**_jsonable(h), "by_docflow_support": h["acting_as_tenant_id"] is not None} for h in history
        ],
    }


@router.post("/tenants/{tenant_id}/buyer-merges/{candidate_id}/merge")
def merge_buyers(
    tenant_id: UUID,
    candidate_id: UUID,
    body: MergeRequest,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    admin_id = _console_act(
        identity,
        tenant_id,
        "buyer_merge",
        target_type="buyer_merge_candidate",
        target_id=candidate_id,
        payload={"keep_buyer_id": str(body.keep_buyer_id)},
    )
    with tenant_session(tenant_id) as session:
        try:
            result = buyer_merge.merge_candidate(
                session,
                tenant_id,
                candidate_id,
                keep_buyer_id=body.keep_buyer_id,
                actor_user_id=admin_id,
                acting_as_tenant_id=tenant_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404) from exc
        except buyer_merge.MergeError as exc:
            raise _merge_error(exc) from exc
    return {
        "merge_id": str(result.merge_id),
        "documents_moved": result.documents_moved,
        "rules_moved": result.rules_moved,
        "fields_filled": result.fields_filled,
    }


@router.post("/tenants/{tenant_id}/buyer-merges/{candidate_id}/dismiss")
def dismiss_buyer_merge(
    tenant_id: UUID, candidate_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    admin_id = _console_act(
        identity,
        tenant_id,
        "buyer_merge_dismiss",
        target_type="buyer_merge_candidate",
        target_id=candidate_id,
    )
    with tenant_session(tenant_id) as session:
        try:
            buyer_merge.dismiss_candidate(session, candidate_id, actor_user_id=admin_id)
        except LookupError as exc:
            raise HTTPException(status_code=404) from exc
        except buyer_merge.MergeError as exc:
            raise _merge_error(exc) from exc
    return {"ok": True}


@router.get("/tenants/{tenant_id}/rules")
def get_rules(tenant_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)) -> dict:
    _console_act(identity, tenant_id, "read", target_type="learned_rules")
    with tenant_session(tenant_id) as session:
        rows = learned_rules.list_rules(session)
    return {
        "rules": [
            {**_jsonable(r), "by_docflow_support": r["acting_as_tenant_id"] is not None} for r in rows
        ]
    }


def _rule_change(identity: AuthenticatedIdentity, tenant_id: UUID, rule_id: UUID, action: str) -> dict:
    _console_act(
        identity, tenant_id, f"learned_rule_{action}", target_type="learned_rule", target_id=rule_id
    )
    with tenant_session(tenant_id) as session:
        try:
            if action == "delete":
                change = learned_rules.delete_rule(session, rule_id)
            else:
                change = learned_rules.set_status(session, rule_id, enabled=action == "enable")
        except LookupError as exc:
            raise HTTPException(status_code=404) from exc
        except learned_rules.RuleError as exc:
            raise catalog_error(exc.code, status_code=409) from exc
    return {"change": change}


@router.post("/tenants/{tenant_id}/rules/{rule_id}/disable")
def disable_rule(
    tenant_id: UUID, rule_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    return _rule_change(identity, tenant_id, rule_id, "disable")


@router.post("/tenants/{tenant_id}/rules/{rule_id}/enable")
def enable_rule(
    tenant_id: UUID, rule_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    return _rule_change(identity, tenant_id, rule_id, "enable")


@router.post("/tenants/{tenant_id}/rules/{rule_id}/delete")
def delete_rule(
    tenant_id: UUID, rule_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    return _rule_change(identity, tenant_id, rule_id, "delete")


# ── Per-tenant field schema (slice 5.4 part 2; D-120) ───────────────────────


class FieldSchemaRequest(BaseModel):
    # {"header": {"payment_terms": "hidden"}, "line": {"unit": "hidden"}}
    #
    # Plain strings, not Literals: an unknown field or state is answered with
    # the catalog's FLD-001, not FastAPI's generic validation body (7.16.5) --
    # the same reason ExportBody takes a plain format string.
    fields: dict[str, dict[str, str]]
    note: str | None = Field(default=None, max_length=500)
    # Re-check orders still waiting for review against the new version, so a
    # change the founder makes during onboarding shows on the test batch
    # immediately instead of only on the next order.
    apply_to_open_documents: bool = True


@router.get("/tenants/{tenant_id}/field-schema")
def get_field_schema(
    tenant_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    _console_act(identity, tenant_id, "read", target_type="field_schema")
    with tenant_session(tenant_id) as session:
        schema = field_schema.current(session, tenant_id)
        past = field_schema.history(session, tenant_id)
    return {"schema": schema.as_dict(), "history": [_jsonable(row) for row in past]}


@router.put("/tenants/{tenant_id}/field-schema")
def put_field_schema(
    tenant_id: UUID,
    body: FieldSchemaRequest,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    """A new version, never an edit of the one in use (Section 7.13)."""
    admin_id = _console_act(
        identity,
        tenant_id,
        "field_schema_save",
        target_type="field_schema",
        payload={"fields": body.fields},
    )
    with tenant_session(tenant_id) as session:
        try:
            schema = field_schema.save(
                session,
                tenant_id,
                body.fields,
                actor_user_id=admin_id,
                acting_as_tenant_id=tenant_id,
                note=body.note,
            )
        except field_schema.FieldSchemaError as exc:
            raise catalog_error(exc.code, status_code=422, extra=exc.detail or None) from exc
        rechecked = (
            field_schema.recheck_open_documents(session, tenant_id, schema)
            if body.apply_to_open_documents
            else 0
        )
    return {"schema": schema.as_dict(), "rechecked_documents": rechecked}


# ── Tenant audit trail (Section 7.15.3 "Audit" tab; D-143) ──────────────────


@router.get("/tenants/{tenant_id}/audit")
def get_tenant_audit(
    tenant_id: UUID,
    include_views: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    """Lifecycle events and Console actions for one tenant, newest first.
    Read-only; the read itself is audited (written first, in the data layer)."""
    result = admin_data_access.tenant_audit(
        platform_admin_user_id=_admin_id(identity),
        tenant_id=tenant_id,
        include_views=include_views,
        limit=limit,
        offset=offset,
    )
    if result is None:
        raise HTTPException(status_code=404)
    return result


# ── Approved-example prompting (slice 5.10; Section 7.13, D-141) ────────────


class ExamplePromptingRequest(BaseModel):
    enabled: bool
    # 7.13: switched on "after a live golden run passes with the feature
    # enabled" -- the founder confirms that they ran it (EXM-001 otherwise).
    golden_run_confirmed: bool = False


@router.get("/tenants/{tenant_id}/example-prompting")
def get_example_prompting(
    tenant_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    _console_act(identity, tenant_id, "read", target_type="example_prompting")
    with tenant_session(tenant_id) as session:
        return example_prompting.overview(session, tenant_id)


@router.put("/tenants/{tenant_id}/example-prompting")
def put_example_prompting(
    tenant_id: UUID,
    body: ExamplePromptingRequest,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    admin_id = _console_act(
        identity,
        tenant_id,
        "example_prompting_set",
        target_type="tenant",
        target_id=tenant_id,
        payload={"enabled": body.enabled, "golden_run_confirmed": body.golden_run_confirmed},
    )
    with tenant_session(tenant_id) as session:
        try:
            return example_prompting.set_enabled(
                session,
                tenant_id,
                enabled=body.enabled,
                golden_run_confirmed=body.golden_run_confirmed,
                actor_user_id=admin_id,
            )
        except example_prompting.ExamplePromptingError as exc:
            raise catalog_error(exc.code, status_code=422) from exc


# ── Dashboard (Section 7.15.3; slice 5.5, D-121) ────────────────────────────


def _queue_depths() -> dict[str, int | None]:
    """Queue depth by priority, straight from the broker. A broker that is
    down is a number we don't have, not a page that fails."""
    try:
        import redis

        client = redis.Redis.from_url(get_settings().redis_url, socket_timeout=1)
        return {name: int(cast(int, client.llen(name))) for name in ("interactive", "bulk")}
    except Exception:  # noqa: BLE001 -- the strip degrades, the page does not
        return {"interactive": None, "bulk": None}


def _worker_heartbeat() -> str | None:
    """The last time a worker answered. None means "no worker answered in a
    second", which is what the strip shows."""
    try:
        replies = celery_client.control.ping(timeout=1)
    except Exception:  # noqa: BLE001 -- same reason as above
        return None
    return ", ".join(sorted(name for reply in replies or [] for name in reply)) or None


@router.get("/dashboard")
def dashboard(
    days: int = 30, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    """
    The Console home (7.15.3): health strip, KPI cards from the nightly
    rollup, and when that rollup last ran. One admin_actions row.
    """
    data = admin_data_access.dashboard(platform_admin_user_id=_admin_id(identity), days=days)
    return {
        **_jsonable(data),
        "queues": _queue_depths(),
        "worker": _worker_heartbeat(),
        "rollup_stale_hours": ROLLUP_STALE_HOURS,
        "rollup_is_stale": metrics.is_stale(data["rollup"], hours=ROLLUP_STALE_HOURS),
    }


@router.get("/tenants/{tenant_id}/metrics")
def tenant_metrics(
    tenant_id: UUID, days: int = 30, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    return _jsonable(
        admin_data_access.tenant_metrics(
            platform_admin_user_id=_admin_id(identity), tenant_id=tenant_id, days=days
        )
    )


class RecomputeRequest(BaseModel):
    # How many days back to recompute. The dashboard's button sends 2; a
    # backfill after a quiet night can ask for more.
    days: int = Field(default=2, ge=1, le=120)


@router.post("/rollup/recompute", status_code=202)
def recompute_rollup(
    body: RecomputeRequest, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    """The "recompute" the section asks for. Queued, not run in the request:
    it is the same job the nightly beat runs."""
    admin_id = _admin_id(identity)
    admin_data_access.record_platform_action(
        platform_admin_user_id=admin_id,
        action="rollup_recompute",
        target_type="rollup",
        payload={"days": body.days},
    )
    celery_client.send_task(
        "docflow.run_daily_rollup", args=[body.days, "manual"], queue="interactive"
    )
    return {"queued": True, "days": body.days}


# ── Allowances, quarantine, intake address (slice 5.7, D-126) ───────────────
#
# The Console's side of Section 7.16. Each request is one `admin_actions` row
# (written by _console_act, 404 for a tenant that doesn't exist), then the work
# runs in that tenant's own session with the founder as the actor and
# `acting_as_tenant_id` on what it writes (Section 7.15.1). The founder may
# release any hold, including one whose ceiling is still tripped: an explicit
# human act, logged like every other.


def _quarantine_error(exc: quarantine.QuarantineError) -> HTTPException:
    status = {"QUA-003": 422, "QUA-005": 422}.get(exc.code, 409)
    return catalog_error(exc.code, status_code=status, extra=exc.detail or None)


@router.get("/tenants/{tenant_id}/quarantine")
def get_quarantine(
    tenant_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    _console_act(identity, tenant_id, "quarantine_read", target_type="tenant", target_id=tenant_id)
    with tenant_session(tenant_id) as session:
        groups = quarantine.held_summary(session, tenant_id, role=None)
        rows = quarantine.list_held(session, tenant_id)
        a = usage.allowance_for(session, tenant_id)
        sender = intake_admin.get_sender_settings(session, tenant_id)
        expired = quarantine.expired_count(session, tenant_id)
        spend, tokens = usage.daily_ai_spend(session, tenant_id)
    ceiling = a.allowance * ABUSE_CEILING_MULTIPLIER if a.allowance else None
    return {
        "usage": {"used": a.used, "allowance": a.allowance, "tier": a.tier_name, "month": a.month},
        # Where the two ceilings stand NOW, so a hold made earlier can be judged:
        # a hold whose ceiling is no longer reached is safe to release.
        "limits": {
            "monthly_ceiling": ceiling,
            "monthly_ceiling_reached": ceiling is not None and a.used >= ceiling,
            "daily_cost_ceiling_usd": str(DAILY_AI_COST_CEILING_USD),
            "spend_today_usd": str(spend),
            "daily_cost_ceiling_reached": spend >= DAILY_AI_COST_CEILING_USD
            or tokens >= DAILY_TOKEN_CEILING,
        },
        "sender_settings": sender,
        "expired_held": expired,
        "groups": [
            {
                "reason": g.reason,
                "label": quarantine.reason_label(g.reason),
                "count": g.count,
                "title": g.entry.title,
                "message": g.entry.message,
            }
            for g in groups
        ],
        "documents": [
            {**_jsonable(dict(r)), "reason_label": quarantine.reason_label(r["quarantine_reason"])}
            for r in rows
        ],
    }


class QuarantineIds(BaseModel):
    document_ids: list[UUID] = Field(min_length=1, max_length=500)


@router.post("/tenants/{tenant_id}/quarantine/release")
def release_quarantine(
    tenant_id: UUID,
    body: QuarantineIds,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    admin_id = _console_act(
        identity, tenant_id, "quarantine_release", target_type="tenant", target_id=tenant_id,
        payload={"count": len(body.document_ids)},
    )
    with tenant_session(tenant_id) as session:
        try:
            result = quarantine.release(
                session, tenant_id, body.document_ids, role=None,
                actor_user_id=admin_id, acting_as_tenant_id=tenant_id,
            )
        except quarantine.QuarantineError as exc:
            raise _quarantine_error(exc) from exc
    # Oldest first: the order they were received.
    queue = "interactive" if len(result.released) <= 10 else "bulk"
    for document_id in result.released:
        celery_client.send_task(
            "docflow.parse_and_extract", args=[str(tenant_id), str(document_id)], queue=queue
        )
    return {"released": [str(i) for i in result.released], "skipped": [str(i) for i in result.skipped]}


class QuarantineClear(BaseModel):
    document_ids: list[UUID] = Field(min_length=1, max_length=500)
    confirm_name: str


@router.post("/tenants/{tenant_id}/quarantine/clear")
def clear_quarantine(
    tenant_id: UUID,
    body: QuarantineClear,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    _console_act(
        identity, tenant_id, "quarantine_clear", target_type="tenant", target_id=tenant_id,
        payload={"count": len(body.document_ids)},
    )
    with tenant_session(tenant_id) as session:
        try:
            cleared = quarantine.clear(session, tenant_id, body.document_ids, confirm_name=body.confirm_name)
        except quarantine.QuarantineError as exc:
            raise _quarantine_error(exc) from exc
    return {"cleared": [str(i) for i in cleared]}


@router.post("/tenants/{tenant_id}/intake-address/rotate")
def rotate_intake_address(
    tenant_id: UUID, identity: AuthenticatedIdentity = Depends(require_platform_admin)
) -> dict:
    admin_id = _console_act(
        identity, tenant_id, "intake_address_rotate", target_type="tenant", target_id=tenant_id
    )
    with tenant_session(tenant_id) as session:
        try:
            result = intake_admin.rotate_address(session, tenant_id, actor_user_id=admin_id)
        except quarantine.QuarantineError as exc:
            raise _quarantine_error(exc) from exc
    return {"address": result["address"], "grace_ends_at": result["grace_ends_at"].isoformat()}


class SenderSettings(BaseModel):
    strict_sender_mode: bool
    sender_allowlist: list[str] = Field(default_factory=list, max_length=200)


@router.put("/tenants/{tenant_id}/sender-settings")
def put_sender_settings(
    tenant_id: UUID,
    body: SenderSettings,
    identity: AuthenticatedIdentity = Depends(require_platform_admin),
) -> dict:
    admin_id = _console_act(
        identity, tenant_id, "sender_settings_update", target_type="tenant", target_id=tenant_id,
        payload={"strict_sender_mode": body.strict_sender_mode, "entries": len(body.sender_allowlist)},
    )
    with tenant_session(tenant_id) as session:
        try:
            return intake_admin.set_sender_allowlist(
                session, tenant_id, strict=body.strict_sender_mode,
                entries=body.sender_allowlist, actor_user_id=admin_id,
            )
        except quarantine.QuarantineError as exc:
            raise _quarantine_error(exc) from exc
