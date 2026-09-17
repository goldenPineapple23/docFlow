"""
Duplicate and change-order detection (CLAUDE.md Section 7.8).

The specification, in full:

    "Uploads and email attachments are hashed (SHA-256). The same content for
     the same tenant is not re-ingested silently -- it is linked to the
     existing document and surfaced as a possible duplicate."
    "Email intake dedupes on Message-ID plus attachment hash."
    "Duplicate/change-order handling never deletes or overwrites the earlier
     document. Both exist; the relationship is recorded."

Two relationships, both of them a *flag plus a pointer*, never an action:

  * **Duplicate** -- another document for this tenant has the identical
    `content_sha256`. The same bytes arrived twice: a resend, a forward, or
    an intake retry. Detected at ingest (the hash is final the moment the
    file lands) and re-confirmed in the worker, because both paths must agree
    and re-running must be idempotent.
  * **Change order** -- another document for this tenant carries the same PO
    number with *different* content. This is the revision case, and it cannot
    be detected at ingest because the PO number does not exist until
    extraction has run.

Neither one rejects, delays, deletes or edits anything. Both documents keep
processing normally; the newer one gains two columns and a warning row
(`docflow_core.validation`, VAL-012 / VAL-013) and the earlier one is not
touched at all -- there is no UPDATE in this module whose WHERE clause can
name a document other than the one being detected on.

Direction is fixed and never reversed: the **newer** document points at the
**earliest** matching older one. Ordering is `(created_at, id)`, so a tie in
timestamps still resolves deterministically, and a chain of five resends all
point at the original rather than forming a linked list nobody can follow
(DECISIONS.md D-077).

Every function that touches the database takes an already-open, tenant-scoped
`Session` from `docflow_core.db.tenant_session()`. There is no query here
that could see another tenant's documents, which is what makes "Tenant A's
identical file is never flagged against Tenant B's" true by construction and
not by a WHERE clause (Section 7.5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

RELATION_DUPLICATE = "duplicate"
RELATION_CHANGE_ORDER = "change_order"

_WHITESPACE = re.compile(r"\s+")

# The SQL half of `normalize_po_number`. Defined once, here, and used both by
# the query below and by the expression index in
# supabase/migrations/0006_validation_and_duplicates.sql -- if the two ever
# drift, the index silently stops being used and the comparison silently
# changes meaning. `{column}` is only ever formatted with a module-local
# literal column name; no caller input reaches this string.
PO_NUMBER_KEY_SQL = "btrim(regexp_replace(upper({column}), '\\s+', ' ', 'g'))"


def normalize_po_number(value: str | None) -> str:
    """
    The PO-number comparison key: uppercased, every run of whitespace
    collapsed to a single space, trimmed. "po-1001", "PO-1001 " and
    "PO-1001" all compare as one string.

    A derived key, exactly like `buyers.normalize_buyer_name` and
    `matching.normalize_description` (D-054): `document_headers.po_number`
    keeps precisely what the document printed, forever. Nothing in this
    module writes it back.
    """
    if not value:
        return ""
    return _WHITESPACE.sub(" ", value.upper()).strip()


@dataclass(frozen=True)
class DocumentRelationships:
    is_possible_duplicate: bool = False
    duplicate_of_document_id: UUID | None = None
    is_possible_change_order: bool = False
    change_order_of_document_id: UUID | None = None


def relationship_to(
    *,
    same_content: bool,
    same_po_number: bool,
    buyer_id: UUID | None,
    other_buyer_id: UUID | None,
) -> str | None:
    """
    The whole classification decision, pure and DB-free.

      * identical content                     -> `duplicate`
      * same PO number, different content     -> `change_order`, unless the
                                                 two documents are known to
                                                 come from *different* buyers
      * anything else                         -> no relationship

    **Identical content is never also a change order.** The same bytes carry
    the same PO number by definition, and "this is a copy of that exact
    document" is the stronger, more useful statement. If document 1 is
    revision A, document 2 is revision B and document 3 is a resend of
    revision B, then 3 points at 2 as a duplicate and 2 already carries the
    change-order flag -- the chain is intact without claiming 3 revises 1.

    **Two known-different buyers are not a relationship.** "PO-1001" is the
    thousand-and-first order a business has ever placed, and two of a
    distributor's buyers reaching that number independently is ordinary, not
    suspicious.

    **An unknown buyer on either side still flags.** This errs toward
    surfacing, which is the opposite direction from D-055's deliberately
    conservative buyer-merge threshold, and for a reason: nothing here can be
    auto-applied. A false change-order flag costs a reviewer one glance at
    two documents; a missed revision ships the wrong order. See D-078.
    """
    if same_content:
        return RELATION_DUPLICATE
    if not same_po_number:
        return None
    if buyer_id is not None and other_buyer_id is not None and buyer_id != other_buyer_id:
        return None
    return RELATION_CHANGE_ORDER


# ── Database layer (tenant-scoped session required) ─────────────────────────


def find_earlier_content_duplicate(
    session: Session,
    tenant_id: UUID,
    document_id: UUID,
    *,
    content_sha256: str,
    created_at: datetime,
) -> UUID | None:
    """
    The earliest other document for this tenant with identical content, or
    None. "Earlier" is `(created_at, id) <` this document's own pair, so a
    document can never be its own duplicate and two documents can never point
    at each other.
    """
    row = session.execute(
        text(
            """
            SELECT id FROM documents
            WHERE tenant_id = :tenant_id
              AND content_sha256 = :content_sha256
              AND deleted_at IS NULL
              AND (created_at, id) < (:created_at, CAST(:document_id AS uuid))
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """
        ),
        {
            "tenant_id": str(tenant_id),
            "content_sha256": content_sha256,
            "created_at": created_at,
            "document_id": str(document_id),
        },
    ).mappings().first()
    return UUID(str(row["id"])) if row else None


def find_earlier_same_po_document(
    session: Session,
    tenant_id: UUID,
    document_id: UUID,
    *,
    po_key: str,
    content_sha256: str,
    buyer_id: UUID | None,
    created_at: datetime,
) -> UUID | None:
    """
    The earliest other document for this tenant carrying the same PO number
    with different content, or None.

    The buyer predicate implements `relationship_to`'s rule in SQL so the
    filtering happens in one indexed pass: a candidate qualifies when either
    side's buyer is unknown, or both are known and equal.
    """
    if not po_key:
        return None
    key_expression = PO_NUMBER_KEY_SQL.format(column="h.po_number")
    row = session.execute(
        text(
            f"""
            SELECT d.id
            FROM documents d
            JOIN document_headers h ON h.document_id = d.id AND h.deleted_at IS NULL
            WHERE d.tenant_id = :tenant_id
              AND d.deleted_at IS NULL
              AND h.po_number IS NOT NULL
              AND {key_expression} = :po_key
              AND d.content_sha256 <> :content_sha256
              AND (
                    CAST(:buyer_id AS uuid) IS NULL
                 OR h.buyer_id IS NULL
                 OR h.buyer_id = CAST(:buyer_id AS uuid)
              )
              AND (d.created_at, d.id) < (:created_at, CAST(:document_id AS uuid))
            ORDER BY d.created_at ASC, d.id ASC
            LIMIT 1
            """
        ),
        {
            "tenant_id": str(tenant_id),
            "po_key": po_key,
            "content_sha256": content_sha256,
            "buyer_id": str(buyer_id) if buyer_id else None,
            "created_at": created_at,
            "document_id": str(document_id),
        },
    ).mappings().first()
    return UUID(str(row["id"])) if row else None


def detect_document_relationships(
    session: Session, tenant_id: UUID, document_id: UUID
) -> DocumentRelationships:
    """
    Run Section 7.8's detection for one document and record what it found.
    `session` must already be tenant-scoped.

    Writes exactly one UPDATE, against exactly one row: the document being
    detected on. The earlier document is read and never written -- Section 10:
    "duplicate/change-order handling never deletes or overwrites the earlier
    document."

    Idempotent: re-running on an unchanged document reaches the same
    conclusion and writes the same values. Re-running after the earlier
    document has been soft-deleted clears the flag rather than leaving a
    pointer at something a reviewer can no longer open.
    """
    row = session.execute(
        text(
            """
            SELECT d.content_sha256, d.created_at, h.po_number, h.buyer_id
            FROM documents d
            LEFT JOIN document_headers h
                   ON h.document_id = d.id AND h.deleted_at IS NULL
            WHERE d.id = :document_id AND d.deleted_at IS NULL
            """
        ),
        {"document_id": str(document_id)},
    ).mappings().first()
    if row is None:
        return DocumentRelationships()

    buyer_id = UUID(str(row["buyer_id"])) if row["buyer_id"] else None
    duplicate_of = find_earlier_content_duplicate(
        session,
        tenant_id,
        document_id,
        content_sha256=row["content_sha256"],
        created_at=row["created_at"],
    )

    change_order_of: UUID | None = None
    if duplicate_of is None:
        change_order_of = find_earlier_same_po_document(
            session,
            tenant_id,
            document_id,
            po_key=normalize_po_number(row["po_number"]),
            content_sha256=row["content_sha256"],
            buyer_id=buyer_id,
            created_at=row["created_at"],
        )

    result = DocumentRelationships(
        is_possible_duplicate=duplicate_of is not None,
        duplicate_of_document_id=duplicate_of,
        is_possible_change_order=change_order_of is not None,
        change_order_of_document_id=change_order_of,
    )

    session.execute(
        text(
            """
            UPDATE documents
            SET is_possible_duplicate = :is_possible_duplicate,
                duplicate_of_document_id = :duplicate_of_document_id,
                is_possible_change_order = :is_possible_change_order,
                change_order_of_document_id = :change_order_of_document_id
            WHERE id = :document_id
            """
        ),
        {
            "document_id": str(document_id),
            "is_possible_duplicate": result.is_possible_duplicate,
            "duplicate_of_document_id": str(duplicate_of) if duplicate_of else None,
            "is_possible_change_order": result.is_possible_change_order,
            "change_order_of_document_id": str(change_order_of) if change_order_of else None,
        },
    )
    return result


def find_content_duplicate_at_ingest(
    session: Session, tenant_id: UUID, content_sha256: str
) -> UUID | None:
    """
    The ingest-time half of Section 7.8, shared by the upload endpoint and
    email intake so the two cannot disagree about what a duplicate is.

    Called *before* the new `documents` row exists, so there is no "earlier
    than me" predicate to apply -- every existing row is earlier. Returns the
    earliest match, which the caller writes straight into the INSERT rather
    than issuing a second UPDATE (closing DECISIONS.md D-020, which surfaced
    the relationship in the HTTP response without ever storing it).
    """
    row = session.execute(
        text(
            "SELECT id FROM documents "
            "WHERE tenant_id = :tenant_id AND content_sha256 = :content_sha256 AND deleted_at IS NULL "
            "ORDER BY created_at ASC, id ASC LIMIT 1"
        ),
        {"tenant_id": str(tenant_id), "content_sha256": content_sha256},
    ).mappings().first()
    return UUID(str(row["id"])) if row else None
