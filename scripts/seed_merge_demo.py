"""
Seed look-alike customers so the Console's "Possible duplicate customers"
screen (D-119) has something real to decide on.

Runs the normal path -- `identify_and_link_buyer` -- so every buyer, flag and
score is produced by the same code a processed order goes through; nothing is
inserted into `buyer_merge_candidates` by hand.

Every name is obviously fake (CLAUDE.md Section 0 rule 4). The orders it
creates are marked with a `seed_merge_demo` filename so they are easy to find
and delete again.

    python scripts/seed_merge_demo.py <tenant-id> [--clean]
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "core"))

from docflow_core.buyers import identify_and_link_buyer
from docflow_core.db import platform_session, tenant_session
from sqlalchemy import text

FILENAME = "seed_merge_demo.txt"

# (buyer name, contact email). Each pair is one company written two ways --
# exactly what a buyer's own paperwork does over time.
ORDERS = [
    ("Acme Test Cafe, LLC", "orders@acme-test-cafe.example"),
    ("Northwind Test Supply", None),
    ("Northwind Test Supply Co.", "purchasing@northwind-test.example"),
]


def _create_document(tenant_id: UUID, buyer_name: str, email: str | None) -> UUID:
    document_id = uuid4()
    with platform_session() as session:
        session.execute(
            text(
                """
                -- Seeded outside the pipeline: raw_json is a labelled stand-in, not a model
                -- answer. Migration 0027 requires one on every reviewable document (D-156, D-158).
                INSERT INTO documents
                    (id, tenant_id, original_filename, storage_path, source, status,
                     content_sha256, is_test_batch, raw_json, created_at)
                VALUES
                    (:id, :tenant_id, :filename, :path, 'upload', 'needs_review',
                     :sha, true, '{"header": {}, "line_items": [], "seeded_demo": true}'::jsonb, now())
                """
            ),
            {
                "id": str(document_id),
                "tenant_id": str(tenant_id),
                "filename": FILENAME,
                "path": f"tenants/{tenant_id}/seed/{document_id}.txt",
                "sha": uuid4().hex,
            },
        )
        session.execute(
            text(
                """
                INSERT INTO document_headers
                    (document_id, tenant_id, po_number, buyer_name, buyer_contact_email,
                     currency, created_at, updated_at)
                VALUES
                    (:id, :tenant_id, :po, :buyer, :email, 'USD', now(), now())
                """
            ),
            {
                "id": str(document_id),
                "tenant_id": str(tenant_id),
                "po": f"TEST-PO-{document_id.hex[:4].upper()}",
                "buyer": buyer_name,
                "email": email,
            },
        )
    return document_id


# Two rules, so a merge has something to move and the learned-rules screen
# has something to show. Seeded rows, not confirmations -- a real rule is only
# ever created by a reviewer or a merge (Section 7.13).
def _seed_rules(tenant_id: UUID) -> None:
    with platform_session() as session:
        owner = session.execute(
            text(
                "SELECT id FROM users WHERE tenant_id = :t AND role = 'owner' AND deleted_at IS NULL "
                "ORDER BY created_at LIMIT 1"
            ),
            {"t": str(tenant_id)},
        ).scalar()
        item = (
            session.execute(
                text(
                    "SELECT id FROM items WHERE tenant_id = :t AND deleted_at IS NULL ORDER BY sku LIMIT 1"
                ),
                {"t": str(tenant_id)},
            )
            .mappings()
            .first()
        )
        buyer = session.execute(
            text(
                "SELECT id FROM buyers WHERE tenant_id = :t AND name = :name AND deleted_at IS NULL"
            ),
            {"t": str(tenant_id), "name": ORDERS[0][0]},
        ).scalar()
        if item is None or buyer is None:
            print("no catalog item or seeded buyer: skipping rules")
            return
        session.execute(
            text(
                """
                INSERT INTO learned_rules
                    (id, tenant_id, buyer_id, rule_type, match_key, match_value, status, confirmed_by)
                VALUES
                    (:id, :t, :b, 'sku_mapping', 'colombian beans 5 lb bag', :value, 'active', :u)
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "id": str(uuid4()),
                "t": str(tenant_id),
                "b": str(buyer),
                "u": str(owner) if owner else None,
                "value": {
                    "item_id": str(item["id"]),
                    "sku": "TEST-1001",
                    "raw_description": "Colombian Beans 5 lb bag",
                },
            },
        )
        session.execute(
            text(
                """
                INSERT INTO learned_rules
                    (id, tenant_id, buyer_id, rule_type, match_key, match_value, status, confirmed_by)
                VALUES (:id, :t, NULL, 'uom_alias', 'cs', :value, 'active', :u)
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "id": str(uuid4()),
                "t": str(tenant_id),
                "u": str(owner) if owner else None,
                "value": {"unit_of_measure": "CASE"},
            },
        )
    print(
        "seeded 2 learned rules (one on the look-alike customer, one for every customer)"
    )


def seed(tenant_id: UUID) -> None:
    for buyer_name, email in ORDERS:
        document_id = _create_document(tenant_id, buyer_name, email)
        with tenant_session(tenant_id) as session:
            result = identify_and_link_buyer(
                session,
                tenant_id,
                document_id,
                buyer_name=buyer_name,
                buyer_contact_email=email,
            )
        print(
            f"{buyer_name:28} -> buyer {str(result.buyer_id)[:8]} "
            f"({'created' if result.created else 'existing'}), "
            f"{len(result.merge_candidates)} look-alike(s) flagged"
        )
    _seed_rules(tenant_id)
    with platform_session() as session:
        open_pairs = session.execute(
            text(
                "SELECT count(*) FROM buyer_merge_candidates "
                "WHERE tenant_id = :t AND status = 'open' AND deleted_at IS NULL"
            ),
            {"t": str(tenant_id)},
        ).scalar()
    print(f"\n{open_pairs} pair(s) waiting on /admin/tenants/{tenant_id}/merges")


def clean(tenant_id: UUID) -> None:
    """Remove what this script created: its orders, and any buyer and flag
    that came from them. Buyers that existed before are left alone."""
    with platform_session() as session:
        ids = [
            str(r[0])
            for r in session.execute(
                text(
                    "SELECT id FROM documents WHERE tenant_id = :t AND original_filename = :f"
                ),
                {"t": str(tenant_id), "f": FILENAME},
            )
        ]
        if not ids:
            print("nothing seeded by this script")
            return
        params = {"t": str(tenant_id), "ids": ids, "names": [name for name, _ in ORDERS]}
        # A seeded buyer is one this script's orders created. Order matters:
        # everything that points at those buyers goes before they do.
        seeded_buyers = (
            "(SELECT id FROM buyers WHERE tenant_id = :t "
            "AND created_from_document_id::text = ANY(:ids))"
        )
        for statement in (
            f"DELETE FROM buyer_merge_candidates WHERE tenant_id = :t AND buyer_id IN {seeded_buyers}",
            (
                "DELETE FROM buyer_merge_candidates WHERE tenant_id = :t "
                f"AND existing_buyer_id IN {seeded_buyers}"
            ),
            "DELETE FROM document_headers WHERE tenant_id = :t AND document_id::text = ANY(:ids)",
            "DELETE FROM buyer_merges WHERE tenant_id = :t",
            (
                "DELETE FROM learned_rules WHERE tenant_id = :t AND ("
                "match_key IN ('colombian beans 5 lb bag', 'cs') "
                # The alias a merge left behind: it hangs off the KEPT buyer,
                # which may have existed before this script ran.
                "OR (rule_type = 'buyer_alias' AND match_value->>'alias_name' = ANY(:names)))"
            ),
            (
                "UPDATE buyers SET merged_into_buyer_id = NULL WHERE tenant_id = :t "
                f"AND merged_into_buyer_id IN {seeded_buyers}"
            ),
            "DELETE FROM buyers WHERE tenant_id = :t AND created_from_document_id::text = ANY(:ids)",
            "DELETE FROM documents WHERE tenant_id = :t AND id::text = ANY(:ids)",
        ):
            session.execute(text(statement), params)
    print(f"removed {len(ids)} seeded order(s) and the buyers they created")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    tenant = UUID(sys.argv[1])
    if "--clean" in sys.argv:
        clean(tenant)
    else:
        seed(tenant)
