"""
The Team page's API (slice 5.8d; DECISIONS.md D-132): the account's admin lists
the people on the account, invites a reviewer, resends an invite and removes
someone. Section 3: "every subsequent user is invited by a tenant owner/admin".

Admin-only (`require_tenant_admin`); a reviewer is refused with AUTH-003, as on
the dashboard. The tenant comes from the session, never the request (Section
7.5), and a user id in the path is looked up inside that tenant's own session,
so another account's user is simply not found (TEAM-008). Every refusal is a
catalog entry (7.16.5); the rules themselves live in `docflow_core.team`.
"""

from __future__ import annotations

from uuid import UUID

from docflow_core import team
from docflow_core.db import tenant_session
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.deps import AuthenticatedIdentity, get_current_identity, require_tenant_admin
from app.errors import catalog_error

router = APIRouter(prefix="/team", tags=["team"])

# How each refusal answers over HTTP.
_STATUS = {
    "TEAM-001": 422,
    "TEAM-002": 409,
    "TEAM-003": 409,
    "TEAM-004": 503,
    "TEAM-005": 409,
    "TEAM-006": 403,
    "TEAM-007": 403,
    "TEAM-008": 404,
}


class InviteBody(BaseModel):
    email: str


def _admin(identity: AuthenticatedIdentity) -> tuple[UUID, UUID]:
    tenant_id = require_tenant_admin(identity)
    assert identity.local_user_id is not None  # a tenant member always has a row
    return tenant_id, identity.local_user_id


@router.get("")
def list_team(identity: AuthenticatedIdentity = Depends(get_current_identity)) -> dict:
    tenant_id, me = _admin(identity)
    with tenant_session(tenant_id) as session:
        people = team.members(session, tenant_id, me=me)
    return {"members": [m.as_dict() for m in people]}


@router.post("/invite")
def invite(body: InviteBody, identity: AuthenticatedIdentity = Depends(get_current_identity)) -> dict:
    tenant_id, me = _admin(identity)
    try:
        with tenant_session(tenant_id) as session:
            return team.invite(session, tenant_id, actor=me, raw_email=body.email)
    except team.TeamError as exc:
        raise catalog_error(exc.code, status_code=_STATUS[exc.code]) from None


@router.post("/{user_id}/resend")
def resend(user_id: UUID, identity: AuthenticatedIdentity = Depends(get_current_identity)) -> dict:
    tenant_id, me = _admin(identity)
    try:
        with tenant_session(tenant_id) as session:
            return team.resend(session, tenant_id, actor=me, user_id=user_id)
    except team.TeamError as exc:
        raise catalog_error(exc.code, status_code=_STATUS[exc.code]) from None


@router.post("/{user_id}/remove")
def remove(user_id: UUID, identity: AuthenticatedIdentity = Depends(get_current_identity)) -> dict:
    tenant_id, me = _admin(identity)
    try:
        with tenant_session(tenant_id) as session:
            return team.remove(session, tenant_id, actor=me, user_id=user_id)
    except team.TeamError as exc:
        raise catalog_error(exc.code, status_code=_STATUS[exc.code]) from None
