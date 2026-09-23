"""
Who may reach each route on the tenant surface (CLAUDE.md Section 3:
"Permissions enforced at the API layer, never only in the UI"). Slice 5.8a,
D-128.

Four roles exist in the database and in Section 3, which locks them. Only two
are given out today (D-128): the tenant's first user, stored as `owner` and
shown as **Admin**, and `reviewer`. `admin` and `viewer` remain valid and
enforced here, so turning either on later is a change to a screen, not to the
permission model.

    member    any signed-in user of the tenant (read)
    reviewer  owner, admin, reviewer -- may change documents
    admin     owner, admin only -- the dashboard, and managing people

`ROUTE_ACCESS` is the inventory: every tenant-surface route appears in it, and
`tests/test_roles.py` fails the build when one does not. The table does not
grant anything by itself -- each route carries its own dependency -- but a route
that nobody has thought about cannot ship quietly.
"""

from __future__ import annotations

from typing import Literal

Access = Literal["member", "reviewer", "admin"]

ROLES_BY_ACCESS: dict[Access, frozenset[str]] = {
    "member": frozenset({"owner", "admin", "reviewer", "viewer"}),
    "reviewer": frozenset({"owner", "admin", "reviewer"}),
    "admin": frozenset({"owner", "admin"}),
}

# (method, path) -> the least role that may call it.
ROUTE_ACCESS: dict[tuple[str, str], Access] = {
    # Identity
    ("GET", "/auth/me"): "member",
    # The customer's own dashboard (5.8a)
    ("GET", "/home"): "admin",
    # The account's activity trail (5.8b). A read, but not a `member` one: the
    # people who do the work see what a colleague did; a viewer would not.
    ("GET", "/activity"): "reviewer",
    # Intake
    ("POST", "/documents/upload"): "reviewer",
    # Review
    ("GET", "/review/documents"): "member",
    ("GET", "/review/documents/{document_id}"): "member",
    ("PATCH", "/review/documents/{document_id}"): "reviewer",
    ("POST", "/review/documents/{document_id}/approve"): "reviewer",
    ("POST", "/review/documents/{document_id}/reject"): "reviewer",
    ("GET", "/review/documents/{document_id}/warnings/open"): "member",
    ("POST", "/review/documents/{document_id}/mapping"): "reviewer",
    ("GET", "/review/items"): "member",
    ("GET", "/review/documents/{document_id}/original"): "member",
    ("GET", "/review/documents/{document_id}/original/content"): "member",
    # Export
    ("POST", "/review/documents/{document_id}/exports"): "member",
    ("GET", "/review/documents/{document_id}/exports"): "member",
    ("GET", "/review/exports/{export_id}"): "member",
    ("GET", "/review/exports/{export_id}/download"): "member",
    # Allowances and held documents (5.7)
    ("GET", "/allowance"): "member",
    ("GET", "/held"): "member",
    ("POST", "/held/release"): "reviewer",
    ("GET", "/ignored-mail"): "member",
}
