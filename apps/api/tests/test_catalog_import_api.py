# ruff: noqa: F811 -- pytest fixtures imported from test_console_api look like redefinitions.
"""
Catalog and customer-list import through the Console, against the real
database and RLS (CLAUDE.md Section 7.15.2 Steps 4-5; D-108; migration 0012).

The worker is not running: the upload's enqueue is captured, and
`catalog_import.run_parse` -- the whole of the worker task -- is called
directly, exactly as the export tests call `run_export`.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import io
from uuid import UUID, uuid4

import pytest
from docflow_core import catalog_import
from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import requires_console_schema
from tests.test_console_api import (  # noqa: F401 -- fixtures
    _Console,
    _environment,
    _scalar,
    stripe,
)

CATALOG_V1 = (
    b"SKU,Description,UOM\n"
    b"TEST-1001,Test Beans 5lb,CS\n"
    b"TEST-1002,Test Cups 12oz,BOX\n"
    b"TEST-1003,Test Syrup,EA\n"
)


def _imports_schema_available() -> bool:
    try:
        with platform_session() as session:
            session.execute(text("SELECT header_row_number, overrides FROM catalog_imports LIMIT 0"))
            session.execute(text("SELECT last_import_id, retired_by_import_id FROM items LIMIT 0"))
        return True
    except Exception:
        return False


requires_imports_schema = pytest.mark.skipif(
    not _imports_schema_available(),
    reason="supabase/migrations/0012_catalog_import.sql has not been applied yet -- see D-108.",
)


@pytest.fixture(autouse=True)
def _capture_enqueue(monkeypatch):
    sent: list[tuple[str, list]] = []

    class _FakeCelery:
        def send_task(self, name, args=None, queue=None):
            sent.append((name, args))

    monkeypatch.setattr("app.routers.admin.celery_client", _FakeCelery())
    return sent


def _upload(client, console, tenant_id, content: bytes, name="catalog.csv", kind="catalog"):
    return client.post(
        f"/admin/tenants/{tenant_id}/imports",
        headers=console.headers(),
        data={"kind": kind},
        files={"file": (name, content)},
    )


def _import(client, console, tenant_id, content: bytes, name="catalog.csv", kind="catalog") -> dict:
    response = _upload(client, console, tenant_id, content, name, kind)
    assert response.status_code == 202, response.text
    import_id = response.json()["import_id"]
    catalog_import.run_parse(UUID(tenant_id), UUID(import_id))
    return _preview(client, console, tenant_id, import_id)


def _preview(client, console, tenant_id, import_id) -> dict:
    response = client.get(f"/admin/tenants/{tenant_id}/imports/{import_id}", headers=console.headers())
    assert response.status_code == 200, response.text
    return response.json()["import"]


def _commit(client, console, tenant_id, import_id):
    return client.post(f"/admin/tenants/{tenant_id}/imports/{import_id}/commit", headers=console.headers())


def _items(tenant_id) -> dict[str, dict]:
    with platform_session() as session:
        rows = session.execute(
            text(
                "SELECT id, sku, description, unit_of_measure, deleted_at, last_import_id, "
                "retired_by_import_id FROM items WHERE tenant_id = :t"
            ),
            {"t": tenant_id},
        ).mappings().all()
    return {r["sku"]: dict(r) for r in rows}


# ── The first catalog ───────────────────────────────────────────────────────


@requires_imports_schema
@requires_console_schema
def test_a_first_catalog_is_mapped_checked_and_committed(client, stripe, _capture_enqueue):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]

        preview = _import(client, console, tenant_id, CATALOG_V1)

        assert _capture_enqueue[-1][0] == "docflow.parse_import"
        assert preview["status"] == "parsed"
        assert preview["mapping"]["sku"] == 0 and preview["mapping"]["description"] == 1
        assert preview["report"] == []
        assert preview["diff"] == {"insert": 3, "update": 0, "reinstate": 0, "unchanged": 0, "retire": 0}
        assert preview["can_commit"] is True

        committed = _commit(client, console, tenant_id, preview["id"])
        assert committed.status_code == 200, committed.text
        items = _items(tenant_id)
        assert set(items) == {"TEST-1001", "TEST-1002", "TEST-1003"}
        assert all(str(i["last_import_id"]) == preview["id"] for i in items.values())
        assert items["TEST-1002"]["unit_of_measure"] == "BOX"

        overview = console.overview(client, tenant_id)
        assert overview["onboarding_status"] == "catalog_loaded"
        # Acting-as, recorded on the import itself (Section 7.15.1).
        assert str(_scalar(
            "SELECT acting_as_tenant_id FROM catalog_imports WHERE id = :i", i=preview["id"]
        )) == tenant_id
        assert _scalar(
            "SELECT count(*) FROM admin_actions WHERE action = 'import_commit' AND target_tenant_id = :t",
            t=tenant_id,
        ) == 1


@requires_imports_schema
@requires_console_schema
def test_an_unmapped_unit_is_left_empty_not_defaulted(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        preview = _import(client, console, tenant_id, b"SKU,Description\nTEST-1,Test Beans\n")
        assert _commit(client, console, tenant_id, preview["id"]).status_code == 200
        # The items column defaults to 'EA'; the file never said so.
        assert _items(tenant_id)["TEST-1"]["unit_of_measure"] is None


# ── Re-uploads are diffs ────────────────────────────────────────────────────


@requires_imports_schema
@requires_console_schema
def test_a_reupload_updates_adds_and_retires_but_never_deletes(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        first = _import(client, console, tenant_id, CATALOG_V1)
        _commit(client, console, tenant_id, first["id"])
        original_ids = {sku: i["id"] for sku, i in _items(tenant_id).items()}

        v2 = (
            b"SKU,Description,UOM\nTEST-1001,Test Beans 5lb,CS\n"
            b"TEST-1002,Test Cups 16oz,BOX\nTEST-1004,Test Lids,BOX\n"
        )
        second = _import(client, console, tenant_id, v2)
        assert second["diff"] == {"insert": 1, "update": 1, "reinstate": 0, "unchanged": 1, "retire": 1}
        assert second["retiring"] == ["TEST-1003"]
        assert _commit(client, console, tenant_id, second["id"]).status_code == 200

        items = _items(tenant_id)
        assert items["TEST-1002"]["description"] == "Test Cups 16oz"
        assert items["TEST-1003"]["deleted_at"] is not None  # retired, still there
        assert str(items["TEST-1003"]["retired_by_import_id"]) == second["id"]
        assert items["TEST-1003"]["id"] == original_ids["TEST-1003"]

        # A later file that has it again brings back the SAME item.
        third = _import(client, console, tenant_id, CATALOG_V1)
        assert third["diff"]["reinstate"] == 1
        _commit(client, console, tenant_id, third["id"])
        items = _items(tenant_id)
        assert items["TEST-1003"]["deleted_at"] is None
        assert items["TEST-1003"]["id"] == original_ids["TEST-1003"]


@requires_imports_schema
@requires_console_schema
def test_a_reupload_without_a_column_keeps_that_field_instead_of_blanking_it(client, stripe):
    """D-109: the founder's second file had no UPC column, and the preview
    counted every item as 'updated' -- committing would have wiped every
    barcode the first file set."""
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        first = _import(
            client, console, tenant_id,
            b"SKU,Description,UOM,UPC\nTEST-1001,Test Beans 5lb,CS,000000100017\n"
            b"TEST-1002,Test Cups 12oz,BOX,000000100024\n",
        )
        assert _commit(client, console, tenant_id, first["id"]).status_code == 200

        second = _import(
            client, console, tenant_id,
            b"Item #,Product Name,U/M\nTEST-1001,Test Beans 5lb,CS\nTEST-1002,Test Cups 12oz,EA\n",
            name="reexport.csv",
        )
        assert second["mapping"]["barcode"] is None
        assert second["diff"]["unchanged"] == 1 and second["diff"]["update"] == 1
        assert _commit(client, console, tenant_id, second["id"]).status_code == 200

        with platform_session() as session:
            items = {
                sku: (barcode, uom)
                for sku, barcode, uom in session.execute(
                    text("SELECT sku, barcode, unit_of_measure FROM items WHERE tenant_id = :t"),
                    {"t": tenant_id},
                ).all()
            }
        assert items["TEST-1001"] == ("000000100017", "CS")
        assert items["TEST-1002"] == ("000000100024", "EA")  # the unit updated, the barcode kept


@requires_imports_schema
@requires_console_schema
def test_retiring_a_sku_a_learned_rule_uses_is_flagged_before_commit(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        first = _import(client, console, tenant_id, CATALOG_V1)
        _commit(client, console, tenant_id, first["id"])
        item_id = _items(tenant_id)["TEST-1003"]["id"]
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO learned_rules (tenant_id, rule_type, match_key, match_value, status) "
                    "VALUES (:t, 'sku_mapping', 'vanilla syrup', CAST(:v AS jsonb), 'active')"
                ),
                {"t": tenant_id, "v": f'{{"item_id": "{item_id}"}}'},
            )

        without_1003 = b"SKU,Description\nTEST-1001,Test Beans 5lb\nTEST-1002,Test Cups 12oz\n"
        preview = _import(client, console, tenant_id, without_1003)
        flag = next(f for f in preview["report"] if f["code"] == "CAT-007")
        assert flag["keys"] == ["TEST-1003"] and flag["severity"] == "warning"
        assert flag["title"]  # catalog wording attached for the screen


# ── Blockers, fixes, mapping ────────────────────────────────────────────────


@requires_imports_schema
@requires_console_schema
def test_blockers_stop_the_commit_until_fixed_inline(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        messy = b"SKU,Description\nTEST-1,Test Beans\n,Mystery item\nTEST-1,Beans again\n"
        preview = _import(client, console, tenant_id, messy)
        codes = {f["code"]: f for f in preview["report"]}
        assert codes["CAT-001"]["rows"] == [3]
        assert codes["CAT-002"]["rows"] == [2, 4]
        assert preview["can_commit"] is False

        refused = _commit(client, console, tenant_id, preview["id"])
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "IMP-005"
        assert _items(tenant_id) == {}

        base = f"/admin/tenants/{tenant_id}/imports/{preview['id']}/rows"
        client.put(f"{base}/3", headers=console.headers(), json={"field": "sku", "value": "TEST-2"})
        client.put(f"{base}/4", headers=console.headers(), json={"field": "sku", "value": "TEST-3"})
        fixed = _preview(client, console, tenant_id, preview["id"])
        assert fixed["can_commit"] is True
        assert _commit(client, console, tenant_id, preview["id"]).status_code == 200
        assert set(_items(tenant_id)) == {"TEST-1", "TEST-2", "TEST-3"}


@requires_imports_schema
@requires_console_schema
def test_a_fixed_row_stays_in_view_editable_and_can_be_undone(client, stripe):
    """Founder feedback: after a fix, the row stopped being a problem, so it
    turned into plain text -- or, deep in a long file, left the table."""
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        lines = [f"TEST-{i},Test Item {i}" for i in range(1, 30)] + [",Test Mystery"]
        preview = _import(client, console, tenant_id, ("SKU,Description\n" + "\n".join(lines)).encode())
        assert {f["code"]: f["rows"] for f in preview["report"]}["CAT-001"] == [31]

        row = f"/admin/tenants/{tenant_id}/imports/{preview['id']}/rows/31"
        client.put(row, headers=console.headers(), json={"field": "sku", "value": "TEST-99"})
        fixed = _preview(client, console, tenant_id, preview["id"])
        assert fixed["can_commit"] is True
        shown = {r["row_number"]: r for r in fixed["preview"]}
        assert shown[31]["values"]["sku"] == "TEST-99"
        assert shown[31]["fixed"] == {"sku": ""}  # what the file says

        # Changed again before commit.
        client.put(row, headers=console.headers(), json={"field": "sku", "value": "TEST-98"})
        again = {r["row_number"]: r for r in _preview(client, console, tenant_id, preview["id"])["preview"]}
        assert again[31]["values"]["sku"] == "TEST-98"

        # Undo: back to exactly what the file says removes the fix.
        client.put(row, headers=console.headers(), json={"field": "sku", "value": ""})
        undone = _preview(client, console, tenant_id, preview["id"])
        assert undone["overrides"] == {}
        assert undone["can_commit"] is False
        assert {r["row_number"]: r for r in undone["preview"]}[31]["fixed"] == {}


@requires_imports_schema
@requires_console_schema
def test_an_unusual_file_is_mapped_by_hand_and_remembered_next_time(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        odd = b"Stock Ref,Wording\nTEST-1,Test Beans\n"
        preview = _import(client, console, tenant_id, odd)
        assert preview["missing_required"] == ["sku", "description"]
        assert _commit(client, console, tenant_id, preview["id"]).json()["detail"]["code"] == "IMP-007"

        response = client.put(
            f"/admin/tenants/{tenant_id}/imports/{preview['id']}/mapping",
            headers=console.headers(),
            json={"mapping": {"sku": 0, "description": 1}},
        )
        assert response.status_code == 200
        assert _commit(client, console, tenant_id, preview["id"]).status_code == 200

        # Same headers, reordered: the template maps it without help.
        again = _import(client, console, tenant_id, b"Wording,Stock Ref\nTest Beans,TEST-1\n")
        assert again["mapping"]["sku"] == 1 and again["mapping"]["description"] == 0
        assert again["diff"]["unchanged"] == 1


@requires_imports_schema
@requires_console_schema
def test_excel_skus_that_look_like_numbers_stay_as_written(client, stripe):
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.append(["SKU", "Description"])
    workbook.active.append([1002, "Test Beans"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        preview = _import(client, console, tenant_id, buffer.getvalue(), name="catalog.xlsx")
        assert preview["preview"][0]["values"]["sku"] == "1002"


# ── Files that cannot be imported ───────────────────────────────────────────


@requires_imports_schema
@requires_console_schema
def test_a_non_table_file_is_refused_before_anything_is_stored(client, stripe):
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        response = _upload(client, console, tenant_id, pdf, name="catalog.pdf")
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "IMP-001"
        assert _scalar("SELECT count(*) FROM catalog_imports WHERE tenant_id = :t", t=tenant_id) == 0


@requires_imports_schema
@requires_console_schema
def test_a_header_only_file_fails_with_its_reason_shown(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        preview = _import(client, console, tenant_id, b"SKU,Description\n")
        assert preview["status"] == "failed"
        assert preview["error"]["code"] == "IMP-002"
        assert preview["error"]["action"]


# ── From the intake, and customer lists ─────────────────────────────────────


@requires_imports_schema
@requires_console_schema
def test_a_catalog_can_come_straight_from_the_tenants_intake(client, stripe):
    with _Console() as console:
        intake_id = console.intake(client)
        console.upload(client, intake_id, "acme-test-catalog.csv", CATALOG_V1)
        tenant_id = console.create_tenant(client, intake_id=intake_id).json()["tenant_id"]

        listing = client.get(f"/admin/tenants/{tenant_id}/intake-files", headers=console.headers())
        files = listing.json()["files"]
        assert [f["original_filename"] for f in files] == ["acme-test-catalog.csv"]
        assert "storage_path" not in files[0]

        response = client.post(
            f"/admin/tenants/{tenant_id}/imports/from-intake",
            headers=console.headers(),
            json={"kind": "catalog", "intake_file_id": files[0]["id"]},
        )
        assert response.status_code == 202, response.text
        import_id = response.json()["import_id"]
        catalog_import.run_parse(UUID(tenant_id), UUID(import_id))
        assert _preview(client, console, tenant_id, import_id)["diff"]["insert"] == 3


@requires_imports_schema
@requires_console_schema
def test_a_customer_list_adds_and_updates_but_only_flags_near_duplicates(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO buyers (tenant_id, name, normalized_name) "
                    "VALUES (:t, 'Acme Test Cafe', 'acme test cafe'), "
                    "(:t, 'Harbor Test Foods', 'harbor test foods')"
                ),
                {"t": tenant_id},
            )
        customers = (
            b"Customer,Account,Email\n"
            b"Acme Test Cafe,A-100,orders@example.com\n"
            b"Acme Test Caffe,A-200,\n"
            b"Beacon Test Bistro,A-300,\n"
        )
        preview = _import(client, console, tenant_id, customers, name="customers.csv", kind="buyers")
        assert preview["diff"]["update"] == 1 and preview["diff"]["insert"] == 2
        assert preview["diff"]["retire"] == 0
        near = next(f for f in preview["report"] if f["code"] == "BUY-003")
        assert near["rows"] == [3]
        assert _commit(client, console, tenant_id, preview["id"]).status_code == 200

        with platform_session() as session:
            buyers = {
                r["name"]: r
                for r in session.execute(
                    text(
                        "SELECT id, name, external_account_number, deleted_at "
                        "FROM buyers WHERE tenant_id = :t"
                    ),
                    {"t": tenant_id},
                ).mappings()
            }
            candidates = session.execute(
                text("SELECT status FROM buyer_merge_candidates WHERE tenant_id = :t"), {"t": tenant_id}
            ).scalars().all()
        assert buyers["Acme Test Cafe"]["external_account_number"] == "A-100"
        assert "Acme Test Caffe" in buyers  # created, not merged
        assert buyers["Harbor Test Foods"]["deleted_at"] is None  # never retired
        assert candidates == ["open"]


# ── Section 7.15.1 / 7.5 ────────────────────────────────────────────────────


@requires_imports_schema
@requires_console_schema
def test_an_import_is_reachable_only_through_its_own_tenant(client, stripe):
    from tests.test_review_api import _ReviewTenant

    with _Console() as console:
        a = console.create_tenant(client, name="Acme Test A").json()["tenant_id"]
        b = console.create_tenant(client, name="Beacon Test B").json()["tenant_id"]
        preview = _import(client, console, a, CATALOG_V1)
        # A's import id under B's URL: B's tenant session cannot see it.
        wrong = client.get(f"/admin/tenants/{b}/imports/{preview['id']}", headers=console.headers())
        assert wrong.status_code == 404
        # A tenant user never reaches the import routes at all.
        with _ReviewTenant("Acme Test Distributor -- not an admin") as tenant:
            response = client.get(f"/admin/tenants/{a}/imports?kind=catalog", headers=tenant.headers())
            assert response.status_code == 404
        # And a nonexistent tenant is 404, not an empty list.
        assert client.get(
            f"/admin/tenants/{uuid4()}/imports?kind=catalog", headers=console.headers()
        ).status_code == 404
