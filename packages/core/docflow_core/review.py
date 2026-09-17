"""
Human review, approval, and the immutable approved snapshot
(CLAUDE.md Section 7.3).

The specification, in full:

    "Approval is an explicit user action. There is no code path that sets
     `approved` without a user ID and timestamp."
    "Any unresolved warning at approval time must be explicitly acknowledged;
     the acknowledgement is recorded in `review_actions` with the warning
     text."
    "On approval, freeze a complete snapshot of the approved header and lines
     as `approved_json` on the document. This snapshot is immutable. Exports
     are generated from the snapshot, never from the live tables. If someone
     edits after approval, the document reverts to `needs_review` and must be
     re-approved -- the old snapshot is retained."
    "Every human edit writes a `review_actions` row with before and after
     values. Never overwrite a human correction with a machine value on
     re-extraction."

Four things in this module are load-bearing, and each has a test whose name
says so:

  * **Approval is never anonymous.** `approve_document` takes a `user_id` and
    there is no default, no "system" sentinel, and no other function anywhere
    that writes `status = 'approved'`. The database agrees
    (`documents_approved_is_attributable` in 0007), so even a future code
    path that tried would be rejected.
  * **An edit is only ever a human's.** `apply_edits` is the sole writer of
    an extracted value outside the extraction and matching pipelines, it
    always writes a `review_actions` row in the same transaction, and it
    stamps `human_edit:<review_action_id>` into `field_provenance` -- which
    is what makes `matching._human_edited` skip that field forever afterwards
    (Section 10: "never overwrite a human correction with a machine value").
  * **The snapshot is frozen, not referenced.** `freeze_snapshot` copies the
    header and lines into a self-contained JSON document. An export built
    from it months later does not depend on the live tables still saying the
    same thing, which is the entire point of Section 7.4's round-trip test.
  * **Editing an approved document reopens it.** It does not silently amend
    the approved data, and it does not discard the old snapshot. The prior
    snapshot is superseded, never deleted.

Two layers, the same shape `validation.py` and `matching.py` use: a pure
layer that takes plain data and returns plain data, and a database layer that
takes an already-open, tenant-scoped `Session` from
`docflow_core.db.tenant_session()`. This module never opens its own
connection and never accepts a tenant_id from request data (Section 7.5).

Every number is a `Decimal` in Python and a string in JSON. No float touches
a quantity, a price, or a total (Section 7.1) -- including in the snapshot,
which is the document an export must deep-equal.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core.errors import get_error

# ── Catalog codes this module can raise ─────────────────────────────────────
CODE_UNACKNOWLEDGED_WARNINGS = "REV-001"
CODE_ALREADY_APPROVED = "REV-002"
CODE_FIELD_NOT_EDITABLE = "REV-003"
CODE_NOT_REVIEWABLE = "REV-004"
CODE_STALE_EDIT = "REV-005"

# ── Review action types (0007's CHECK constraint, mirrored) ─────────────────
ACTION_EDITED = "edited"
ACTION_APPROVED = "approved"
ACTION_REJECTED = "rejected"
ACTION_REOPENED = "reopened"

# ── What a human is allowed to edit ─────────────────────────────────────────
#
# An allowlist, not a denylist, and it is the ONLY thing that decides which
# column an edit can reach. Two reasons, and the second is the important one:
#
#   1. The obvious one -- a field name arrives from an HTTP request, and it
#      is interpolated into no SQL anywhere in this module (Section 10), but
#      an allowlist means a wrong name is a clean REV-003 rather than a
#      surprise.
#   2. The one that matters -- `documents.status`, `raw_json`, `model_id`,
#      `content_sha256`, `approved_json` and the confidence columns are the
#      record of what the MACHINE did. A review screen that could edit those
#      could rewrite history to make a document look like it was always
#      right. The reviewer corrects what the order SAYS; nothing they do
#      changes what DocFlow read or when.
EDITABLE_HEADER_FIELDS: frozenset[str] = frozenset(
    {
        "po_number",
        "order_date",
        "requested_delivery_date",
        "buyer_name",
        "buyer_contact_email",
        "ship_to_address",
        "payment_terms",
        "order_total",
        "currency",
        "notes",
    }
)

EDITABLE_LINE_FIELDS: frozenset[str] = frozenset(
    {
        "sku",
        "description",
        "unit",
        "quantity",
        "unit_price",
        "line_total",
    }
)

# The header/line columns whose values are money or quantities. They reach
# Postgres as strings so the NUMERIC column, not a float, decides precision.
_NUMERIC_HEADER_FIELDS: frozenset[str] = frozenset({"order_total"})
_NUMERIC_LINE_FIELDS: frozenset[str] = frozenset({"quantity", "unit_price", "line_total"})

# The statuses a document can be reviewed from. `approved` is deliberately
# absent -- approving twice is REV-002, and editing an approved document goes
# through the reopen path rather than being treated as an ordinary edit.
REVIEWABLE_STATUSES: frozenset[str] = frozenset({"needs_review"})

# The provenance prefix `matching._human_edited` looks for. Defined there and
# re-stated here rather than imported, because a change to either one has to
# be a deliberate change to both -- there is a test that asserts they agree.
PROVENANCE_HUMAN = "human_edit"


class ReviewError(Exception):
    """
    A user-facing review failure, carrying a catalog code.

    Every raise site in this module names a `REV-0xx` code, so the API layer
    renders the what / why / what-next from the catalog and never invents a
    string of its own (Section 7.16.5).
    """

    def __init__(self, code: str, detail: dict[str, Any] | None = None):
        self.code = code
        self.detail = detail or {}
        entry = get_error(code)
        super().__init__(f"{code}: {entry.title}")


# ── Pure layer ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FieldChange:
    """
    One `{field, before, after}` entry, exactly as Section 9 specifies.

    `before` and `after` are strings or None -- never numbers. A `before` that
    round-tripped through a JSON float would make the audit trail disagree
    with the document it exists to prove something about.
    """

    field: str
    before: str | None
    after: str | None
    line_number: int | None = None

    def as_json(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "field": self.field,
            "before": self.before,
            "after": self.after,
        }
        if self.line_number is not None:
            entry["line_number"] = self.line_number
        return entry


@dataclass(frozen=True)
class WarningAcknowledgement:
    """
    Section 7.3: the acknowledgement is recorded "with the warning text".

    The text is copied in, not referenced. If the catalog's wording changes
    next year, this row must still say what the human actually agreed to at
    the time -- a pointer would silently rewrite the past.
    """

    warning_id: UUID
    code: str
    text: str
    note: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "warning_id": str(self.warning_id),
            "code": self.code,
            "text": self.text,
            "note": self.note,
        }


@dataclass
class EditRequest:
    """A single save from the review screen: header edits, line edits, or both."""

    header: dict[str, str | None] = field(default_factory=dict)
    # line id -> {field: new value}
    lines: dict[UUID, dict[str, str | None]] = field(default_factory=dict)


def validate_editable(request: EditRequest) -> None:
    """
    Reject any field outside the allowlist before a single row is read.

    Pure, so the API layer can reject a malformed request without opening a
    transaction, and so the allowlist is testable without a database.
    """
    for name in request.header:
        if name not in EDITABLE_HEADER_FIELDS:
            raise ReviewError(CODE_FIELD_NOT_EDITABLE, {"field": name, "scope": "header"})
    for line_id, fields in request.lines.items():
        for name in fields:
            if name not in EDITABLE_LINE_FIELDS:
                raise ReviewError(
                    CODE_FIELD_NOT_EDITABLE,
                    {"field": name, "scope": "line", "line_id": str(line_id)},
                )


def _as_text(value: Any) -> str | None:
    """
    Every value reaches the audit trail and the snapshot as a string.

    `Decimal("47.50")` becomes "47.50" and never 47.5 -- Section 7.1's "no
    float ever touches money", applied to the two places a number is most
    likely to be quietly reformatted on its way through JSON.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def diff_fields(
    before: dict[str, Any], after: dict[str, str | None], *, line_number: int | None = None
) -> list[FieldChange]:
    """
    The changes a save actually makes, ignoring the ones it doesn't.

    A reviewer who opens a field, retypes the same value and saves has not
    edited the document, and recording that as an `edited` action would make
    7.15.3's zero-edit-approvals KPI measure typing rather than extraction
    quality.
    """
    changes: list[FieldChange] = []
    for name, new_value in after.items():
        old_value = _as_text(before.get(name))
        new_text = _as_text(new_value)
        if old_value != new_text:
            changes.append(
                FieldChange(
                    field=name, before=old_value, after=new_text, line_number=line_number
                )
            )
    return changes


def canonical_snapshot_json(snapshot: dict[str, Any]) -> str:
    """
    The one rendering a snapshot hash is computed over.

    `sort_keys` and a fixed separator, so the same approved data always
    produces the same bytes. Section 7.4 requires exports to be deterministic
    ("same snapshot -> byte-identical file") and to record "the snapshot hash
    it was built from"; neither is meaningful if the hash depends on Python's
    dict ordering.
    """
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def snapshot_sha256(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_snapshot_json(snapshot).encode("utf-8")).hexdigest()


# ── Database layer (tenant-scoped session required) ─────────────────────────


def start_review(session: Session, document_id: UUID) -> None:
    """
    Stamp `review_started_at` the FIRST time a reviewer opens the document.

    Never updated afterwards. 7.15.3 measures review time as
    `approved_at - review_started_at`, so a reviewer who opens a document,
    goes to lunch and reopens it must not have the clock reset -- that would
    turn a slow review into a fast one in the KPI. `WHERE review_started_at
    IS NULL` is what enforces it, rather than the caller remembering to check.

    Idempotent by construction: calling it on every page load is correct.
    """
    session.execute(
        text(
            "UPDATE documents SET review_started_at = now() "
            "WHERE id = :document_id AND review_started_at IS NULL AND deleted_at IS NULL"
        ),
        {"document_id": str(document_id)},
    )


def _load_document(session: Session, document_id: UUID) -> dict[str, Any] | None:
    row = session.execute(
        text(
            "SELECT id, tenant_id, status, approved_json, approved_at, approved_by, "
            "approved_snapshot_hash, review_started_at "
            "FROM documents WHERE id = :document_id AND deleted_at IS NULL"
        ),
        {"document_id": str(document_id)},
    ).mappings().first()
    return dict(row) if row else None


def _load_header(session: Session, document_id: UUID) -> dict[str, Any]:
    row = session.execute(
        text(
            "SELECT po_number, order_date, requested_delivery_date, buyer_name, "
            "buyer_contact_email, ship_to_address, payment_terms, order_total, currency, "
            "notes, buyer_id, header_confidence, currency_inferred, field_provenance "
            "FROM document_headers WHERE document_id = :document_id AND deleted_at IS NULL"
        ),
        {"document_id": str(document_id)},
    ).mappings().first()
    return dict(row) if row else {}


def _load_lines(session: Session, document_id: UUID) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            "SELECT id, line_number, sku, description, unit, quantity, unit_price, "
            "line_total, confidence, matched_item_id, match_method, match_score, "
            "matched_uom, uom_mismatch, field_provenance "
            "FROM document_lines WHERE document_id = :document_id AND deleted_at IS NULL "
            "ORDER BY line_number"
        ),
        {"document_id": str(document_id)},
    ).mappings().all()
    return [dict(row) for row in rows]


def _record_action(
    session: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    user_id: UUID,
    action: str,
    changes: list[FieldChange] | None = None,
    acknowledgements: list[WarningAcknowledgement] | None = None,
    note: str | None = None,
    acting_as_tenant_id: UUID | None = None,
) -> UUID:
    """
    Write one `review_actions` row and return its id.

    The single writer of that table. Every caller in this module goes through
    it, so "every human action leaves a row" is one function's responsibility
    rather than four callers' discipline.
    """
    action_id = uuid4()
    session.execute(
        text(
            """
            INSERT INTO review_actions
                (id, tenant_id, document_id, user_id, acting_as_tenant_id, action,
                 changes, warning_acknowledgements, note, created_at)
            VALUES
                (:id, :tenant_id, :document_id, :user_id, :acting_as_tenant_id, :action,
                 :changes, :warning_acknowledgements, :note, now())
            """
        ),
        {
            "id": str(action_id),
            "tenant_id": str(tenant_id),
            "document_id": str(document_id),
            "user_id": str(user_id),
            "acting_as_tenant_id": str(acting_as_tenant_id) if acting_as_tenant_id else None,
            "action": action,
            "changes": [c.as_json() for c in (changes or [])],
            "warning_acknowledgements": [a.as_json() for a in (acknowledgements or [])],
            "note": note,
        },
    )
    return action_id


def apply_edits(
    session: Session,
    tenant_id: UUID,
    document_id: UUID,
    *,
    user_id: UUID,
    request: EditRequest,
    acting_as_tenant_id: UUID | None = None,
    expected_version: str | None = None,
) -> UUID | None:
    """
    Apply a reviewer's corrections, recording before and after for each one.

    Returns the `review_actions` id, or None when nothing actually changed.

    Section 7.3 and Section 10 in one function:

      * every changed field produces a `{field, before, after}` entry;
      * every changed field gets `human_edit:<review_action_id>` provenance,
        which is what stops re-extraction and re-matching from ever
        overwriting it;
      * editing an `approved` document reverts it to `needs_review` and
        writes a `reopened` action. The old snapshot is superseded, never
        deleted.

    `session` must already be tenant-scoped.
    """
    validate_editable(request)

    document = _load_document(session, document_id)
    if document is None:
        raise ReviewError(CODE_NOT_REVIEWABLE, {"document_id": str(document_id)})

    status = document["status"]
    was_approved = status in ("approved", "exported")
    if not was_approved and status not in REVIEWABLE_STATUSES:
        raise ReviewError(CODE_NOT_REVIEWABLE, {"status": status})

    header_before = _load_header(session, document_id)
    lines_before = {row["id"]: row for row in _load_lines(session, document_id)}

    if expected_version is not None:
        current = _document_version(header_before, list(lines_before.values()))
        if current != expected_version:
            raise ReviewError(CODE_STALE_EDIT, {"expected": expected_version, "actual": current})

    header_changes = diff_fields(header_before, request.header)

    # Keyed by line id, not by line number. Two rows sharing a line number
    # would be a data defect rather than normal traffic, but resolving an edit
    # by a number the reviewer can also edit is the kind of thing that works
    # until the day it silently writes one line's correction onto another.
    line_changes: dict[UUID, list[FieldChange]] = {}
    for line_id, fields in request.lines.items():
        line = lines_before.get(line_id)
        if line is None:
            raise ReviewError(CODE_NOT_REVIEWABLE, {"line_id": str(line_id)})
        found = diff_fields(line, fields, line_number=line["line_number"])
        if found:
            line_changes[line_id] = found

    changes: list[FieldChange] = list(header_changes)
    for found in line_changes.values():
        changes.extend(found)

    if not changes:
        # A reviewer who retyped the same value has not edited the document.
        return None

    action_id = _record_action(
        session,
        tenant_id=tenant_id,
        document_id=document_id,
        user_id=user_id,
        action=ACTION_EDITED,
        changes=changes,
        acting_as_tenant_id=acting_as_tenant_id,
    )

    if header_changes:
        _write_header_edits(
            session,
            document_id,
            request.header,
            {c.field for c in header_changes},
            header_before,
            action_id,
        )

    for line_id, found in line_changes.items():
        _write_line_edits(
            session,
            line_id,
            request.lines[line_id],
            {c.field for c in found},
            lines_before[line_id],
            action_id,
        )

    if was_approved:
        _reopen(
            session,
            tenant_id=tenant_id,
            document_id=document_id,
            user_id=user_id,
            acting_as_tenant_id=acting_as_tenant_id,
        )

    return action_id


def _write_header_edits(
    session: Session,
    document_id: UUID,
    values: dict[str, str | None],
    changed: set[str],
    before: dict[str, Any],
    action_id: UUID,
) -> None:
    """
    Update only the fields that changed, and stamp each one's provenance.

    The SET clause is built from `EDITABLE_HEADER_FIELDS`, never from the
    request: `name` has already been checked against the allowlist, and the
    VALUE always travels as a bind parameter. No caller input is interpolated
    into SQL (Section 10).
    """
    provenance = dict(before.get("field_provenance") or {})
    assignments = []
    params: dict[str, Any] = {"document_id": str(document_id)}
    for name in sorted(changed):
        column = _checked_column(name, EDITABLE_HEADER_FIELDS)
        assignments.append(f"{column} = :{column}")
        params[column] = _bind_value(name, values[name], _NUMERIC_HEADER_FIELDS)
        provenance[name] = f"{PROVENANCE_HUMAN}:{action_id}"

    params["field_provenance"] = provenance
    session.execute(
        text(
            f"UPDATE document_headers SET {', '.join(assignments)}, "
            "field_provenance = :field_provenance, updated_at = now() "
            "WHERE document_id = :document_id"
        ),
        params,
    )


def _write_line_edits(
    session: Session,
    line_id: UUID,
    values: dict[str, str | None],
    changed: set[str],
    before: dict[str, Any],
    action_id: UUID,
) -> None:
    provenance = dict(before.get("field_provenance") or {})
    assignments = []
    params: dict[str, Any] = {"line_id": str(line_id)}
    for name in sorted(changed):
        column = _checked_column(name, EDITABLE_LINE_FIELDS)
        assignments.append(f"{column} = :{column}")
        params[column] = _bind_value(name, values[name], _NUMERIC_LINE_FIELDS)
        provenance[name] = f"{PROVENANCE_HUMAN}:{action_id}"

    params["field_provenance"] = provenance
    session.execute(
        text(
            f"UPDATE document_lines SET {', '.join(assignments)}, "
            "field_provenance = :field_provenance "
            "WHERE id = :line_id"
        ),
        params,
    )


def _checked_column(name: str, allowlist: frozenset[str]) -> str:
    """
    The last gate before a name reaches an SQL string.

    `validate_editable` has already rejected anything outside the allowlist,
    so this can only fire if someone later adds a code path that skips it.
    That is exactly when a belt-and-braces check earns its place.
    """
    if name not in allowlist:
        raise ReviewError(CODE_FIELD_NOT_EDITABLE, {"field": name})
    return name


def _bind_value(name: str, value: str | None, numeric_fields: frozenset[str]) -> Any:
    """
    Money and quantities reach a NUMERIC column as a string, never a float
    (Section 7.1). The driver binds the string exactly; a float would round.
    """
    if value is None:
        return None
    if name in numeric_fields:
        return str(value)
    return value


def _document_version(header: dict[str, Any], lines: list[dict[str, Any]]) -> str:
    """
    A hash of the editable state, used for optimistic concurrency.

    Two reviewers on one document is rare in a small distributor, but "rare"
    and "silently loses one person's correction" is a bad combination in a
    system whose product is data integrity. A stale save is REV-005, not an
    overwrite.
    """
    payload = {
        "header": {name: _as_text(header.get(name)) for name in sorted(EDITABLE_HEADER_FIELDS)},
        "lines": [
            {
                "id": str(line["id"]),
                **{name: _as_text(line.get(name)) for name in sorted(EDITABLE_LINE_FIELDS)},
            }
            for line in sorted(lines, key=lambda row: row["line_number"])
        ],
    }
    return hashlib.sha256(canonical_snapshot_json(payload).encode("utf-8")).hexdigest()


def document_version(session: Session, document_id: UUID) -> str:
    """The version token the review screen holds while a reviewer edits."""
    return _document_version(
        _load_header(session, document_id), _load_lines(session, document_id)
    )


def build_snapshot(session: Session, document_id: UUID) -> dict[str, Any]:
    """
    The complete approved header and lines, as a self-contained JSON document.

    Self-contained is the requirement, not a nicety: Section 7.3 says
    "exports are generated from the snapshot, never from the live tables", so
    an export built from this months later must not depend on the live rows
    still saying the same thing. Every value is a string.
    """
    header = _load_header(session, document_id)
    lines = _load_lines(session, document_id)

    return {
        "document_id": str(document_id),
        "header": {
            name: _as_text(header.get(name))
            for name in sorted(EDITABLE_HEADER_FIELDS)
        },
        "lines": [
            {
                "line_number": line["line_number"],
                **{name: _as_text(line.get(name)) for name in sorted(EDITABLE_LINE_FIELDS)},
                "matched_item_id": _as_text(line.get("matched_item_id")),
                "matched_uom": _as_text(line.get("matched_uom")),
            }
            for line in lines
        ],
    }


def unacknowledged_warnings(session: Session, document_id: UUID) -> list[dict[str, Any]]:
    """Every live warning on this document that nobody has acknowledged."""
    rows = session.execute(
        text(
            "SELECT id, code, severity, field_name, line_number, detail "
            "FROM document_warnings "
            "WHERE document_id = :document_id AND status = 'open' AND deleted_at IS NULL "
            "ORDER BY severity, code"
        ),
        {"document_id": str(document_id)},
    ).mappings().all()
    return [dict(row) for row in rows]


def approve_document(
    session: Session,
    tenant_id: UUID,
    document_id: UUID,
    *,
    user_id: UUID,
    acknowledgements: list[WarningAcknowledgement] | None = None,
    acting_as_tenant_id: UUID | None = None,
) -> UUID:
    """
    Approve a document and freeze its snapshot. Returns the review action id.

    There is no other function in DocFlow that sets `status = 'approved'`, and
    this one cannot be called without a `user_id` (Section 7.3). Everything
    happens in the caller's transaction, so a failure anywhere leaves the
    document unapproved rather than approved with no snapshot.

    Order matters and is deliberate:
      1. refuse if the document is not reviewable, or is already approved;
      2. refuse if any live warning is unacknowledged (Section 7.3);
      3. write the `approved` action, carrying the acknowledgement text;
      4. freeze the snapshot, superseding any previous one;
      5. mark the document approved, pointing at that snapshot.
    """
    document = _load_document(session, document_id)
    if document is None:
        raise ReviewError(CODE_NOT_REVIEWABLE, {"document_id": str(document_id)})
    if document["status"] in ("approved", "exported"):
        raise ReviewError(CODE_ALREADY_APPROVED, {"status": document["status"]})
    if document["status"] not in REVIEWABLE_STATUSES:
        raise ReviewError(CODE_NOT_REVIEWABLE, {"status": document["status"]})

    supplied = {a.warning_id for a in (acknowledgements or [])}
    outstanding = [w for w in unacknowledged_warnings(session, document_id) if w["id"] not in supplied]
    if outstanding:
        raise ReviewError(
            CODE_UNACKNOWLEDGED_WARNINGS,
            {"warning_ids": [str(w["id"]) for w in outstanding],
             "codes": sorted({w["code"] for w in outstanding})},
        )

    action_id = _record_action(
        session,
        tenant_id=tenant_id,
        document_id=document_id,
        user_id=user_id,
        action=ACTION_APPROVED,
        acknowledgements=acknowledgements,
        acting_as_tenant_id=acting_as_tenant_id,
    )

    for ack in acknowledgements or []:
        session.execute(
            text(
                "UPDATE document_warnings "
                "SET status = 'acknowledged', acknowledged_by = :user_id, "
                "    acknowledged_at = now(), acknowledgement_note = :note, "
                "    acknowledged_review_action_id = :action_id, updated_at = now() "
                "WHERE id = :warning_id AND document_id = :document_id"
            ),
            {
                "warning_id": str(ack.warning_id),
                "document_id": str(document_id),
                "user_id": str(user_id),
                "note": ack.note,
                "action_id": str(action_id),
            },
        )

    snapshot = build_snapshot(session, document_id)
    digest = snapshot_sha256(snapshot)

    # Supersede the previous snapshot before inserting the new one -- the
    # partial unique index in 0007 allows exactly one live snapshot per
    # document, so this ordering is what makes re-approval possible at all.
    session.execute(
        text(
            "UPDATE document_snapshots SET superseded_at = now() "
            "WHERE document_id = :document_id AND superseded_at IS NULL AND deleted_at IS NULL"
        ),
        {"document_id": str(document_id)},
    )
    session.execute(
        text(
            """
            INSERT INTO document_snapshots
                (id, tenant_id, document_id, review_action_id, snapshot, snapshot_sha256, created_at)
            VALUES
                (:id, :tenant_id, :document_id, :review_action_id, :snapshot, :snapshot_sha256, now())
            """
        ),
        {
            "id": str(uuid4()),
            "tenant_id": str(tenant_id),
            "document_id": str(document_id),
            "review_action_id": str(action_id),
            "snapshot": snapshot,
            "snapshot_sha256": digest,
        },
    )

    session.execute(
        text(
            "UPDATE documents "
            "SET status = 'approved', approved_at = now(), approved_by = :user_id, "
            "    approved_json = :snapshot, approved_snapshot_hash = :snapshot_sha256 "
            "WHERE id = :document_id"
        ),
        {
            "document_id": str(document_id),
            "user_id": str(user_id),
            "snapshot": snapshot,
            "snapshot_sha256": digest,
        },
    )
    return action_id


def reject_document(
    session: Session,
    tenant_id: UUID,
    document_id: UUID,
    *,
    user_id: UUID,
    note: str,
    acting_as_tenant_id: UUID | None = None,
) -> UUID:
    """
    Reject a document. Explicit, attributed, and reversible by re-review.

    A rejection freezes no snapshot and deletes nothing -- the document and
    everything extracted from it stay exactly where they are. `note` is
    required, because a rejection nobody can explain later is not an audit
    trail.
    """
    document = _load_document(session, document_id)
    if document is None:
        raise ReviewError(CODE_NOT_REVIEWABLE, {"document_id": str(document_id)})
    if document["status"] not in REVIEWABLE_STATUSES:
        raise ReviewError(CODE_NOT_REVIEWABLE, {"status": document["status"]})

    action_id = _record_action(
        session,
        tenant_id=tenant_id,
        document_id=document_id,
        user_id=user_id,
        action=ACTION_REJECTED,
        note=note,
        acting_as_tenant_id=acting_as_tenant_id,
    )
    session.execute(
        text("UPDATE documents SET status = 'rejected' WHERE id = :document_id"),
        {"document_id": str(document_id)},
    )
    return action_id


def _reopen(
    session: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    user_id: UUID,
    acting_as_tenant_id: UUID | None,
) -> UUID:
    """
    Section 7.3's revert: an edit after approval sends the document back to
    `needs_review` and requires a fresh approval.

    The previous snapshot is superseded and RETAINED -- an export generated
    from it must stay explicable. `approved_json` and the approval columns are
    cleared because the document is no longer approved, and 0007's
    `documents_approved_is_attributable` constraint would be satisfied either
    way; leaving stale approval metadata on a `needs_review` document is how
    a later reader concludes it was approved when it wasn't.
    """
    action_id = _record_action(
        session,
        tenant_id=tenant_id,
        document_id=document_id,
        user_id=user_id,
        action=ACTION_REOPENED,
        acting_as_tenant_id=acting_as_tenant_id,
    )
    session.execute(
        text(
            "UPDATE document_snapshots SET superseded_at = now() "
            "WHERE document_id = :document_id AND superseded_at IS NULL AND deleted_at IS NULL"
        ),
        {"document_id": str(document_id)},
    )
    session.execute(
        text(
            "UPDATE documents "
            "SET status = 'needs_review', approved_at = NULL, approved_by = NULL, "
            "    approved_json = NULL, approved_snapshot_hash = NULL "
            "WHERE id = :document_id"
        ),
        {"document_id": str(document_id)},
    )
    return action_id


def review_trail(session: Session, document_id: UUID) -> list[dict[str, Any]]:
    """
    Everything that happened to this document, oldest first.

    The Phase 3 exit criterion is "the audit trail shows exactly what
    changed", so this returns the `changes` array as stored rather than a
    summary of it.
    """
    rows = session.execute(
        text(
            "SELECT id, sequence, user_id, acting_as_tenant_id, action, changes, "
            "warning_acknowledgements, note, created_at "
            "FROM review_actions "
            "WHERE document_id = :document_id AND deleted_at IS NULL "
            # By `sequence`, never by `created_at`: an edit to an approved
            # document writes its `edited` and `reopened` rows in ONE
            # transaction, and `now()` is transaction start time, so both
            # carry the same timestamp. Ordering by the timestamp and a
            # random uuid rendered cause and effect in arbitrary order
            # (DECISIONS.md D-084).
            "ORDER BY sequence"
        ),
        {"document_id": str(document_id)},
    ).mappings().all()
    return [dict(row) for row in rows]


def current_snapshot(session: Session, document_id: UUID) -> dict[str, Any] | None:
    """The live approved snapshot, or None if the document is not approved."""
    row = session.execute(
        text(
            "SELECT id, snapshot, snapshot_sha256, review_action_id, created_at "
            "FROM document_snapshots "
            "WHERE document_id = :document_id AND superseded_at IS NULL AND deleted_at IS NULL"
        ),
        {"document_id": str(document_id)},
    ).mappings().first()
    return dict(row) if row else None
