# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
The founder acting in a tenant from the Console (CLAUDE.md Section 7.15.1,
"Acting-as, not impersonation"; D-111), against the real database and RLS.

The review and export routes are mounted a second time under
/admin/tenants/{id}/act behind the admin gate. What must hold:

  * nobody but a platform admin can reach them -- 404, like every /admin route;
  * a platform admin reading a tenant's document gets it, and an
    `admin_actions` row exists for that read (a Section 7.15.1 required test);
  * a Console review action lands in the tenant's own audit trail, attributed
    to the founder with acting_as_tenant_id set, labelled DocFlow support
    (a Section 7.15.1 required test);
  * the acting-as path is still tenant-scoped: acting in tenant A never
    reaches tenant B's document;
  * `current_actor` fails closed if the gate is ever missing.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from docflow_core.db import platform_session
from fastapi import HTTPException
from sqlalchemy import text

from app.actor import Actor, current_actor
from tests.conftest import requires_console_schema, requires_review_schema
from tests.test_console_api import _Console, _environment  # noqa: F401 -- fixtures
from tests.test_review_api import CLEAN_HEADER, CLEAN_LINES, _ReviewTenant, _secrets  # noqa: F401


def _act(tenant_id) -> str:
    return f"/admin/tenants/{tenant_id}/act/review"


def _forget_admin_actions(tenant_id) -> None:
    # admin_actions point at the tenant; the tenant fixture deletes the
    # tenant before the Console fixture deletes its own actions.
    with platform_session() as session:
        session.execute(
            text("DELETE FROM admin_actions WHERE target_tenant_id = :t"), {"t": str(tenant_id)}
        )


def _admin_actions(tenant_id, action: str) -> list[dict]:
    with platform_session() as session:
        return [
            dict(r)
            for r in session.execute(
                text(
                    "SELECT action, target_type, target_id, payload FROM admin_actions "
                    "WHERE target_tenant_id = :t AND action = :a ORDER BY created_at"
                ),
                {"t": str(tenant_id), "a": action},
            ).mappings()
        ]


@requires_console_schema
@requires_review_schema
def test_the_acting_as_routes_are_404_to_anyone_but_a_platform_admin(client):
    with _ReviewTenant("Acme Test Distributor -- acting 404") as tenant:
        document = tenant.create_document(header=_header(), lines=CLEAN_LINES)
        for path in (f"{_act(tenant.tenant_id)}/documents", f"{_act(tenant.tenant_id)}/documents/{document}"):
            assert client.get(path).status_code == 404
            # The tenant's own owner, asking for their own tenant: still 404.
            assert client.get(path, headers=tenant.headers()).status_code == 404
        assert client.post(
            f"{_act(tenant.tenant_id)}/documents/{document}/approve",
            headers=tenant.headers(),
            json={"acknowledgements": []},
        ).status_code == 404


@requires_console_schema
@requires_review_schema
def test_a_platform_admin_reads_a_tenants_document_and_the_read_is_audited(client):
    with _Console() as console, _ReviewTenant("Acme Test Distributor -- acting read") as tenant:
        try:
            document = tenant.create_document(header=_header(), lines=CLEAN_LINES)

            queue = client.get(f"{_act(tenant.tenant_id)}/documents", headers=console.headers())
            assert queue.status_code == 200
            assert [d["id"] for d in queue.json()["documents"]] == [str(document)]

            detail = client.get(f"{_act(tenant.tenant_id)}/documents/{document}", headers=console.headers())
            assert detail.status_code == 200
            assert detail.json()["header"]["po_number"] == CLEAN_HEADER["po_number"]
            assert detail.json()["can_edit"] is True

            reads = _admin_actions(tenant.tenant_id, "acting_as_read")
            assert len(reads) == 2  # one row per request, not per row returned
            assert reads[1]["target_type"] == "document" and str(reads[1]["target_id"]) == str(document)
            assert reads[1]["payload"]["route"].endswith("/review/documents/{document_id}")
        finally:
            _forget_admin_actions(tenant.tenant_id)


@requires_console_schema
@requires_review_schema
def test_a_console_edit_is_in_the_tenants_trail_as_docflow_support(client):
    with _Console() as console, _ReviewTenant("Acme Test Distributor -- acting edit") as tenant:
        try:
            document = tenant.create_document(header=_header(), lines=CLEAN_LINES)
            base = f"{_act(tenant.tenant_id)}/documents/{document}"
            before = client.get(base, headers=console.headers()).json()

            edited = client.patch(
                base,
                headers=console.headers(),
                json={"header": {"po_number": "BCH-2299"}, "expected_version": before["version"]},
            )
            assert edited.status_code == 200, edited.text

            with platform_session() as session:
                row = session.execute(
                    text(
                        "SELECT user_id, acting_as_tenant_id FROM review_actions "
                        "WHERE document_id = :d AND action = 'edited'"
                    ),
                    {"d": str(document)},
                ).mappings().one()
            assert str(row["user_id"]) == str(console.user_id)  # the founder, as themselves
            assert str(row["acting_as_tenant_id"]) == str(tenant.tenant_id)

            # The tenant's own view of its trail labels it.
            trail = client.get(f"/review/documents/{document}", headers=tenant.headers()).json()["trail"]
            assert any(entry["by_docflow_support"] for entry in trail)
            assert _admin_actions(tenant.tenant_id, "acting_as_write")
        finally:
            _forget_admin_actions(tenant.tenant_id)


@requires_console_schema
@requires_review_schema
def test_acting_in_one_tenant_never_reaches_another_tenants_document(client):
    with (
        _Console() as console,
        _ReviewTenant("Acme Test Distributor -- acting A") as tenant_a,
        _ReviewTenant("Acme Test Distributor -- acting B") as tenant_b,
    ):
        try:
            b_document = tenant_b.create_document(header=_header(), lines=CLEAN_LINES)
            response = client.get(
                f"{_act(tenant_a.tenant_id)}/documents/{b_document}", headers=console.headers()
            )
            assert response.status_code == 404
        finally:
            _forget_admin_actions(tenant_a.tenant_id)
            _forget_admin_actions(tenant_b.tenant_id)


@requires_console_schema
def test_acting_in_a_tenant_that_does_not_exist_is_404(client):
    with _Console() as console:
        assert client.get(f"{_act(uuid4())}/documents", headers=console.headers()).status_code == 404


def test_current_actor_fails_closed_on_the_console_mount_without_the_gate():
    """If the mount ever lost its gate, the route must not fall through to
    the caller's own tenant session -- it 404s."""
    tenant_id = uuid4()
    request = SimpleNamespace(path_params={"acting_tenant_id": str(tenant_id)}, state=SimpleNamespace())
    with pytest.raises(HTTPException) as refused:
        current_actor(request, authorization=None)  # type: ignore[arg-type]
    assert refused.value.status_code == 404

    # An Actor for a different tenant than the path names is refused too.
    request.state.acting_actor = Actor(
        tenant_id=uuid4(), user_id=uuid4(), role=None, acting_as_tenant_id=uuid4()
    )
    with pytest.raises(HTTPException):
        current_actor(request, authorization=None)  # type: ignore[arg-type]

    # A non-support Actor on the Console mount is refused.
    request.state.acting_actor = Actor(tenant_id=tenant_id, user_id=uuid4(), role="owner")
    with pytest.raises(HTTPException):
        current_actor(request, authorization=None)  # type: ignore[arg-type]


def _header() -> dict:
    return dict(CLEAN_HEADER)
