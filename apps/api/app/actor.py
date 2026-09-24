"""
Who is acting, in which tenant -- the one thing every tenant route needs.

Two ways in, one code path (CLAUDE.md Section 7.15.1, "Acting-as, not
impersonation"; DECISIONS.md D-111):

* A tenant user. The tenant comes from their own session, never from the
  request (Section 7.5).
* The founder in the Console. The review and export routers are mounted a
  second time under `/admin/tenants/{acting_tenant_id}/act`, behind
  `app.routers.admin.acting_as_gate`. The gate returns 404 to anyone who is
  not a platform admin, writes the `admin_actions` row, and leaves the Actor
  on `request.state`. Every write then records the founder's own user id
  AND `acting_as_tenant_id`, so the tenant's audit trail shows
  "DocFlow support", never a tenant user.

This module never imports `admin_data_access`: the audited half lives in the
admin router (tests/test_admin_import_boundary.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import Header, HTTPException, Request

from app.deps import (
    REVIEWING_ROLES,
    AuthenticatedIdentity,
    _resolve_identity,
    require_tenant_member,
)

ACTING_TENANT_PARAM = "acting_tenant_id"


@dataclass(frozen=True)
class Actor:
    tenant_id: UUID
    user_id: UUID | None
    role: str | None
    # Set only on the Console path: the tenant the founder is acting in.
    acting_as_tenant_id: UUID | None = None

    @property
    def is_support(self) -> bool:
        return self.acting_as_tenant_id is not None

    @property
    def can_review(self) -> bool:
        return self.is_support or self.role in REVIEWING_ROLES

    def require_user_id(self) -> UUID:
        if self.user_id is None:
            # Section 7.3: no review action is ever anonymous.
            raise HTTPException(status_code=403, detail="This account cannot act on documents.")
        return self.user_id

    def require_reviewer(self) -> UUID:
        """The tenant, for a route that changes something. Enforced here, in
        the API, never only by hiding a button (Section 3)."""
        if not self.can_review:
            from app.errors import catalog_error

            raise catalog_error("AUTH-002", status_code=403, extra={"role": self.role})
        return self.tenant_id


def tenant_actor_from_identity(identity: AuthenticatedIdentity) -> Actor:
    # A platform-admin-only account (D-004) reaches a tenant through the
    # Console's acting-as mount, never through the tenant routes; a removed
    # person (D-132) is refused with AUTH-004. One check for every route.
    tenant_id = require_tenant_member(identity)
    return Actor(tenant_id=tenant_id, user_id=identity.local_user_id, role=identity.role)


def current_actor(request: Request, authorization: str | None = Header(default=None)) -> Actor:
    """
    The dependency every review/export route takes.

    Fails closed: on the Console mount (the path carries `acting_tenant_id`)
    it only ever accepts the Actor the admin gate put on the request, and
    404s otherwise -- so a mount without its gate can never fall through to
    a tenant session.
    """
    if ACTING_TENANT_PARAM in request.path_params:
        actor = getattr(request.state, "acting_actor", None)
        try:
            in_path = UUID(str(request.path_params[ACTING_TENANT_PARAM]))
        except ValueError:
            raise HTTPException(status_code=404) from None
        if not isinstance(actor, Actor) or actor.tenant_id != in_path or not actor.is_support:
            raise HTTPException(status_code=404)
        return actor
    identity = _resolve_identity(authorization)
    if identity is None:
        from app.errors import catalog_error

        raise catalog_error("AUTH-005", status_code=401)
    return tenant_actor_from_identity(identity)
