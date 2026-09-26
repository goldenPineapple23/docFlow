"""
Quarantine: holding, listing, releasing and clearing documents (CLAUDE.md
Section 7.16.4; D-126).

`quarantined` is a document status, not a folder. A held document is stored
and never sent to the model, never counted against the allowance and never in
the review queue -- until someone releases it, at which point it goes through
the normal pipeline in the order it was received.

Who may release what is decided HERE, once, and enforced by the API through
this module (Section 3: permissions at the API layer, never only in the UI):

  * the tenant's owner or admin: only holds that are the tenant's own call about
    their own buyers -- `attachment_cap`, `unknown_sender_velocity`,
    `sender_not_allowed`;
  * the founder (from the Console, acting-as): anything, including a hold whose
    ceiling is still tripped. That is an explicit human act, so the gate is not
    consulted; it is logged like every Console write.

Nothing here discards. `clear` is a soft delete, founder-only, and the stored
file stays until the retention period ends and the founder removes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import allowance, document_status
from docflow_core.constants import QUARANTINE_TTL_DAYS
from docflow_core.errors import ErrorCatalogEntry, get_error

# Holds the tenant may release themselves.
TENANT_RELEASABLE = frozenset({"attachment_cap", "unknown_sender_velocity", "sender_not_allowed"})
# Tenant roles that may do so.
TENANT_RELEASING_ROLES = frozenset({"owner", "admin"})

# The plain-English catalog entry for each reason ("12 documents are being held
# because ..."). Both ceilings share one wording: the customer needs to know
# their documents are safe and being looked at, not which meter tripped.
_REASON_CODE = {
    "attachment_cap": "INT-002",
    "unknown_sender_velocity": "INT-003",
    "auth_fail": "INT-004",
    "abuse_ceiling": "INT-007",
    "cost_breaker": "INT-007",
    "sender_not_allowed": "INT-009",
    "manual": "INT-007",
}


# What the FOUNDER sees for each reason in the Console (a label, not customer
# wording -- the customer reads the catalog entry above). Says what tripped, in
# words that don't need the code table.
REASON_LABELS = {
    "abuse_ceiling": "Monthly volume ceiling reached (3x the allowance)",
    "cost_breaker": "Daily AI-cost ceiling reached",
    "attachment_cap": "Too many attachments in one email",
    "auth_fail": "Sender failed its authentication check",
    "unknown_sender_velocity": "Too many new senders in one hour",
    "sender_not_allowed": "Sender is not on the approved list",
    "manual": "Held manually",
}


def reason_label(reason: str) -> str:
    return REASON_LABELS.get(reason, reason)


class QuarantineError(Exception):
    """A refusal with an error-catalog code (QUA-0xx)."""

    def __init__(self, code: str, detail: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


def reason_entry(reason: str) -> ErrorCatalogEntry:
    return get_error(_REASON_CODE.get(reason, "INT-007"))


@dataclass(frozen=True)
class HeldGroup:
    reason: str
    count: int
    can_release: bool
    entry: ErrorCatalogEntry


def hold(
    session: Session,
    tenant_id: UUID,
    document_id: UUID,
    reason: str,
) -> None:
    """Move an already-stored document into quarantine (used for holds that
    are decided after the row exists)."""
    document_status.transition_many(
        session,
        tenant_id,
        [document_id],
        from_statuses=["pending"],
        to="quarantined",
        values={"quarantine_reason": reason, "quarantined_at": document_status.NOW},
    )


def held_summary(session: Session, tenant_id: UUID, *, role: str | None) -> list[HeldGroup]:
    """Held documents grouped by reason, for the tenant's 'Held for review'
    section: a count and the reason in plain English, and whether *this* role
    can release the group."""
    rows = (
        session.execute(
            text(
                """
            SELECT quarantine_reason AS reason, count(*) AS n
            FROM documents
            WHERE tenant_id = :t AND status = 'quarantined' AND deleted_at IS NULL
              AND NOT is_test_batch
            GROUP BY quarantine_reason ORDER BY min(created_at)
            """
            ),
            {"t": str(tenant_id)},
        )
        .mappings()
        .all()
    )
    return [
        HeldGroup(
            reason=r["reason"],
            count=int(r["n"]),
            can_release=can_release(role, r["reason"]),
            entry=reason_entry(r["reason"]),
        )
        for r in rows
    ]


def can_release(role: str | None, reason: str) -> bool:
    """`role=None` is the founder acting from the Console."""
    if role is None:
        return True
    return role in TENANT_RELEASING_ROLES and reason in TENANT_RELEASABLE


def list_held(session: Session, tenant_id: UUID, *, limit: int = 500) -> list[dict[str, Any]]:
    """The Console's per-tenant list: sender, subject, authentication results,
    file type, hash and reason, oldest first (the order they would be released
    in). Subject and authentication come from the email's forensic record."""
    rows = (
        session.execute(
            text(
                """
            SELECT d.id, d.original_filename, d.content_sha256, d.sender_email, d.source,
                   d.quarantine_reason, d.quarantined_at, d.created_at,
                   e.subject, e.spf_result, e.dkim_result, e.dmarc_result
            FROM documents d
            LEFT JOIN raw_emails e
                   ON e.tenant_id = d.tenant_id AND e.message_id = d.message_id
            WHERE d.tenant_id = :t AND d.status = 'quarantined' AND d.deleted_at IS NULL
              AND NOT d.is_test_batch
            ORDER BY d.created_at, d.id
            LIMIT :limit
            """
            ),
            {"t": str(tenant_id), "limit": limit},
        )
        .mappings()
        .all()
    )
    out = []
    for r in rows:
        row = dict(r)
        name = row["original_filename"] or ""
        row["file_type"] = name.rsplit(".", 1)[-1].lower() if "." in name else None
        out.append(row)
    return out


@dataclass(frozen=True)
class ReleaseResult:
    released: list[UUID]  # in received order, which is the order to enqueue
    skipped: list[UUID]  # not held any more, or not this tenant's


def release(
    session: Session,
    tenant_id: UUID,
    document_ids: list[UUID],
    *,
    role: str | None,
    actor_user_id: UUID,
    acting_as_tenant_id: UUID | None = None,
) -> ReleaseResult:
    """Release held documents. All-or-nothing on permission: if any chosen
    document is held for a reason this role may not release, nothing is
    released (QUA-001) -- a partial release would leave the person guessing
    which ones went through. Documents no longer held are skipped, and if none
    remain the request fails with QUA-002.

    Released documents become `pending`, keep their original `created_at` (so
    they release in received order), and start counting against the allowance
    because they no longer have the `quarantined` status."""
    if not document_ids:
        raise QuarantineError("QUA-002")
    rows = (
        session.execute(
            text(
                """
            SELECT id, quarantine_reason FROM documents
            WHERE tenant_id = :t AND id = ANY(CAST(string_to_array(:ids, ',') AS uuid[]))
              AND status = 'quarantined' AND deleted_at IS NULL
            ORDER BY created_at, id
            FOR UPDATE
            """
            ),
            {"t": str(tenant_id), "ids": ",".join(str(i) for i in document_ids)},
        )
        .mappings()
        .all()
    )
    held = {UUID(str(r["id"])): r["quarantine_reason"] for r in rows}
    if not held:
        raise QuarantineError("QUA-002")
    if any(not can_release(role, reason) for reason in held.values()):
        raise QuarantineError("QUA-001")

    candidates = [UUID(str(r["id"])) for r in rows]
    moved = set(
        document_status.transition_many(
            session,
            tenant_id,
            candidates,
            from_statuses=["quarantined"],
            to="pending",
            values={
                "released_at": document_status.NOW,
                "released_by_user_id": str(actor_user_id),
                "released_acting_as_tenant_id": str(acting_as_tenant_id) if acting_as_tenant_id else None,
            },
        )
    )
    # Received order, as selected above (Section 7.16.4), minus any that
    # someone else released or cleared in the meantime.
    ordered = [document_id for document_id in candidates if document_id in moved]
    # They count now, so the 80% / 100% notices may fire.
    allowance.record_thresholds(session, tenant_id)
    return ReleaseResult(
        released=ordered,
        skipped=[i for i in document_ids if i not in held],
    )


def clear(
    session: Session,
    tenant_id: UUID,
    document_ids: list[UUID],
    *,
    confirm_name: str,
) -> list[UUID]:
    """Founder-only. Soft-deletes held documents (Section 7.10: soft-delete
    everywhere a delete exists). The caller has already required the founder;
    this requires the tenant's name typed exactly."""
    name = session.execute(
        text("SELECT name FROM tenants WHERE id = :t"), {"t": str(tenant_id)}
    ).scalar_one_or_none()
    if name is None or confirm_name != name:
        raise QuarantineError("QUA-003")
    rows = (
        session.execute(
            text(
                """
            UPDATE documents SET deleted_at = now()
             WHERE tenant_id = :t AND id = ANY(CAST(string_to_array(:ids, ',') AS uuid[]))
               AND status = 'quarantined' AND deleted_at IS NULL
            RETURNING id
            """
            ),
            {"t": str(tenant_id), "ids": ",".join(str(i) for i in document_ids)},
        )
        .scalars()
        .all()
    )
    if not rows:
        raise QuarantineError("QUA-002")
    return [UUID(str(r)) for r in rows]


def expired_count(session: Session, tenant_id: UUID) -> int:
    """Held documents older than the retention period. They are surfaced to the
    founder for deletion, never auto-deleted."""
    return int(
        session.execute(
            text(
                "SELECT count(*) FROM documents WHERE tenant_id = :t AND status = 'quarantined' "
                "AND deleted_at IS NULL AND quarantined_at < now() - make_interval(days => :days)"
            ),
            {"t": str(tenant_id), "days": QUARANTINE_TTL_DAYS},
        ).scalar_one()
    )
