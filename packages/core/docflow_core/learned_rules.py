"""
Managing learned rules (CLAUDE.md Section 7.13; D-119).

Rules are created only by a human action -- a reviewer confirming a SKU
match (`matching.confirm_sku_mapping`) or a founder merging buyers
(`buyer_merge`). This module never creates one. It lets the founder see
every rule a tenant has, and switch one off:

  * disable / re-enable -- "it can be switched off" (7.13). A disabled rule
    stops firing on the next document; nothing already on a document changes,
    and it never overrides a human edit (Section 10).
  * delete -- a soft delete (Section 7.10). The row stays for audit; the
    wording can then be taught again from a review.

Every function takes an already-open tenant-scoped session (Section 7.5).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

RULE_TYPES = ("sku_mapping", "buyer_alias", "uom_alias", "field_hint")


class RuleError(Exception):
    """A refusal with an error-catalog code (RUL-0xx)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def list_rules(session: Session) -> list[dict[str, Any]]:
    """Every live rule of the tenant, newest first, with what a founder needs
    to judge it: who it's for, what it maps to, whether the SKU it points at
    still exists, how often it has fired, and who confirmed it."""
    rows = session.execute(
        text(
            """
            SELECT r.id, r.rule_type, r.match_key, r.match_value, r.status, r.times_applied,
                   r.created_at, r.updated_at, r.source_document_id, r.acting_as_tenant_id,
                   r.buyer_id, b.name AS buyer_name,
                   u.email AS confirmed_by_email,
                   dh.po_number AS source_po_number,
                   i.sku AS item_sku, i.description AS item_description,
                   (i.id IS NOT NULL AND i.deleted_at IS NOT NULL) AS item_retired
            FROM learned_rules r
            LEFT JOIN buyers b ON b.id = r.buyer_id
            -- LEFT: the founder's own users row is outside the tenant (D-118).
            LEFT JOIN users u ON u.id = r.confirmed_by
            LEFT JOIN document_headers dh ON dh.document_id = r.source_document_id
            LEFT JOIN items i
                   ON r.rule_type = 'sku_mapping'
                  AND i.id = CASE WHEN r.match_value->>'item_id' ~ '^[0-9a-f-]{36}$'
                                  THEN (r.match_value->>'item_id')::uuid END
            WHERE r.deleted_at IS NULL AND r.status IN ('active', 'disabled')
            ORDER BY r.created_at DESC
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _live_rule(session: Session, rule_id: UUID) -> dict[str, Any]:
    row = session.execute(
        text(
            "SELECT id, rule_type, status, buyer_id, match_key FROM learned_rules "
            "WHERE id = :id AND deleted_at IS NULL FOR UPDATE"
        ),
        {"id": str(rule_id)},
    ).mappings().first()
    if row is None:
        raise LookupError(rule_id)
    if row["status"] == "proposed":
        # Proposals are the Section 7.13 hook, not built in the MVP; nothing
        # here may activate one.
        raise RuleError("RUL-001")
    return dict(row)


def set_status(session: Session, rule_id: UUID, *, enabled: bool) -> dict[str, Any]:
    """Disable or re-enable. Returns the before/after for the audit row."""
    rule = _live_rule(session, rule_id)
    after = "active" if enabled else "disabled"
    session.execute(
        text("UPDATE learned_rules SET status = :s, updated_at = now() WHERE id = :id"),
        {"id": str(rule_id), "s": after},
    )
    return {"rule_type": rule["rule_type"], "before": rule["status"], "after": after}


def delete_rule(session: Session, rule_id: UUID) -> dict[str, Any]:
    """Soft delete. The row stays, for the audit trail and any provenance
    that points at it."""
    rule = _live_rule(session, rule_id)
    session.execute(
        text("UPDATE learned_rules SET deleted_at = now(), updated_at = now() WHERE id = :id"),
        {"id": str(rule_id)},
    )
    return {"rule_type": rule["rule_type"], "before": rule["status"], "after": "deleted"}
