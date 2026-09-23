# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
/auth/me carries the signed-in person's own company name, for the customer's
header. It is read from the session's own tenant, so it can only ever be the
caller's company -- never another tenant's, and never taken from the request
(CLAUDE.md Section 7.5). A founder with no tenant gets none.
"""

from __future__ import annotations

from tests.conftest import requires_database
from tests.test_console_api import _Console, _environment  # noqa: F401
from tests.test_quarantine_api import _Tenant


@requires_database
def test_me_names_the_callers_own_company_and_no_other(client):
    with _Tenant("Acme Test Distributor A") as a, _Tenant("Beacon Test Supply B") as b:
        mine = client.get("/auth/me", headers=a.headers()).json()
        theirs = client.get("/auth/me", headers=b.headers()).json()
        assert mine["tenant_name"] == "Acme Test Distributor A"
        assert theirs["tenant_name"] == "Beacon Test Supply B"
        # Nothing the client sends can point it at someone else's company.
        spoofed = client.get(
            f"/auth/me?tenant_id={b.tenant_id}", headers={**a.headers(), "X-Tenant-Id": str(b.tenant_id)}
        ).json()
        assert spoofed["tenant_name"] == "Acme Test Distributor A"


@requires_database
def test_me_gives_a_founder_with_no_tenant_no_company_name(client):
    with _Console() as console:
        me = client.get("/auth/me", headers=console.headers()).json()
        assert me["tenant_name"] is None and me["is_platform_admin"] is True


@requires_database
def test_me_tidies_stray_spaces_in_the_name(client):
    with _Tenant(" Acme Test Padded  ") as t:
        assert client.get("/auth/me", headers=t.headers()).json()["tenant_name"] == "Acme Test Padded"
