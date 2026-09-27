# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
One tenant's catalog-import history is never another tenant's, at the API
(CLAUDE.md Section 7.5, 7.15.1; the `GET /admin/tenants/{id}/imports` endpoint).

**Why this file exists.** `apps/web/e2e/import-tenants.spec.ts` already asserts
this, in a browser -- but that suite stubs the API at the network boundary, so
it decides for itself what each tenant's import list contains. It can prove the
screen shows what it was handed and cannot prove the server hands over the right
thing. Tenant isolation is the one thing Section 7.5 says cannot ever fail, so
the claim needs a test that asks the real database through real RLS. Found by
the stubbed-auth audit of 2026-09-27: this was the only stubbed spec with no
API-level counterpart.

`list_imports` carries no `tenant_id` in its WHERE clause -- by design, since
Section 7.5 puts the scoping in one data-access layer -- so RLS is the whole of
what separates these two lists, and this file is the only thing that checks it.
Verified honestly: with `tenant_session(tenant_id)` swapped for `platform_session()`
in the router, the first two tests fail, the first one returning 23 imports
belonging to every test tenant on staging.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from tests.conftest import requires_console_schema
from tests.test_catalog_import_api import (  # noqa: F401 -- fixtures and helpers
    _capture_enqueue,
    _commit,
    _import,
    requires_imports_schema,
)
from tests.test_console_api import (  # noqa: F401 -- fixtures
    _Console,
    _environment,
    _scalar,
    stripe,
)

ALPHA_CATALOG = b"SKU,Description,UOM\nTEST-ALPHA-1,Test Alpha Widget,EA\n"
BETA_CATALOG = b"SKU,Description,UOM\nTEST-BETA-1,Test Beta Widget,EA\n"


def _history(client, console, tenant_id, kind="catalog"):
    response = client.get(
        f"/admin/tenants/{tenant_id}/imports", params={"kind": kind}, headers=console.headers()
    )
    assert response.status_code == 200, response.text
    return response.json()["imports"]


@requires_imports_schema
@requires_console_schema
def test_one_tenants_import_history_never_appears_under_another(client, stripe, _capture_enqueue):
    with _Console() as console:
        alpha = console.create_tenant(client).json()["tenant_id"]
        beta = console.create_tenant(client).json()["tenant_id"]

        alpha_import = _import(client, console, alpha, ALPHA_CATALOG, name="alpha-only-catalog.csv")
        assert _commit(client, console, alpha, alpha_import["id"]).status_code == 200

        # Alpha sees its own import, named as uploaded.
        alpha_history = _history(client, console, alpha)
        assert [r["original_filename"] for r in alpha_history] == ["alpha-only-catalog.csv"]

        # Beta has committed nothing, so its history is empty -- not Alpha's.
        assert _history(client, console, beta) == []

        # And once Beta has its own, the two lists stay disjoint.
        beta_import = _import(client, console, beta, BETA_CATALOG, name="beta-only-catalog.csv")
        assert _commit(client, console, beta, beta_import["id"]).status_code == 200

        beta_history = _history(client, console, beta)
        assert [r["original_filename"] for r in beta_history] == ["beta-only-catalog.csv"]
        assert {r["id"] for r in beta_history}.isdisjoint({r["id"] for r in alpha_history})

        # Alpha's list did not gain Beta's row either.
        assert [r["original_filename"] for r in _history(client, console, alpha)] == [
            "alpha-only-catalog.csv"
        ]


@requires_imports_schema
@requires_console_schema
def test_a_catalog_import_does_not_show_up_as_a_buyer_import(client, stripe, _capture_enqueue):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        committed = _import(client, console, tenant_id, ALPHA_CATALOG, name="alpha-only-catalog.csv")
        assert _commit(client, console, tenant_id, committed["id"]).status_code == 200

        assert len(_history(client, console, tenant_id, kind="catalog")) == 1
        assert _history(client, console, tenant_id, kind="buyers") == []


@requires_imports_schema
@requires_console_schema
def test_reading_a_tenants_import_history_is_an_audited_console_read(client, stripe, _capture_enqueue):
    """7.15.1: one `admin_actions` row per request through the admin layer."""
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]

        before = _scalar(
            "SELECT count(*) FROM admin_actions WHERE target_type = 'catalog_imports' "
            "AND target_tenant_id = :t",
            t=tenant_id,
        )
        _history(client, console, tenant_id)
        after = _scalar(
            "SELECT count(*) FROM admin_actions WHERE target_type = 'catalog_imports' "
            "AND target_tenant_id = :t",
            t=tenant_id,
        )
        assert after == before + 1
