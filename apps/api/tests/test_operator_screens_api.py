# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
Slice 5.4's operator screens through the Console (D-119), against the real
database and RLS (migration 0015).

Buyer merge (Section 7.6, 7.13):
  * a flagged pair is listed; merging moves the documents and rules, fills
    blanks without overwriting, soft-deletes the merged buyer, resolves the
    candidate, leaves a buyer_alias rule and a buyer_merges row -- all
    attributed to the founder acting-as, with an admin_actions row;
  * the next order under the merged name links to the kept buyer through
    that rule, with the rule recorded as provenance, and creates nothing;
  * either side can be kept; a rule both buyers have stops the merge and
    changes nothing (BUY-009); a resolved pair can't be merged (BUY-008);
  * dismissing leaves both buyers as they are.

Learned rules (Section 7.13):
  * every rule is listed with who confirmed it (DocFlow support for the
    founder); disable stops it firing, enable restores it, delete removes it
    from the list but keeps the row; a proposal can't be switched on.

Both: 404 to anyone but a platform admin; a tenant's id in another tenant's
path is 404 (Section 7.5).

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from docflow_core.buyers import identify_and_link_buyer
from docflow_core.db import platform_session, tenant_session
from docflow_core.matching import load_sku_rules
from sqlalchemy import text

from tests.conftest import requires_console_schema
from tests.test_buyers import _TestBuyerTenant
from tests.test_console_api import _Console, _environment  # noqa: F401 -- fixtures


def _merge_schema_available() -> bool:
    try:
        with platform_session() as session:
            session.execute(text("SELECT id FROM buyer_merges LIMIT 0"))
        return True
    except Exception:
        return False


requires_buyer_merge_schema = pytest.mark.skipif(
    not _merge_schema_available(),
    reason="supabase/migrations/0015_buyer_merge.sql has not been applied yet -- see D-119.",
)


class _Tenant(_TestBuyerTenant):
    """The buyer-test tenant, plus the rows this slice writes."""

    def link(self, document_id: UUID, buyer_name: str, email: str | None = None):
        with tenant_session(self.tenant_id) as session:
            return identify_and_link_buyer(
                session, self.tenant_id, document_id, buyer_name=buyer_name, buyer_contact_email=email
            )

    def rule(self, *, buyer_id: UUID | None, rule_type: str, key: str, value: dict, status="active") -> UUID:
        rule_id = uuid4()
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO learned_rules "
                    "(id, tenant_id, buyer_id, rule_type, match_key, match_value, status) "
                    "VALUES (:id, :t, :b, :type, :key, :value, :status)"
                ),
                {
                    "id": str(rule_id),
                    "t": str(self.tenant_id),
                    "b": str(buyer_id) if buyer_id else None,
                    "type": rule_type,
                    "key": key,
                    "value": value,
                    "status": status,
                },
            )
        return rule_id

    def item(self, sku: str) -> UUID:
        item_id = uuid4()
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO items (id, tenant_id, sku, description) VALUES (:id, :t, :sku, 'Test item')"
                ),
                {"id": str(item_id), "t": str(self.tenant_id), "sku": sku},
            )
        return item_id

    def one(self, sql: str, **params):
        with platform_session() as session:
            return session.execute(text(sql), {"t": str(self.tenant_id), **params}).mappings().first()

    def __exit__(self, *exc):
        tid = str(self.tenant_id)
        with platform_session() as session:
            for table in ("buyer_merges", "learned_rules", "items"):
                session.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tid})
            session.execute(text("DELETE FROM admin_actions WHERE target_tenant_id = :t"), {"t": tid})
        super().__exit__(*exc)


def _flagged_pair(tenant: _Tenant):
    """Two orders from what may be one customer; the second is flagged."""
    first = tenant.create_document(buyer_name="Bella's Test Coffee House")
    second = tenant.create_document(buyer_name="Bellas Test Coffee House LLC")
    kept = tenant.link(first, "Bella's Test Coffee House").buyer_id
    other = tenant.link(second, "Bellas Test Coffee House LLC", email="orders@bellas.example").buyer_id
    candidate = tenant.one("SELECT id FROM buyer_merge_candidates WHERE tenant_id = :t AND status = 'open'")[
        "id"
    ]
    return first, second, kept, other, str(candidate)


def _base(tenant) -> str:
    return f"/admin/tenants/{tenant.tenant_id}"


# ── Buyer merge ─────────────────────────────────────────────────────────────


@requires_buyer_merge_schema
@requires_console_schema
def test_a_merge_moves_everything_leaves_an_alias_and_is_logged(client):
    with _Console() as console, _Tenant("Acme Test Distributor -- merge") as tenant:
        first, second, kept, merged, candidate = _flagged_pair(tenant)
        item = tenant.item("TEST-1001")
        moved_rule = tenant.rule(
            buyer_id=merged,
            rule_type="sku_mapping",
            key="test beans 5lb",
            value={"item_id": str(item), "sku": "TEST-1001", "raw_description": "Test Beans 5lb"},
        )

        listed = client.get(f"{_base(tenant)}/buyer-merges", headers=console.headers())
        assert listed.status_code == 200
        (pair,) = listed.json()["candidates"]
        assert pair["id"] == candidate
        assert {b["id"] for b in pair["buyers"]} == {str(kept), str(merged)}
        assert pair["buyers"][0]["id"] == str(kept)  # the older buyer first

        response = client.post(
            f"{_base(tenant)}/buyer-merges/{candidate}/merge",
            headers=console.headers(),
            json={"keep_buyer_id": str(kept)},
        )
        assert response.status_code == 200, response.text
        assert response.json() | {"merge_id": None} == {
            "merge_id": None,
            "documents_moved": 1,
            "rules_moved": 1,
            "fields_filled": ["contact_email"],
        }

        assert tenant.header_buyer_id(second) == kept
        assert tenant.header_buyer_id(first) == kept
        gone = tenant.one("SELECT deleted_at, merged_into_buyer_id FROM buyers WHERE id = :b", b=str(merged))
        assert gone["deleted_at"] is not None and str(gone["merged_into_buyer_id"]) == str(kept)
        keep = tenant.one("SELECT name, contact_email FROM buyers WHERE id = :b", b=str(kept))
        assert (keep["name"], keep["contact_email"]) == ("Bella's Test Coffee House", "orders@bellas.example")
        assert str(
            tenant.one("SELECT buyer_id FROM learned_rules WHERE id = :r", r=str(moved_rule))["buyer_id"]
        ) == str(kept)
        assert (
            tenant.one("SELECT status FROM buyer_merge_candidates WHERE id = :c", c=candidate)["status"]
            == "merged"
        )

        alias = tenant.one(
            "SELECT id, buyer_id, match_key, confirmed_by, acting_as_tenant_id FROM learned_rules "
            "WHERE tenant_id = :t AND rule_type = 'buyer_alias'"
        )
        assert alias["match_key"] == "bellas test coffee house llc"
        assert str(alias["confirmed_by"]) == str(console.user_id)  # the founder, as themselves
        assert str(alias["acting_as_tenant_id"]) == str(tenant.tenant_id)
        log = tenant.one("SELECT * FROM buyer_merges WHERE tenant_id = :t")
        assert log["document_ids"] == [str(second)] and log["rule_ids"] == [str(moved_rule)]
        assert str(log["merged_by"]) == str(console.user_id) and str(log["alias_rule_id"]) == str(alias["id"])
        assert (
            tenant.one(
                "SELECT count(*) AS n FROM admin_actions "
                "WHERE target_tenant_id = :t AND action = 'buyer_merge'"
            )["n"]
            == 1
        )

        history = client.get(f"{_base(tenant)}/buyer-merges", headers=console.headers()).json()
        assert history["candidates"] == []
        assert history["history"][0]["merged_name"] == "Bellas Test Coffee House LLC"
        assert history["history"][0]["by_docflow_support"] is True

        # The next order under the merged-away name links to the kept buyer,
        # through the alias, with the rule as provenance -- and creates nothing.
        third = tenant.create_document(buyer_name="BELLAS TEST COFFEE HOUSE, LLC")
        result = tenant.link(third, "BELLAS TEST COFFEE HOUSE, LLC")
        assert (result.buyer_id, result.created, result.matched_on) == (kept, False, "buyer_alias")
        provenance = tenant.one(
            "SELECT field_provenance FROM document_headers WHERE document_id = :d", d=str(third)
        )["field_provenance"]
        assert provenance["buyer_id"] == f"learned_rule:{alias['id']}"
        assert len([b for b in tenant.buyers() if b["deleted_at"] is None]) == 1
        assert (
            tenant.one(
                "SELECT count(*) AS n FROM buyer_merge_candidates WHERE tenant_id = :t AND status = 'open'"
            )["n"]
            == 0
        )


@requires_buyer_merge_schema
@requires_console_schema
def test_either_side_can_be_kept(client):
    with _Console() as console, _Tenant("Acme Test Distributor -- keep new") as tenant:
        first, _, older, newer, candidate = _flagged_pair(tenant)
        response = client.post(
            f"{_base(tenant)}/buyer-merges/{candidate}/merge",
            headers=console.headers(),
            json={"keep_buyer_id": str(newer)},
        )
        assert response.status_code == 200, response.text
        assert tenant.header_buyer_id(first) == newer
        assert (
            tenant.one("SELECT deleted_at FROM buyers WHERE id = :b", b=str(older))["deleted_at"] is not None
        )


@requires_buyer_merge_schema
@requires_console_schema
def test_a_rule_both_buyers_have_stops_the_merge_and_changes_nothing(client):
    with _Console() as console, _Tenant("Acme Test Distributor -- clash") as tenant:
        _, second, kept, merged, candidate = _flagged_pair(tenant)
        for buyer in (kept, merged):
            tenant.rule(buyer_id=buyer, rule_type="uom_alias", key="cs", value={"unit_of_measure": "CASE"})

        response = client.post(
            f"{_base(tenant)}/buyer-merges/{candidate}/merge",
            headers=console.headers(),
            json={"keep_buyer_id": str(kept)},
        )
        assert response.status_code == 409 and response.json()["detail"]["code"] == "BUY-009"
        assert tenant.header_buyer_id(second) == merged
        assert all(b["deleted_at"] is None for b in tenant.buyers())
        assert (
            tenant.one("SELECT status FROM buyer_merge_candidates WHERE id = :c", c=candidate)["status"]
            == "open"
        )
        assert tenant.one("SELECT count(*) AS n FROM buyer_merges WHERE tenant_id = :t")["n"] == 0


@requires_buyer_merge_schema
@requires_console_schema
def test_a_resolved_pair_or_a_stranger_as_keeper_is_refused(client):
    with _Console() as console, _Tenant("Acme Test Distributor -- refused") as tenant:
        _, _, kept, _, candidate = _flagged_pair(tenant)
        url = f"{_base(tenant)}/buyer-merges/{candidate}/merge"
        stranger = client.post(url, headers=console.headers(), json={"keep_buyer_id": str(uuid4())})
        assert stranger.status_code == 409 and stranger.json()["detail"]["code"] == "BUY-008"

        assert (
            client.post(url, headers=console.headers(), json={"keep_buyer_id": str(kept)}).status_code == 200
        )
        again = client.post(url, headers=console.headers(), json={"keep_buyer_id": str(kept)})
        assert again.status_code == 409 and again.json()["detail"]["code"] == "BUY-008"


@requires_buyer_merge_schema
@requires_console_schema
def test_dismissing_leaves_both_buyers_alone(client):
    with _Console() as console, _Tenant("Acme Test Distributor -- dismiss") as tenant:
        first, second, older, newer, candidate = _flagged_pair(tenant)
        response = client.post(f"{_base(tenant)}/buyer-merges/{candidate}/dismiss", headers=console.headers())
        assert response.status_code == 200
        assert (
            tenant.one("SELECT status FROM buyer_merge_candidates WHERE id = :c", c=candidate)["status"]
            == "dismissed"
        )
        assert (tenant.header_buyer_id(first), tenant.header_buyer_id(second)) == (older, newer)
        assert all(b["deleted_at"] is None for b in tenant.buyers())
        assert (
            client.get(f"{_base(tenant)}/buyer-merges", headers=console.headers()).json()["candidates"] == []
        )


@requires_buyer_merge_schema
@requires_console_schema
def test_one_tenants_pair_is_404_under_another_tenant(client):
    with (
        _Console() as console,
        _Tenant("Acme Test Distributor -- merge A") as tenant_a,
        _Tenant("Acme Test Distributor -- merge B") as tenant_b,
    ):
        _, _, kept_b, _, candidate_b = _flagged_pair(tenant_b)
        response = client.post(
            f"{_base(tenant_a)}/buyer-merges/{candidate_b}/merge",
            headers=console.headers(),
            json={"keep_buyer_id": str(kept_b)},
        )
        assert response.status_code == 404
        assert (
            tenant_b.one("SELECT status FROM buyer_merge_candidates WHERE id = :c", c=candidate_b)["status"]
            == "open"
        )


# ── Learned rules ───────────────────────────────────────────────────────────


@requires_buyer_merge_schema
@requires_console_schema
def test_rules_are_listed_and_can_be_switched_off_on_and_deleted(client):
    with _Console() as console, _Tenant("Acme Test Distributor -- rules") as tenant:
        buyer = tenant.seed_buyer("Acme Test Buyer")
        item = tenant.item("TEST-2002")
        rule = tenant.rule(
            buyer_id=buyer,
            rule_type="sku_mapping",
            key="test widget blue",
            value={"item_id": str(item), "sku": "TEST-2002", "raw_description": "Test Widget, Blue"},
        )
        with platform_session() as session:
            session.execute(
                text("UPDATE learned_rules SET confirmed_by = :u, acting_as_tenant_id = :t WHERE id = :r"),
                {"u": str(console.user_id), "t": str(tenant.tenant_id), "r": str(rule)},
            )
        base = f"{_base(tenant)}/rules"

        (listed,) = client.get(base, headers=console.headers()).json()["rules"]
        assert listed["id"] == str(rule) and listed["buyer_name"] == "Acme Test Buyer"
        assert (listed["item_sku"], listed["item_retired"], listed["status"]) == (
            "TEST-2002",
            False,
            "active",
        )
        assert listed["by_docflow_support"] is True

        def fires() -> bool:
            with tenant_session(tenant.tenant_id) as session:
                return "test widget blue" in load_sku_rules(session, tenant.tenant_id, buyer)

        assert fires()
        assert (
            client.post(f"{base}/{rule}/disable", headers=console.headers()).json()["change"]["after"]
            == "disabled"
        )
        assert not fires()
        assert client.post(f"{base}/{rule}/enable", headers=console.headers()).status_code == 200
        assert fires()
        assert client.post(f"{base}/{rule}/delete", headers=console.headers()).status_code == 200
        assert not fires()
        assert client.get(base, headers=console.headers()).json()["rules"] == []
        assert (
            tenant.one("SELECT deleted_at FROM learned_rules WHERE id = :r", r=str(rule))["deleted_at"]
            is not None
        )
        assert client.post(f"{base}/{rule}/enable", headers=console.headers()).status_code == 404

        actions = tenant.one(
            "SELECT count(*) AS n FROM admin_actions "
            "WHERE target_tenant_id = :t AND action LIKE 'learned_rule_%'"
        )["n"]
        assert actions == 4  # disable, enable, delete, and the refused enable


@requires_buyer_merge_schema
@requires_console_schema
def test_a_proposed_rule_cannot_be_switched_on(client):
    with _Console() as console, _Tenant("Acme Test Distributor -- proposal") as tenant:
        rule = tenant.rule(
            buyer_id=None,
            rule_type="uom_alias",
            key="bx",
            value={"unit_of_measure": "BOX"},
            status="proposed",
        )
        response = client.post(f"{_base(tenant)}/rules/{rule}/enable", headers=console.headers())
        assert response.status_code == 409 and response.json()["detail"]["code"] == "RUL-001"
        assert (
            tenant.one("SELECT status FROM learned_rules WHERE id = :r", r=str(rule))["status"] == "proposed"
        )


@requires_buyer_merge_schema
@requires_console_schema
def test_one_tenants_rule_is_404_under_another_tenant(client):
    with (
        _Console() as console,
        _Tenant("Acme Test Distributor -- rules A") as tenant_a,
        _Tenant("Acme Test Distributor -- rules B") as tenant_b,
    ):
        rule_b = tenant_b.rule(
            buyer_id=None, rule_type="uom_alias", key="cs", value={"unit_of_measure": "CASE"}
        )
        assert client.get(f"{_base(tenant_a)}/rules", headers=console.headers()).json()["rules"] == []
        response = client.post(f"{_base(tenant_a)}/rules/{rule_b}/disable", headers=console.headers())
        assert response.status_code == 404
        assert (
            tenant_b.one("SELECT status FROM learned_rules WHERE id = :r", r=str(rule_b))["status"]
            == "active"
        )


def test_the_operator_routes_are_404_to_everyone_else(client):
    t, x = UUID(int=1), UUID(int=2)
    for method, path in (
        ("get", f"/admin/tenants/{t}/buyer-merges"),
        ("post", f"/admin/tenants/{t}/buyer-merges/{x}/merge"),
        ("post", f"/admin/tenants/{t}/buyer-merges/{x}/dismiss"),
        ("get", f"/admin/tenants/{t}/rules"),
        ("post", f"/admin/tenants/{t}/rules/{x}/disable"),
        ("post", f"/admin/tenants/{t}/rules/{x}/enable"),
        ("post", f"/admin/tenants/{t}/rules/{x}/delete"),
    ):
        assert getattr(client, method)(path).status_code == 404
