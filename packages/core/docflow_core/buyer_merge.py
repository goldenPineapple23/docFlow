"""
Merging near-duplicate buyers (CLAUDE.md Section 7.6, 7.13; D-119).

    "Buyer auto-creation: on a new buyer name, create the record and flag any
     near-duplicate existing names for founder merge. Never auto-merge.
     Merging is a founder action that re-points foreign keys in a
     transaction and is logged."
    "buyer_alias (a founder merge becomes a rule)"

The flagging half lives in `buyers` (0004). This is the other half: a human
looks at a flagged pair and either dismisses it or merges it. There is no
code path that merges without a candidate a person chose, and no threshold
at which anything merges on its own.

A merge, in one transaction:
  1. Every document header linked to the merged buyer is linked to the kept
     one. Approved snapshots are NOT touched -- they are immutable (7.3) and
     keep the name the document was approved with.
  2. Every learned rule scoped to the merged buyer moves to the kept one. If
     both buyers already have a rule for the same wording, the merge stops
     (BUY-009): which one is right is a human decision, and a merge must
     never quietly drop a human's confirmation.
  3. The kept buyer gains the merged buyer's contact email / account number
     only where it had none -- never an overwrite.
  4. The merged buyer is soft-deleted and points at the kept one.
  5. A `buyer_alias` rule is created: the merged buyer's name now identifies
     the kept buyer, so the next order under the old name links to the right
     record instead of re-creating the duplicate.
  6. The candidate is resolved; other open candidates that named the merged
     buyer now name the kept one (or close, if that would pair it with
     itself).
  7. A `buyer_merges` row records all of it.

Every function takes an already-open tenant-scoped session (Section 7.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core.buyers import normalize_buyer_name


class MergeError(Exception):
    """A refusal with an error-catalog code (BUY-008, BUY-009)."""

    def __init__(self, code: str, detail: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


@dataclass(frozen=True)
class MergeResult:
    merge_id: UUID
    kept_buyer_id: UUID
    merged_buyer_id: UUID
    alias_rule_id: UUID
    documents_moved: int
    rules_moved: int
    fields_filled: list[str]


# ── Reading ─────────────────────────────────────────────────────────────────


def list_open_candidates(session: Session) -> list[dict[str, Any]]:
    """Open near-duplicate pairs, most similar first, with what the founder
    needs to decide: both names, contacts, account numbers, and how many
    orders and rules each has."""
    rows = session.execute(
        text(
            """
            WITH doc_counts AS (
                SELECT buyer_id, count(*) AS n FROM document_headers
                WHERE buyer_id IS NOT NULL AND deleted_at IS NULL GROUP BY buyer_id
            ), rule_counts AS (
                SELECT buyer_id, count(*) AS n FROM learned_rules
                WHERE buyer_id IS NOT NULL AND deleted_at IS NULL GROUP BY buyer_id
            )
            SELECT c.id, c.similarity_score, c.created_at, c.detected_from_document_id,
                   n.id AS new_id, n.name AS new_name, n.contact_email AS new_email,
                   n.external_account_number AS new_account, n.created_at AS new_created_at,
                   coalesce(nd.n, 0) AS new_documents, coalesce(nr.n, 0) AS new_rules,
                   e.id AS existing_id, e.name AS existing_name, e.contact_email AS existing_email,
                   e.external_account_number AS existing_account, e.created_at AS existing_created_at,
                   coalesce(ed.n, 0) AS existing_documents, coalesce(er.n, 0) AS existing_rules
            FROM buyer_merge_candidates c
            JOIN buyers n ON n.id = c.buyer_id AND n.deleted_at IS NULL
            JOIN buyers e ON e.id = c.existing_buyer_id AND e.deleted_at IS NULL
            LEFT JOIN doc_counts nd ON nd.buyer_id = n.id
            LEFT JOIN doc_counts ed ON ed.buyer_id = e.id
            LEFT JOIN rule_counts nr ON nr.buyer_id = n.id
            LEFT JOIN rule_counts er ON er.buyer_id = e.id
            WHERE c.status = 'open' AND c.deleted_at IS NULL
            ORDER BY c.similarity_score DESC, c.created_at
            """
        )
    ).mappings().all()
    return [
        {
            "id": r["id"],
            "similarity_score": r["similarity_score"],
            "created_at": r["created_at"],
            "detected_from_document_id": r["detected_from_document_id"],
            "buyers": [
                _side(r, "existing"),
                _side(r, "new"),
            ],
        }
        for r in rows
    ]


def _side(row: Any, prefix: str) -> dict[str, Any]:
    return {
        "id": row[f"{prefix}_id"],
        "name": row[f"{prefix}_name"],
        "contact_email": row[f"{prefix}_email"],
        "external_account_number": row[f"{prefix}_account"],
        "created_at": row[f"{prefix}_created_at"],
        "documents": row[f"{prefix}_documents"],
        "rules": row[f"{prefix}_rules"],
    }


def list_merges(session: Session, limit: int = 50) -> list[dict[str, Any]]:
    """Recent merges, newest first, for the history under the queue."""
    rows = session.execute(
        text(
            """
            SELECT m.id, m.created_at, m.acting_as_tenant_id,
                   jsonb_array_length(m.document_ids) AS documents_moved,
                   jsonb_array_length(m.rule_ids) AS rules_moved,
                   m.fields_filled,
                   k.name AS kept_name, g.name AS merged_name,
                   u.email AS merged_by_email
            FROM buyer_merges m
            JOIN buyers k ON k.id = m.kept_buyer_id
            JOIN buyers g ON g.id = m.merged_buyer_id
            -- LEFT: the founder's own users row is outside the tenant (D-118).
            LEFT JOIN users u ON u.id = m.merged_by
            ORDER BY m.created_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


# ── Deciding ────────────────────────────────────────────────────────────────


def _open_candidate(session: Session, candidate_id: UUID) -> dict[str, Any]:
    row = session.execute(
        text(
            "SELECT id, buyer_id, existing_buyer_id, status FROM buyer_merge_candidates "
            "WHERE id = :id AND deleted_at IS NULL FOR UPDATE"
        ),
        {"id": str(candidate_id)},
    ).mappings().first()
    if row is None:
        raise LookupError(candidate_id)
    if row["status"] != "open":
        raise MergeError("BUY-008", {"reason": "candidate_resolved"})
    return dict(row)


def dismiss_candidate(
    session: Session, candidate_id: UUID, *, actor_user_id: UUID
) -> None:
    """Dismiss -- "not the same customer". Both buyers stay as they are; the pair is not
    flagged again (the unique index on the pair keeps the dismissed row)."""
    _open_candidate(session, candidate_id)
    session.execute(
        text(
            "UPDATE buyer_merge_candidates SET status = 'dismissed', resolved_by = :u, "
            "resolved_at = now(), updated_at = now() WHERE id = :id"
        ),
        {"id": str(candidate_id), "u": str(actor_user_id)},
    )


def merge_candidate(
    session: Session,
    tenant_id: UUID,
    candidate_id: UUID,
    *,
    keep_buyer_id: UUID,
    actor_user_id: UUID,
    acting_as_tenant_id: UUID | None = None,
) -> MergeResult:
    """Merge the flagged pair, keeping `keep_buyer_id` (either side of it)."""
    candidate = _open_candidate(session, candidate_id)
    pair = {str(candidate["buyer_id"]), str(candidate["existing_buyer_id"])}
    if str(keep_buyer_id) not in pair:
        raise MergeError("BUY-008", {"reason": "keep_not_in_pair"})
    (merged_str,) = pair - {str(keep_buyer_id)}
    kept, merged = keep_buyer_id, UUID(merged_str)

    # Lock both, in a fixed order so two merges can't deadlock each other.
    buyers = {
        str(r["id"]): dict(r)
        for r in session.execute(
            text(
                "SELECT id, name, contact_email, external_account_number, deleted_at FROM buyers "
                "WHERE id IN (:a, :b) ORDER BY id FOR UPDATE"
            ),
            {"a": str(kept), "b": str(merged)},
        ).mappings()
    }
    if len(buyers) != 2 or any(b["deleted_at"] is not None for b in buyers.values()):
        raise MergeError("BUY-008", {"reason": "buyer_gone"})
    keep_row, gone_row = buyers[str(kept)], buyers[str(merged)]

    # 2 (checked first, so a refusal changes nothing): rules both buyers have.
    clashes = session.execute(
        text(
            """
            SELECT g.rule_type, g.match_key
            FROM learned_rules g
            JOIN learned_rules k
              ON k.buyer_id = :kept AND k.rule_type = g.rule_type AND k.match_key = g.match_key
             AND k.deleted_at IS NULL
            WHERE g.buyer_id = :merged AND g.deleted_at IS NULL
            """
        ),
        {"kept": str(kept), "merged": str(merged)},
    ).mappings().all()
    if clashes:
        raise MergeError("BUY-009", {"rules": len(clashes)})

    # 1. Documents.
    document_ids = [
        str(r[0])
        for r in session.execute(
            text(
                "UPDATE document_headers SET buyer_id = :kept, updated_at = now() "
                "WHERE buyer_id = :merged RETURNING document_id"
            ),
            {"kept": str(kept), "merged": str(merged)},
        )
    ]
    # 2. Rules.
    rule_ids = [
        str(r[0])
        for r in session.execute(
            text(
                "UPDATE learned_rules SET buyer_id = :kept, updated_at = now() "
                "WHERE buyer_id = :merged AND deleted_at IS NULL RETURNING id"
            ),
            {"kept": str(kept), "merged": str(merged)},
        )
    ]
    # 3. Fill, never overwrite.
    filled = [
        column
        for column in ("contact_email", "external_account_number")
        if keep_row[column] is None and gone_row[column] is not None
    ]
    if filled:
        session.execute(
            text(
                "UPDATE buyers SET "
                + ", ".join(f"{c} = :{c}" for c in filled)  # fixed column names, never input
                + ", updated_at = now() WHERE id = :id"
            ),
            {"id": str(kept), **{c: gone_row[c] for c in filled}},
        )
    # 4. The merged buyer.
    session.execute(
        text(
            "UPDATE buyers SET deleted_at = now(), merged_into_buyer_id = :kept, merged_at = now(), "
            "updated_at = now() WHERE id = :merged"
        ),
        {"kept": str(kept), "merged": str(merged)},
    )
    # Anything that had been merged INTO the merged buyer now points at the kept one.
    session.execute(
        text("UPDATE buyers SET merged_into_buyer_id = :kept WHERE merged_into_buyer_id = :merged"),
        {"kept": str(kept), "merged": str(merged)},
    )

    # 5. The alias rule. An alias with this wording may already exist for the
    # kept buyer (merging a name back in): then it is reused.
    alias_key = normalize_buyer_name(gone_row["name"])
    alias_id = uuid4()
    inserted = session.execute(
        text(
            """
            INSERT INTO learned_rules
                (id, tenant_id, buyer_id, rule_type, match_key, match_value, status,
                 confirmed_by, acting_as_tenant_id, created_at, updated_at)
            VALUES
                (:id, :tenant_id, :kept, 'buyer_alias', :key, :value, 'active',
                 :actor, :acting_as, now(), now())
            ON CONFLICT (tenant_id, buyer_id, rule_type, match_key)
                WHERE buyer_id IS NOT NULL AND deleted_at IS NULL
            DO UPDATE SET status = 'active', updated_at = now()
            RETURNING id
            """
        ),
        {
            "id": str(alias_id),
            "tenant_id": str(tenant_id),
            "kept": str(kept),
            "key": alias_key,
            "value": {"alias_name": gone_row["name"], "merged_buyer_id": str(merged)},
            "actor": str(actor_user_id),
            "acting_as": str(acting_as_tenant_id) if acting_as_tenant_id else None,
        },
    ).scalar_one()
    alias_id = UUID(str(inserted))

    # 6. This candidate, then every other open one naming the merged buyer.
    session.execute(
        text(
            "UPDATE buyer_merge_candidates SET status = 'merged', resolved_by = :u, resolved_at = now(), "
            "updated_at = now() WHERE id = :id"
        ),
        {"id": str(candidate_id), "u": str(actor_user_id)},
    )
    _repoint_candidates(session, kept=kept, merged=merged, actor_user_id=actor_user_id)

    # 7. The log.
    merge_id = uuid4()
    session.execute(
        text(
            """
            INSERT INTO buyer_merges
                (id, tenant_id, kept_buyer_id, merged_buyer_id, candidate_id, alias_rule_id,
                 document_ids, rule_ids, fields_filled, merged_by, acting_as_tenant_id)
            VALUES
                (:id, :tenant_id, :kept, :merged, :candidate, :alias,
                 :documents, :rules, :filled, :actor, :acting_as)
            """
        ),
        {
            "id": str(merge_id),
            "tenant_id": str(tenant_id),
            "kept": str(kept),
            "merged": str(merged),
            "candidate": str(candidate_id),
            "alias": str(alias_id),
            "documents": document_ids,
            "rules": rule_ids,
            "filled": filled,
            "actor": str(actor_user_id),
            "acting_as": str(acting_as_tenant_id) if acting_as_tenant_id else None,
        },
    )
    return MergeResult(
        merge_id=merge_id,
        kept_buyer_id=kept,
        merged_buyer_id=merged,
        alias_rule_id=alias_id,
        documents_moved=len(document_ids),
        rules_moved=len(rule_ids),
        fields_filled=filled,
    )


def _repoint_candidates(session: Session, *, kept: UUID, merged: UUID, actor_user_id: UUID) -> None:
    rows = session.execute(
        text(
            "SELECT id, buyer_id, existing_buyer_id FROM buyer_merge_candidates "
            "WHERE status = 'open' AND deleted_at IS NULL "
            "AND (buyer_id = :merged OR existing_buyer_id = :merged) FOR UPDATE"
        ),
        {"merged": str(merged)},
    ).mappings().all()
    for row in rows:
        new_pair = [
            str(kept) if str(row[col]) == str(merged) else str(row[col])
            for col in ("buyer_id", "existing_buyer_id")
        ]
        if new_pair[0] == new_pair[1]:
            # Would pair the kept buyer with itself: the question is answered.
            session.execute(
                text(
                    "UPDATE buyer_merge_candidates SET status = 'merged', resolved_by = :u, "
                    "resolved_at = now(), updated_at = now() WHERE id = :id"
                ),
                {"id": str(row["id"]), "u": str(actor_user_id)},
            )
            continue
        exists = session.execute(
            text(
                "SELECT 1 FROM buyer_merge_candidates WHERE deleted_at IS NULL AND id <> :id "
                "AND ((buyer_id = :a AND existing_buyer_id = :b) "
                "OR (buyer_id = :b AND existing_buyer_id = :a))"
            ),
            {"id": str(row["id"]), "a": new_pair[0], "b": new_pair[1]},
        ).first()
        if exists:
            # The same question is already asked (or answered) under the kept buyer.
            session.execute(
                text(
                    "UPDATE buyer_merge_candidates SET deleted_at = now(), updated_at = now() "
                    "WHERE id = :id"
                ),
                {"id": str(row["id"])},
            )
        else:
            session.execute(
                text(
                    "UPDATE buyer_merge_candidates SET buyer_id = :a, existing_buyer_id = :b, "
                    "updated_at = now() WHERE id = :id"
                ),
                {"id": str(row["id"]), "a": new_pair[0], "b": new_pair[1]},
            )
