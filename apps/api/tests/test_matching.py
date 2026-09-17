"""
Catalog (SKU) matching against the real database (CLAUDE.md Section 7.6),
including the Section 7.5 required test class: Tenant B's catalog is never a
match candidate for Tenant A's lines, and Tenant B's learned rule never fires
for Tenant A.

Everything runs through `tenant_session()` exactly as the worker does, so the
RLS policies from 0002/0004 are what enforces isolation here -- not a WHERE
clause the test wrote itself. Setup and teardown use `platform_session()`
(the same pattern as test_buyers.py) because a test fixture is not tenant
traffic.

Every row created is removed in the context manager's __exit__, whether the
test passed or failed. All catalog and buyer data is fictional
(Section 0 rule 4).
"""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID, uuid4

from docflow_core.db import platform_session, tenant_session
from docflow_core.matching import (
    EXACT_MATCH_SCORE,
    FUZZY_MATCH_THRESHOLD,
    LEARNED_RULE_MATCH_SCORE,
    PROVENANCE_EXACT,
    PROVENANCE_FUZZY,
    _upsert_sku_mapping_rule,
    confirm_sku_mapping,
    load_catalog,
    load_sku_rules,
    load_uom_rules,
    match_document_lines,
    normalize_description,
)
from sqlalchemy import text

from tests.conftest import requires_matching_schema, requires_sku_matching_schema


class _TestMatchingTenant:
    """A throwaway tenant with a catalog, buyers, documents and lines."""

    def __init__(self, name: str):
        self.name = name
        self.tenant_id = uuid4()
        self.user_id = uuid4()

    def __enter__(self):
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO tenants "
                    "(id, name, status, onboarding_status, created_at, updated_at, status_changed_at) "
                    "VALUES (:id, :name, 'active', 'tenant_created', now(), now(), now())"
                ),
                {"id": str(self.tenant_id), "name": self.name},
            )
            session.execute(
                text(
                    "INSERT INTO users (id, tenant_id, email, role, created_at, updated_at) "
                    "VALUES (:id, :tenant_id, :email, 'owner', now(), now())"
                ),
                {
                    "id": str(self.user_id),
                    "tenant_id": str(self.tenant_id),
                    "email": f"reviewer-{self.user_id.hex[:8]}@example.test",
                },
            )
        return self

    # ── fixtures ──────────────────────────────────────────────────────────

    def seed_item(self, sku: str, description: str | None, uom: str | None = "CS") -> UUID:
        item_id = uuid4()
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO items (id, tenant_id, sku, description, unit_of_measure, "
                    "created_at, updated_at) "
                    "VALUES (:id, :tenant_id, :sku, :description, :uom, now(), now())"
                ),
                {
                    "id": str(item_id),
                    "tenant_id": str(self.tenant_id),
                    "sku": sku,
                    "description": description,
                    "uom": uom,
                },
            )
        return item_id

    def seed_buyer(self, name: str) -> UUID:
        from docflow_core.buyers import normalize_buyer_name

        buyer_id = uuid4()
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO buyers (id, tenant_id, name, normalized_name, created_at, updated_at) "
                    "VALUES (:id, :tenant_id, :name, :normalized_name, now(), now())"
                ),
                {
                    "id": str(buyer_id),
                    "tenant_id": str(self.tenant_id),
                    "name": name,
                    "normalized_name": normalize_buyer_name(name),
                },
            )
        return buyer_id

    def seed_rule(
        self,
        *,
        rule_type: str,
        match_key: str,
        match_value: dict,
        buyer_id: UUID | None = None,
        status: str = "active",
    ) -> UUID:
        rule_id = uuid4()
        with platform_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO learned_rules
                        (id, tenant_id, buyer_id, rule_type, match_key, match_value, status,
                         confirmed_by, created_at, updated_at)
                    VALUES
                        (:id, :tenant_id, :buyer_id, :rule_type, :match_key, :match_value, :status,
                         :confirmed_by, now(), now())
                    """
                ),
                {
                    "id": str(rule_id),
                    "tenant_id": str(self.tenant_id),
                    "buyer_id": str(buyer_id) if buyer_id else None,
                    "rule_type": rule_type,
                    "match_key": normalize_description(match_key),
                    "match_value": match_value,
                    "status": status,
                    "confirmed_by": str(self.user_id),
                },
            )
        return rule_id

    def create_document(self, *, buyer_id: UUID | None = None, lines: list[dict] | None = None) -> UUID:
        """One `documents` row plus its header and lines, in the state the
        extraction task leaves them in (provenance `extracted`, no match)."""
        document_id = uuid4()
        with platform_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO documents
                        (id, tenant_id, original_filename, storage_path, source, status,
                         content_sha256, created_at)
                    VALUES
                        (:id, :tenant_id, 'po.txt', 'tenants/seed/po.txt', 'upload', 'needs_review',
                         :sha, now())
                    """
                ),
                {"id": str(document_id), "tenant_id": str(self.tenant_id), "sha": uuid4().hex},
            )
            session.execute(
                text(
                    "INSERT INTO document_headers (document_id, tenant_id, buyer_id, "
                    "created_at, updated_at) VALUES (:document_id, :tenant_id, :buyer_id, now(), now())"
                ),
                {
                    "document_id": str(document_id),
                    "tenant_id": str(self.tenant_id),
                    "buyer_id": str(buyer_id) if buyer_id else None,
                },
            )
            for number, line in enumerate(lines or [], start=1):
                provenance = {
                    key: "extracted"
                    for key in ("sku", "description", "unit")
                    if line.get(key) is not None
                }
                provenance.update(line.get("field_provenance") or {})
                session.execute(
                    text(
                        """
                        INSERT INTO document_lines
                            (id, document_id, tenant_id, line_number, sku, description, unit,
                             quantity, field_provenance, created_at)
                        VALUES
                            (:id, :document_id, :tenant_id, :line_number, :sku, :description, :unit,
                             :quantity, :field_provenance, now())
                        """
                    ),
                    {
                        "id": str(line.get("id") or uuid4()),
                        "document_id": str(document_id),
                        "tenant_id": str(self.tenant_id),
                        "line_number": number,
                        "sku": line.get("sku"),
                        "description": line.get("description"),
                        "unit": line.get("unit"),
                        "quantity": line.get("quantity"),
                        "field_provenance": provenance,
                    },
                )
        return document_id

    # ── read-back ─────────────────────────────────────────────────────────

    def lines(self, document_id: UUID) -> list[dict]:
        with platform_session() as session:
            rows = session.execute(
                text(
                    "SELECT id, line_number, sku, description, unit, matched_item_id, match_method, "
                    "match_score, matched_uom, uom_mismatch, match_candidates, field_provenance "
                    "FROM document_lines WHERE document_id = :did ORDER BY line_number"
                ),
                {"did": str(document_id)},
            ).mappings().all()
        return [dict(row) for row in rows]

    def rule(self, rule_id: UUID) -> dict | None:
        with platform_session() as session:
            row = session.execute(
                text(
                    "SELECT id, buyer_id, rule_type, match_key, match_value, status, confirmed_by, "
                    "source_document_id, times_applied FROM learned_rules WHERE id = :id"
                ),
                {"id": str(rule_id)},
            ).mappings().first()
        return dict(row) if row else None

    def rules(self) -> list[dict]:
        with platform_session() as session:
            rows = session.execute(
                text(
                    "SELECT id, buyer_id, rule_type, match_key, match_value, status, times_applied "
                    "FROM learned_rules WHERE tenant_id = :tid ORDER BY created_at"
                ),
                {"tid": str(self.tenant_id)},
            ).mappings().all()
        return [dict(row) for row in rows]

    def __exit__(self, *exc):
        tid = str(self.tenant_id)
        with platform_session() as session:
            session.execute(text("DELETE FROM learned_rules WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM document_lines WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM document_headers WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(
                text("DELETE FROM buyer_merge_candidates WHERE tenant_id = :tid"), {"tid": tid}
            )
            session.execute(text("DELETE FROM buyers WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM items WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM documents WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM users WHERE tenant_id = :tid"), {"tid": tid})
            session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tid})


def _candidates(line: dict) -> list[dict]:
    """`match_candidates` comes back as parsed JSON on jsonb; be tolerant of a
    driver that hands back the raw string."""
    value = line["match_candidates"]
    return json.loads(value) if isinstance(value, str) else value


def _provenance(line: dict) -> dict:
    value = line["field_provenance"]
    return json.loads(value) if isinstance(value, str) else value


# ── catalog and rule loading ────────────────────────────────────────────────
#
# These need only the 0004 objects (`items`, `learned_rules`), so they run
# today and cover the queries with the most SQL in them -- the tenant-wide/
# buyer-scoped OR predicate and the partial-index ON CONFLICT target. The
# tests above additionally need 0005's columns on `document_lines` and skip
# until the founder applies it (DECISIONS.md D-071).


@requires_matching_schema
def test_load_catalog_returns_live_items_only_and_never_another_tenants():
    with _TestMatchingTenant("Acme Test Distributor") as tenant_a:
        with _TestMatchingTenant("Northwind Test Supply") as tenant_b:
            live = tenant_a.seed_item("CF-1001", "Colombian Whole Bean 5lb")
            retired = tenant_a.seed_item("CF-9999", "Discontinued Test Blend 5lb")
            b_item = tenant_b.seed_item("CF-1001", "Colombian Whole Bean 5lb")
            with platform_session() as session:
                session.execute(
                    text("UPDATE items SET deleted_at = now() WHERE id = :id"),
                    {"id": str(retired)},
                )

            with tenant_session(tenant_a.tenant_id) as session:
                catalog = load_catalog(session, tenant_a.tenant_id)

            ids = {item.item_id for item in catalog.items}
            assert ids == {live}
            assert retired not in ids
            assert b_item not in ids
            assert catalog.exact_sku("cf-1001").item_id == live


@requires_matching_schema
def test_load_sku_rules_prefers_the_buyer_scoped_rule_and_skips_inactive_ones():
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        general_item = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        specific_item = tenant.seed_item("CF-2210", "Ethiopian Yirgacheffe 5lb")
        buyer = tenant.seed_buyer("Bella's Coffee House")
        other_buyer = tenant.seed_buyer("Riverbend Test Hardware")

        tenant.seed_rule(
            rule_type="sku_mapping",
            match_key="House Blend Beans",
            match_value={"item_id": str(general_item), "sku": "CF-1001"},
            buyer_id=None,
        )
        buyer_rule = tenant.seed_rule(
            rule_type="sku_mapping",
            match_key="House Blend Beans",
            match_value={"item_id": str(specific_item), "sku": "CF-2210"},
            buyer_id=buyer,
        )
        tenant.seed_rule(
            rule_type="sku_mapping",
            match_key="Proposed Wording",
            match_value={"item_id": str(general_item), "sku": "CF-1001"},
            buyer_id=buyer,
            status="proposed",
        )
        tenant.seed_rule(
            rule_type="sku_mapping",
            match_key="Other Buyers Wording",
            match_value={"item_id": str(general_item), "sku": "CF-1001"},
            buyer_id=other_buyer,
        )

        key = normalize_description("House Blend Beans")
        with tenant_session(tenant.tenant_id) as session:
            rules = load_sku_rules(session, tenant.tenant_id, buyer)
        assert rules[key].rule_id == buyer_rule
        assert rules[key].item_id == specific_item
        assert rules[key].buyer_scoped is True
        # A `proposed` rule is a Section 7.13 hook, not an active rule.
        assert normalize_description("Proposed Wording") not in rules
        # Another buyer's rule is invisible to this buyer.
        assert normalize_description("Other Buyers Wording") not in rules

        # With no buyer resolved, only the tenant-wide rule is in scope.
        with tenant_session(tenant.tenant_id) as session:
            unscoped = load_sku_rules(session, tenant.tenant_id, None)
        assert unscoped[key].item_id == general_item
        assert unscoped[key].buyer_scoped is False


@requires_matching_schema
def test_load_rules_never_crosses_a_tenant_boundary():
    with _TestMatchingTenant("Acme Test Distributor") as tenant_a:
        with _TestMatchingTenant("Northwind Test Supply") as tenant_b:
            b_item = tenant_b.seed_item("CF-1001", "Colombian Whole Bean 5lb")
            tenant_b.seed_rule(
                rule_type="sku_mapping",
                match_key="House Blend Beans",
                match_value={"item_id": str(b_item), "sku": "CF-1001"},
                buyer_id=None,
            )
            with tenant_session(tenant_a.tenant_id) as session:
                assert load_sku_rules(session, tenant_a.tenant_id, None) == {}


@requires_matching_schema
def test_load_uom_rules_reads_the_tenant_level_alias():
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        rule_id = tenant.seed_rule(
            rule_type="uom_alias",
            match_key="CS",
            match_value={"unit_of_measure": "CASE"},
            buyer_id=None,
        )
        with tenant_session(tenant.tenant_id) as session:
            rules = load_uom_rules(session, tenant.tenant_id, None)
        assert rules[normalize_description("CS")].rule_id == rule_id
        assert rules[normalize_description("CS")].unit_of_measure == "CASE"
        # A `sku_mapping` rule is not a `uom_alias` rule.
        assert len(rules) == 1


@requires_matching_schema
def test_the_rule_upsert_refreshes_one_row_rather_than_shadowing_it():
    """
    Exercises `confirm_sku_mapping`'s actual INSERT ... ON CONFLICT against
    the real partial unique indexes from 0004 (D-053). Two rules with the
    same key, one buyer-scoped and one tenant-wide, must coexist; re-
    confirming either must update it in place.
    """
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        first_item = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        second_item = tenant.seed_item("CF-2210", "Ethiopian Yirgacheffe 5lb")
        buyer = tenant.seed_buyer("Bella's Coffee House")
        document = tenant.create_document(buyer_id=buyer)
        key = normalize_description("House Blend Beans")

        with tenant_session(tenant.tenant_id) as session:
            buyer_rule = _upsert_sku_mapping_rule(
                session,
                tenant.tenant_id,
                buyer_id=buyer,
                match_key=key,
                match_value={"item_id": str(first_item), "sku": "CF-1001"},
                confirmed_by=tenant.user_id,
                source_document_id=document,
            )
            tenant_rule = _upsert_sku_mapping_rule(
                session,
                tenant.tenant_id,
                buyer_id=None,
                match_key=key,
                match_value={"item_id": str(first_item), "sku": "CF-1001"},
                confirmed_by=tenant.user_id,
                source_document_id=document,
            )
        assert buyer_rule != tenant_rule
        assert len(tenant.rules()) == 2

        with tenant_session(tenant.tenant_id) as session:
            again = _upsert_sku_mapping_rule(
                session,
                tenant.tenant_id,
                buyer_id=buyer,
                match_key=key,
                match_value={"item_id": str(second_item), "sku": "CF-2210"},
                confirmed_by=tenant.user_id,
                source_document_id=document,
            )

        assert again == buyer_rule
        assert len(tenant.rules()) == 2
        refreshed = tenant.rule(buyer_rule)
        assert refreshed["match_value"]["item_id"] == str(second_item)
        assert refreshed["status"] == "active"
        assert UUID(str(refreshed["source_document_id"])) == document


# ── pipeline order ──────────────────────────────────────────────────────────


@requires_sku_matching_schema
def test_a_learned_rule_wins_over_an_exact_sku_match():
    """
    CLAUDE.md Section 7.6 orders learned mappings first. The line below has a
    SKU that exactly matches one catalog item and a description a human has
    already mapped to a different one; the human's answer wins.
    """
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        colombian = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        ethiopian = tenant.seed_item("CF-2210", "Ethiopian Yirgacheffe 5lb")
        buyer = tenant.seed_buyer("Bella's Coffee House")
        rule_id = tenant.seed_rule(
            rule_type="sku_mapping",
            match_key="House Blend Beans",
            match_value={"item_id": str(ethiopian), "sku": "CF-2210"},
            buyer_id=buyer,
        )
        document = tenant.create_document(
            buyer_id=buyer, lines=[{"sku": "CF-1001", "description": "House Blend Beans"}]
        )

        with tenant_session(tenant.tenant_id) as session:
            match_document_lines(session, tenant.tenant_id, document, buyer_id=buyer)

        line = tenant.lines(document)[0]
        assert UUID(str(line["matched_item_id"])) == ethiopian
        assert UUID(str(line["matched_item_id"])) != colombian
        assert line["match_method"] == "learned_rule"
        assert line["match_score"] == LEARNED_RULE_MATCH_SCORE
        assert _provenance(line)["matched_item_id"] == f"learned_rule:{rule_id}"
        # Section 7.13's "mapping reuse rate" is built on this counter.
        assert tenant.rule(rule_id)["times_applied"] == 1
        # The extracted values themselves are untouched (Section 7.1).
        assert line["sku"] == "CF-1001"
        assert line["description"] == "House Blend Beans"


@requires_sku_matching_schema
def test_an_exact_sku_match_wins_over_a_fuzzy_description_match():
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        colombian = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        ethiopian = tenant.seed_item("CF-2210", "Ethiopian Yirgacheffe 5lb")
        document = tenant.create_document(
            lines=[{"sku": "CF-2210", "description": "Colombian Whole Bean 5 lb"}]
        )

        with tenant_session(tenant.tenant_id) as session:
            match_document_lines(session, tenant.tenant_id, document)

        line = tenant.lines(document)[0]
        assert UUID(str(line["matched_item_id"])) == ethiopian
        assert UUID(str(line["matched_item_id"])) != colombian
        assert line["match_method"] == "exact_sku"
        assert line["match_score"] == EXACT_MATCH_SCORE
        assert _provenance(line)["matched_item_id"] == PROVENANCE_EXACT


# ── fuzzy: above and below threshold ────────────────────────────────────────


@requires_sku_matching_schema
def test_an_above_threshold_fuzzy_match_applies_and_records_its_score():
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        syrup = tenant.seed_item("SY-0045", "Vanilla Syrup 750ml", uom="EA")
        document = tenant.create_document(lines=[{"description": "Syrup, Vanilla 750 ml"}])

        with tenant_session(tenant.tenant_id) as session:
            match_document_lines(session, tenant.tenant_id, document)

        line = tenant.lines(document)[0]
        assert UUID(str(line["matched_item_id"])) == syrup
        assert line["match_method"] == "fuzzy"
        assert isinstance(line["match_score"], Decimal)
        assert line["match_score"] >= FUZZY_MATCH_THRESHOLD
        assert _provenance(line)["matched_item_id"] == PROVENANCE_FUZZY
        assert _candidates(line)[0]["item_id"] == str(syrup)


@requires_sku_matching_schema
def test_a_below_threshold_fuzzy_result_is_recorded_as_a_suggestion_and_never_applied():
    """
    CLAUDE.md Section 7.6 / Section 10, the rule this slice exists to obey.
    The line stays unmatched *and* the reviewer is handed the candidate that
    was considered, with its score.
    """
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        colombian = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        tenant.seed_item("CF-2210", "Ethiopian Yirgacheffe 5lb")
        document = tenant.create_document(lines=[{"description": "Colombian WB 5lb"}])

        with tenant_session(tenant.tenant_id) as session:
            summary = match_document_lines(session, tenant.tenant_id, document)

        assert summary.lines_considered == 1
        assert summary.lines_matched == 0

        line = tenant.lines(document)[0]
        assert line["matched_item_id"] is None
        assert line["match_method"] is None
        assert line["match_score"] is None
        assert "matched_item_id" not in _provenance(line)

        candidates = _candidates(line)
        assert candidates, "an unmatched line must still surface what was considered"
        assert candidates[0]["item_id"] == str(colombian)
        assert Decimal(candidates[0]["score"]) < FUZZY_MATCH_THRESHOLD
        # Scores cross the wire as strings, never floats (Section 7.1).
        assert isinstance(candidates[0]["score"], str)


@requires_sku_matching_schema
def test_a_different_pack_size_is_surfaced_but_never_applied():
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        tenant.seed_item("CF-1005", "Colombian Whole Bean 2lb")
        document = tenant.create_document(lines=[{"description": "Colombian Whole Bean 5lb"}])

        with tenant_session(tenant.tenant_id) as session:
            match_document_lines(session, tenant.tenant_id, document)

        line = tenant.lines(document)[0]
        assert line["matched_item_id"] is None
        candidate = _candidates(line)[0]
        assert Decimal(candidate["score"]) >= FUZZY_MATCH_THRESHOLD
        assert candidate["eligible"] is False
        assert candidate["blocked_reason"] == "measure_mismatch"


# ── the Phase 2 exit criterion ──────────────────────────────────────────────


@requires_sku_matching_schema
def test_a_correction_on_document_one_auto_matches_document_two_from_the_same_buyer():
    """
    **The Phase 2 exit criterion.** A reviewer corrects a line the engine
    could not match; that confirmation becomes an active `sku_mapping` rule;
    the next PO from the same buyer with the same wording matches by itself,
    with `learned_rule:<id>` provenance and the fixed high score.

    Section 7.6 end to end, and the only path in the codebase that creates an
    active learned rule (Section 10: "never activate a learned rule without a
    human confirmation").
    """
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        colombian = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        buyer = tenant.seed_buyer("Bella's Coffee House")

        # Document 1: the buyer's own wording, which matches nothing.
        first = tenant.create_document(
            buyer_id=buyer, lines=[{"sku": "BCH-77", "description": "House Blend Beans"}]
        )
        with tenant_session(tenant.tenant_id) as session:
            match_document_lines(session, tenant.tenant_id, first, buyer_id=buyer)
        assert tenant.lines(first)[0]["matched_item_id"] is None

        # A human resolves it.
        first_line_id = UUID(str(tenant.lines(first)[0]["id"]))
        with tenant_session(tenant.tenant_id) as session:
            rule_id = confirm_sku_mapping(
                session,
                tenant.tenant_id,
                document_line_id=first_line_id,
                item_id=colombian,
                confirmed_by=tenant.user_id,
            )
        assert rule_id is not None

        rule = tenant.rule(rule_id)
        assert rule["status"] == "active"
        assert rule["rule_type"] == "sku_mapping"
        assert rule["match_key"] == normalize_description("House Blend Beans")
        assert UUID(str(rule["buyer_id"])) == buyer
        assert UUID(str(rule["confirmed_by"])) == tenant.user_id
        assert UUID(str(rule["source_document_id"])) == first

        # The corrected line now carries the human's answer, not a machine's.
        corrected = tenant.lines(first)[0]
        assert UUID(str(corrected["matched_item_id"])) == colombian
        assert corrected["match_method"] == "human_confirmed"
        assert _provenance(corrected)["matched_item_id"].startswith("human_edit")

        # Document 2: same buyer, same wording, no human involved.
        second = tenant.create_document(
            buyer_id=buyer, lines=[{"sku": "BCH-77", "description": "House Blend Beans"}]
        )
        with tenant_session(tenant.tenant_id) as session:
            match_document_lines(session, tenant.tenant_id, second, buyer_id=buyer)

        line = tenant.lines(second)[0]
        assert UUID(str(line["matched_item_id"])) == colombian
        assert line["match_method"] == "learned_rule"
        assert line["match_score"] == LEARNED_RULE_MATCH_SCORE
        assert _provenance(line)["matched_item_id"] == f"learned_rule:{rule_id}"
        assert tenant.rule(rule_id)["times_applied"] == 1


@requires_sku_matching_schema
def test_re_running_matching_never_overwrites_a_human_confirmation():
    """Section 10: "Never overwrite a human correction with a machine
    value." Re-processing must leave the reviewer's own answer alone."""
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        colombian = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        # A catalog item whose SKU the line prints exactly -- so an unguarded
        # re-run would have plenty of reason to change the answer.
        tenant.seed_item("BCH-77", "Buyer's Own Part Number")
        document = tenant.create_document(
            lines=[{"sku": "BCH-77", "description": "House Blend Beans"}]
        )
        line_id = UUID(str(tenant.lines(document)[0]["id"]))

        with tenant_session(tenant.tenant_id) as session:
            confirm_sku_mapping(
                session,
                tenant.tenant_id,
                document_line_id=line_id,
                item_id=colombian,
                confirmed_by=tenant.user_id,
            )

        with tenant_session(tenant.tenant_id) as session:
            summary = match_document_lines(session, tenant.tenant_id, document)

        assert summary.skipped_human_edited == 1
        line = tenant.lines(document)[0]
        assert UUID(str(line["matched_item_id"])) == colombian
        assert line["match_method"] == "human_confirmed"


# ── rule scoping ────────────────────────────────────────────────────────────


@requires_sku_matching_schema
def test_a_buyer_scoped_rule_beats_a_tenant_wide_rule_for_the_same_key():
    """
    DECISIONS.md D-065: the more specific human confirmation wins. Otherwise
    a tenant-wide default would make every buyer-specific exception
    unreachable.
    """
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        general = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        specific = tenant.seed_item("CF-2210", "Ethiopian Yirgacheffe 5lb")
        buyer = tenant.seed_buyer("Bella's Coffee House")
        tenant.seed_rule(
            rule_type="sku_mapping",
            match_key="House Blend Beans",
            match_value={"item_id": str(general), "sku": "CF-1001"},
            buyer_id=None,
        )
        buyer_rule = tenant.seed_rule(
            rule_type="sku_mapping",
            match_key="House Blend Beans",
            match_value={"item_id": str(specific), "sku": "CF-2210"},
            buyer_id=buyer,
        )
        document = tenant.create_document(
            buyer_id=buyer, lines=[{"description": "House Blend Beans"}]
        )

        with tenant_session(tenant.tenant_id) as session:
            match_document_lines(session, tenant.tenant_id, document, buyer_id=buyer)

        line = tenant.lines(document)[0]
        assert UUID(str(line["matched_item_id"])) == specific
        assert _provenance(line)["matched_item_id"] == f"learned_rule:{buyer_rule}"


@requires_sku_matching_schema
def test_a_tenant_wide_rule_still_fires_for_a_buyer_with_no_rule_of_their_own():
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        general = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        other_buyer = tenant.seed_buyer("Riverbend Test Hardware")
        rule_id = tenant.seed_rule(
            rule_type="sku_mapping",
            match_key="House Blend Beans",
            match_value={"item_id": str(general), "sku": "CF-1001"},
            buyer_id=None,
        )
        document = tenant.create_document(
            buyer_id=other_buyer, lines=[{"description": "House Blend Beans"}]
        )

        with tenant_session(tenant.tenant_id) as session:
            match_document_lines(session, tenant.tenant_id, document, buyer_id=other_buyer)

        line = tenant.lines(document)[0]
        assert UUID(str(line["matched_item_id"])) == general
        assert _provenance(line)["matched_item_id"] == f"learned_rule:{rule_id}"


@requires_sku_matching_schema
def test_a_rule_scoped_to_buyer_x_does_not_fire_for_buyer_y():
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        item = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        buyer_x = tenant.seed_buyer("Bella's Coffee House")
        buyer_y = tenant.seed_buyer("Riverbend Test Hardware")
        tenant.seed_rule(
            rule_type="sku_mapping",
            match_key="House Blend Beans",
            match_value={"item_id": str(item), "sku": "CF-1001"},
            buyer_id=buyer_x,
        )
        document = tenant.create_document(
            buyer_id=buyer_y, lines=[{"description": "House Blend Beans"}]
        )

        with tenant_session(tenant.tenant_id) as session:
            match_document_lines(session, tenant.tenant_id, document, buyer_id=buyer_y)

        line = tenant.lines(document)[0]
        assert line["matched_item_id"] is None


@requires_sku_matching_schema
def test_a_disabled_rule_does_not_fire():
    """Section 7.13: a learned rule "can be switched off" with immediate
    effect."""
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        item = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        buyer = tenant.seed_buyer("Bella's Coffee House")
        tenant.seed_rule(
            rule_type="sku_mapping",
            match_key="House Blend Beans",
            match_value={"item_id": str(item), "sku": "CF-1001"},
            buyer_id=buyer,
            status="disabled",
        )
        document = tenant.create_document(
            buyer_id=buyer, lines=[{"description": "House Blend Beans"}]
        )

        with tenant_session(tenant.tenant_id) as session:
            match_document_lines(session, tenant.tenant_id, document, buyer_id=buyer)

        assert tenant.lines(document)[0]["matched_item_id"] is None


# ── units of measure: suggest, flag, never correct ──────────────────────────


@requires_sku_matching_schema
def test_a_uom_alias_rule_records_a_suggestion_without_touching_the_extracted_unit():
    """
    CLAUDE.md Section 7.6: "Never silently 'normalize' units of measure...
    Suggest, flag, let the human decide." The document still says "CS".
    """
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb", uom="CASE")
        rule_id = tenant.seed_rule(
            rule_type="uom_alias",
            match_key="CS",
            match_value={"unit_of_measure": "CASE"},
            buyer_id=None,
        )
        document = tenant.create_document(
            lines=[{"sku": "CF-1001", "description": "Colombian Whole Bean 5lb", "unit": "CS"}]
        )

        with tenant_session(tenant.tenant_id) as session:
            match_document_lines(session, tenant.tenant_id, document)

        line = tenant.lines(document)[0]
        assert line["unit"] == "CS", "the extracted unit must never be rewritten"
        assert line["matched_uom"] == "CASE"
        assert _provenance(line)["matched_uom"] == f"learned_rule:{rule_id}"
        # The alias resolved the difference, so there is nothing to flag.
        assert line["uom_mismatch"] is False
        assert tenant.rule(rule_id)["times_applied"] == 1


@requires_sku_matching_schema
def test_a_uom_disagreement_with_no_alias_is_flagged_not_corrected():
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb", uom="CASE")
        document = tenant.create_document(
            lines=[{"sku": "CF-1001", "description": "Colombian Whole Bean 5lb", "unit": "EA"}]
        )

        with tenant_session(tenant.tenant_id) as session:
            match_document_lines(session, tenant.tenant_id, document)

        line = tenant.lines(document)[0]
        assert line["unit"] == "EA"
        assert line["matched_uom"] is None
        assert line["uom_mismatch"] is True
        assert line["matched_item_id"] is not None, "a UOM disagreement is not a reason to unmatch"


# ── tenant isolation (CLAUDE.md Section 7.5's required test class) ──────────


@requires_sku_matching_schema
def test_tenant_b_catalog_is_never_a_match_candidate_for_tenant_a():
    """
    Section 7.5, stated verbatim as a required test: "Tenant B's catalog is
    never a match candidate for Tenant A's lines." Isolation here is RLS, not
    a WHERE clause the test wrote -- the call goes through `tenant_session()`.
    """
    with _TestMatchingTenant("Acme Test Distributor") as tenant_a:
        with _TestMatchingTenant("Northwind Test Supply") as tenant_b:
            b_item = tenant_b.seed_item("CF-1001", "Colombian Whole Bean 5lb")
            # Tenant A's own catalog holds nothing like it.
            tenant_a.seed_item("HW-9000", "Riverbend Test Wrench 10in")
            document = tenant_a.create_document(
                lines=[{"sku": "CF-1001", "description": "Colombian Whole Bean 5lb"}]
            )

            with tenant_session(tenant_a.tenant_id) as session:
                match_document_lines(session, tenant_a.tenant_id, document)

            line = tenant_a.lines(document)[0]
            assert line["matched_item_id"] is None
            candidate_ids = {c["item_id"] for c in _candidates(line)}
            assert str(b_item) not in candidate_ids

            # And a direct read from Tenant A's session cannot see B's item.
            with tenant_session(tenant_a.tenant_id) as session:
                visible = session.execute(
                    text("SELECT id FROM items WHERE id = :id"), {"id": str(b_item)}
                ).first()
            assert visible is None


@requires_sku_matching_schema
def test_tenant_b_learned_rule_never_fires_for_tenant_a():
    """
    Section 7.13: "No learning crosses a tenant boundary. A rule, example, or
    proposal from one tenant is never visible to, or applied for, another."
    """
    with _TestMatchingTenant("Acme Test Distributor") as tenant_a:
        with _TestMatchingTenant("Northwind Test Supply") as tenant_b:
            b_item = tenant_b.seed_item("CF-1001", "Colombian Whole Bean 5lb")
            b_rule = tenant_b.seed_rule(
                rule_type="sku_mapping",
                match_key="House Blend Beans",
                match_value={"item_id": str(b_item), "sku": "CF-1001"},
                buyer_id=None,
            )
            a_item = tenant_a.seed_item("CF-1001", "Colombian Whole Bean 5lb")
            document = tenant_a.create_document(lines=[{"description": "House Blend Beans"}])

            with tenant_session(tenant_a.tenant_id) as session:
                match_document_lines(session, tenant_a.tenant_id, document)

            line = tenant_a.lines(document)[0]
            # Tenant A has an identically described item, but B's rule is not
            # what put it there -- and here nothing matched at all, because
            # "House Blend Beans" resembles nothing in A's catalog.
            assert line["matched_item_id"] is None
            assert str(a_item) not in {
                c["item_id"] for c in _candidates(line) if Decimal(c["score"]) >= FUZZY_MATCH_THRESHOLD
            }
            # B's rule was never applied and its counter never moved.
            assert tenant_b.rule(b_rule)["times_applied"] == 0

            with tenant_session(tenant_a.tenant_id) as session:
                visible = session.execute(
                    text("SELECT id FROM learned_rules WHERE id = :id"), {"id": str(b_rule)}
                ).first()
            assert visible is None


@requires_sku_matching_schema
def test_confirming_a_mapping_against_another_tenants_item_is_refused():
    """A confirmation cannot reach across a tenant boundary either: RLS hides
    the item, and the function refuses rather than creating a rule pointing
    at something this tenant cannot see."""
    import pytest

    with _TestMatchingTenant("Acme Test Distributor") as tenant_a:
        with _TestMatchingTenant("Northwind Test Supply") as tenant_b:
            b_item = tenant_b.seed_item("CF-1001", "Colombian Whole Bean 5lb")
            document = tenant_a.create_document(lines=[{"description": "House Blend Beans"}])
            line_id = UUID(str(tenant_a.lines(document)[0]["id"]))

            with pytest.raises(LookupError):
                with tenant_session(tenant_a.tenant_id) as session:
                    confirm_sku_mapping(
                        session,
                        tenant_a.tenant_id,
                        document_line_id=line_id,
                        item_id=b_item,
                        confirmed_by=tenant_a.user_id,
                    )

            assert tenant_a.rules() == []


# ── whole-document behavior ─────────────────────────────────────────────────


@requires_sku_matching_schema
def test_a_multi_line_document_matches_each_line_independently():
    """The whole sample PO shape: some lines match exactly, one matches
    fuzzily, one matches nothing. All three are correct outcomes."""
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        colombian = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        syrup = tenant.seed_item("SY-0045", "Vanilla Syrup 750ml", uom="EA")
        document = tenant.create_document(
            lines=[
                {"sku": "CF-1001", "description": "Colombian Whole Bean 5lb", "unit": "CS"},
                {"sku": None, "description": "Syrup, Vanilla 750 ml", "unit": "EA"},
                {"sku": "ZZ-0000", "description": "Riverbend Test Widget", "unit": "EA"},
            ]
        )

        with tenant_session(tenant.tenant_id) as session:
            summary = match_document_lines(session, tenant.tenant_id, document)

        assert summary.lines_considered == 3
        assert summary.lines_matched == 2
        assert summary.by_method == {"exact_sku": 1, "fuzzy": 1}

        lines = tenant.lines(document)
        assert UUID(str(lines[0]["matched_item_id"])) == colombian
        assert UUID(str(lines[1]["matched_item_id"])) == syrup
        assert lines[2]["matched_item_id"] is None


@requires_sku_matching_schema
def test_matching_a_document_with_no_catalog_leaves_every_line_unmatched():
    """Not an error and not a failure state -- just a reviewer's job."""
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        document = tenant.create_document(
            lines=[{"sku": "CF-1001", "description": "Colombian Whole Bean 5lb"}]
        )

        with tenant_session(tenant.tenant_id) as session:
            summary = match_document_lines(session, tenant.tenant_id, document)

        assert summary.lines_matched == 0
        line = tenant.lines(document)[0]
        assert line["matched_item_id"] is None
        assert _candidates(line) == []


@requires_sku_matching_schema
def test_matching_is_idempotent_and_clears_a_stale_match_when_the_catalog_changes():
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        item = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        document = tenant.create_document(
            lines=[{"sku": "CF-1001", "description": "Colombian Whole Bean 5lb"}]
        )

        with tenant_session(tenant.tenant_id) as session:
            match_document_lines(session, tenant.tenant_id, document)
        assert UUID(str(tenant.lines(document)[0]["matched_item_id"])) == item

        with tenant_session(tenant.tenant_id) as session:
            match_document_lines(session, tenant.tenant_id, document)
        assert UUID(str(tenant.lines(document)[0]["matched_item_id"])) == item

        # Retire the SKU the way a catalog re-upload does (soft delete, never
        # a hard delete -- Section 10).
        with platform_session() as session:
            session.execute(
                text("UPDATE items SET deleted_at = now() WHERE id = :id"), {"id": str(item)}
            )
        with tenant_session(tenant.tenant_id) as session:
            match_document_lines(session, tenant.tenant_id, document)

        line = tenant.lines(document)[0]
        assert line["matched_item_id"] is None
        assert line["match_method"] is None
        assert "matched_item_id" not in _provenance(line)


@requires_sku_matching_schema
def test_re_confirming_the_same_wording_updates_the_rule_instead_of_shadowing_it():
    with _TestMatchingTenant("Acme Test Distributor") as tenant:
        first_item = tenant.seed_item("CF-1001", "Colombian Whole Bean 5lb")
        second_item = tenant.seed_item("CF-2210", "Ethiopian Yirgacheffe 5lb")
        buyer = tenant.seed_buyer("Bella's Coffee House")
        document = tenant.create_document(
            buyer_id=buyer, lines=[{"description": "House Blend Beans"}]
        )
        line_id = UUID(str(tenant.lines(document)[0]["id"]))

        with tenant_session(tenant.tenant_id) as session:
            first_rule = confirm_sku_mapping(
                session,
                tenant.tenant_id,
                document_line_id=line_id,
                item_id=first_item,
                confirmed_by=tenant.user_id,
            )
        with tenant_session(tenant.tenant_id) as session:
            second_rule = confirm_sku_mapping(
                session,
                tenant.tenant_id,
                document_line_id=line_id,
                item_id=second_item,
                confirmed_by=tenant.user_id,
            )

        assert second_rule == first_rule
        rules = tenant.rules()
        assert len(rules) == 1
        assert rules[0]["match_value"]["item_id"] == str(second_item)
