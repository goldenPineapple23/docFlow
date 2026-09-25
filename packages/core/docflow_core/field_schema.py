"""
Per-tenant field schema (CLAUDE.md Section 7.13; D-120).

    "Per-tenant field schema, stored in the database and versioned. The
     extraction schema in Section 8.1 is the base; a tenant may mark fields
     required/optional/hidden ... The founder configures this during
     onboarding through the operator tooling; customers do not edit it.
     Every extraction logs the schema version it ran against."

What a state means -- and deliberately, what it does not:

  required  The order is checked for it (VAL-006), it counts towards the
            document's overall confidence, and review marks it with a *.
  optional  Extracted and shown, but absent is not a finding, and an empty
            optional field raises no low-confidence check (D-115).
  hidden    This tenant never sees it: not on the review screen, no checks,
            no effect on confidence. **Still extracted.** Hiding a field
            changes what DocFlow shows and checks, never what the model is
            asked for, so no schema or prompt hash changes and the golden set
            does not have to be re-run to hide a field. The value is stored
            as always, so un-hiding it later shows the data that was there.

Overall document confidence is the minimum over the *required* header fields
only (Section 7.1) -- so a tenant that doesn't care about payment terms is
not shown a 0% order because that box is empty.

Custom fields (`resin_grade`, `job_number`) are the other half of 7.13 and
are not built: they change the extraction request itself, which needs a live
golden-set run, and the shape should come from a real customer requirement
(D-120).

Every function takes an already-open tenant-scoped session (Section 7.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core.validation import (
    REQUIRED_HEADER_FIELDS,
    REQUIRED_LINE_FIELDS,
    FieldRules,
)

State = Literal["required", "optional", "hidden"]
STATES: tuple[State, ...] = ("required", "optional", "hidden")


class FieldSchemaError(Exception):
    """A refusal with an error-catalog code (FLD-0xx)."""

    def __init__(self, code: str, detail: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


# The fields a tenant can configure, in the order the review screen shows
# them, with the label that screen uses. `locked` fields cannot be made
# optional or hidden: without a PO number an order cannot be matched to
# anything a customer or their buyer will later ask about, and a line with no
# quantity is not an order line.
#
# Not configurable at all, and not listed here: the check that a line names
# *something* (a SKU or a description, validation.LINE_IDENTITY_FIELD). It is
# not a stored field but a rule about the pair, and a line that names nothing
# cannot be matched against the catalog for any tenant.
@dataclass(frozen=True)
class FieldDef:
    name: str
    label: str
    level: Literal["header", "line"]
    default: State
    locked: bool = False


def _default(name: str, required: tuple[str, ...]) -> State:
    return "required" if name in required else "optional"


HEADER_FIELDS: tuple[FieldDef, ...] = tuple(
    FieldDef(name, label, "header", _default(name, REQUIRED_HEADER_FIELDS), locked=name == "po_number")
    for name, label in (
        ("po_number", "PO number"),
        ("buyer_name", "Buyer"),
        ("order_date", "Order date"),
        ("requested_delivery_date", "Requested delivery"),
        ("order_total", "Order total"),
        ("currency", "Currency"),
        ("payment_terms", "Payment terms"),
        ("buyer_contact_email", "Buyer email"),
        ("ship_to_address", "Ship to"),
        ("notes", "Notes"),
    )
)

LINE_FIELDS: tuple[FieldDef, ...] = tuple(
    FieldDef(name, label, "line", _default(name, REQUIRED_LINE_FIELDS), locked=name == "quantity")
    for name, label in (
        ("sku", "SKU"),
        ("description", "Description"),
        ("quantity", "Quantity"),
        ("unit", "Unit"),
        ("unit_price", "Unit price"),
        ("line_total", "Line total"),
    )
)

_BY_LEVEL = {"header": HEADER_FIELDS, "line": LINE_FIELDS}


@dataclass(frozen=True)
class FieldSchema:
    """A tenant's configuration, resolved against the built-in defaults."""

    version: int  # 0 = no saved version; the defaults
    header: dict[str, State]
    line: dict[str, State]

    def state(self, level: str, name: str) -> State:
        return (self.header if level == "header" else self.line).get(name, "optional")

    def names(self, level: str, *states: State) -> tuple[str, ...]:
        source = self.header if level == "header" else self.line
        return tuple(name for name, state in source.items() if state in states)

    @property
    def required_header_fields(self) -> tuple[str, ...]:
        return self.names("header", "required")

    @property
    def required_line_fields(self) -> tuple[str, ...]:
        return self.names("line", "required")

    @property
    def hidden_header_fields(self) -> tuple[str, ...]:
        return self.names("header", "hidden")

    @property
    def hidden_line_fields(self) -> tuple[str, ...]:
        return self.names("line", "hidden")

    def overrides(self) -> dict[str, dict[str, State]]:
        """Only what differs from the defaults -- what gets stored, so a field
        added to DocFlow later starts at its own default for every tenant."""
        out: dict[str, dict[str, State]] = {}
        for level, defs in _BY_LEVEL.items():
            changed = {
                d.name: self.state(level, d.name) for d in defs if self.state(level, d.name) != d.default
            }
            if changed:
                out[level] = changed
        return out

    def rules(self) -> FieldRules:
        """What validation checks this tenant on (D-120)."""
        return FieldRules(
            required_header=self.required_header_fields,
            required_line=self.required_line_fields,
            hidden_header=self.hidden_header_fields,
            hidden_line=self.hidden_line_fields,
        )

    def overall_confidence(self, header_confidence: dict[str, Any]) -> Decimal:
        """
        Section 7.1: "Overall document confidence is the minimum of
        required-field confidences, not an average." Before D-120 every
        header field counted, so an order was shown as 0% confident because
        an optional box the document never had scored zero.

        No required field with a score -> 0, as before: an order nobody can
        score is not an order anyone should trust at a glance.
        """
        scores = [
            Decimal(str(value))
            for name, value in header_confidence.items()
            if name in self.header and self.header[name] == "required" and isinstance(value, (int, float))
        ]
        return min(scores) if scores else Decimal("0")

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "fields": [
                {
                    "name": d.name,
                    "label": d.label,
                    "level": level,
                    "state": self.state(level, d.name),
                    "default": d.default,
                    "locked": d.locked,
                }
                for level, defs in _BY_LEVEL.items()
                for d in defs
            ],
        }


DEFAULT_SCHEMA = FieldSchema(
    version=0,
    header={d.name: d.default for d in HEADER_FIELDS},
    line={d.name: d.default for d in LINE_FIELDS},
)


def _resolve(version: int, stored: dict[str, Any]) -> FieldSchema:
    header = dict(DEFAULT_SCHEMA.header)
    line = dict(DEFAULT_SCHEMA.line)
    for level, target in (("header", header), ("line", line)):
        for name, state in (stored.get(level) or {}).items():
            if name in target and state in STATES:
                target[name] = state
    # A locked field keeps its default whatever is stored: the guard lives
    # here too, not only at the point of saving, so an old row can never
    # switch one off.
    for level, defs in _BY_LEVEL.items():
        target = header if level == "header" else line
        for d in defs:
            if d.locked:
                target[d.name] = d.default
    return FieldSchema(version=version, header=header, line=line)


def current(session: Session, tenant_id: UUID) -> FieldSchema:
    """The tenant's current schema, or the built-in defaults (version 0)."""
    row = session.execute(
        text(
            "SELECT version, fields FROM tenant_field_schemas "
            "WHERE tenant_id = :t AND is_current ORDER BY version DESC LIMIT 1"
        ),
        {"t": str(tenant_id)},
    ).mappings().first()
    if row is None:
        return DEFAULT_SCHEMA
    return _resolve(int(row["version"]), row["fields"] or {})


def at_version(session: Session, tenant_id: UUID, version: int | None) -> FieldSchema:
    """The schema a document was read under. NULL/0 means the defaults."""
    if not version:
        return DEFAULT_SCHEMA
    row = session.execute(
        text("SELECT version, fields FROM tenant_field_schemas WHERE tenant_id = :t AND version = :v"),
        {"t": str(tenant_id), "v": version},
    ).mappings().first()
    return _resolve(int(row["version"]), row["fields"] or {}) if row else DEFAULT_SCHEMA


def save(
    session: Session,
    tenant_id: UUID,
    states: dict[str, dict[str, str]],
    *,
    actor_user_id: UUID,
    acting_as_tenant_id: UUID | None = None,
    note: str | None = None,
) -> FieldSchema:
    """
    Store a new version. Every save is a version, never an edit of the one in
    use, so a document can always be explained by the version it was read
    under (Section 7.13: versioned).
    """
    header = dict(DEFAULT_SCHEMA.header)
    line = dict(DEFAULT_SCHEMA.line)
    for level, target in (("header", header), ("line", line)):
        for name, state in (states.get(level) or {}).items():
            definition = next((d for d in _BY_LEVEL[level] if d.name == name), None)
            if definition is None:
                raise FieldSchemaError("FLD-001", {"field": name})
            if state not in STATES:
                raise FieldSchemaError("FLD-001", {"field": name})
            if definition.locked and state != definition.default:
                raise FieldSchemaError("FLD-002", {"field": definition.label})
            target[name] = state  # type: ignore[assignment]

    proposed = FieldSchema(version=0, header=header, line=line)
    next_version = (
        session.execute(
            text("SELECT coalesce(max(version), 0) + 1 FROM tenant_field_schemas WHERE tenant_id = :t"),
            {"t": str(tenant_id)},
        ).scalar_one()
    )
    session.execute(
        text("UPDATE tenant_field_schemas SET is_current = false WHERE tenant_id = :t AND is_current"),
        {"t": str(tenant_id)},
    )
    session.execute(
        text(
            """
            INSERT INTO tenant_field_schemas
                (id, tenant_id, version, fields, note, created_by, acting_as_tenant_id, is_current)
            VALUES (:id, :t, :v, :fields, :note, :actor, :acting_as, true)
            """
        ),
        {
            "id": str(uuid4()),
            "t": str(tenant_id),
            "v": next_version,
            "fields": proposed.overrides(),
            "note": (note or "").strip() or None,
            "actor": str(actor_user_id),
            "acting_as": str(acting_as_tenant_id) if acting_as_tenant_id else None,
        },
    )
    return FieldSchema(version=int(next_version), header=header, line=line)


def history(session: Session, tenant_id: UUID, limit: int = 20) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT s.version, s.fields, s.note, s.created_at, s.acting_as_tenant_id,
                   -- LEFT: the founder's own users row is outside the tenant (D-118).
                   u.email AS created_by_email
            FROM tenant_field_schemas s
            LEFT JOIN users u ON u.id = s.created_by
            WHERE s.tenant_id = :t
            ORDER BY s.version DESC
            LIMIT :limit
            """
        ),
        {"t": str(tenant_id), "limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


def recheck_open_documents(session: Session, tenant_id: UUID, schema: FieldSchema) -> int:
    """
    Re-score and re-check the orders that are still waiting for a person,
    against a newly saved version.

    Only `needs_review` documents: an approved order keeps the numbers it was
    approved with, and its snapshot is immutable (Section 7.3). Nothing here
    changes an extracted or edited *value* -- it recomputes the confidence
    summary and re-runs validation, which only writes `document_warnings`
    (Section 7.7). Returns how many were re-checked.
    """
    from docflow_core.validation import validate_document

    rows = session.execute(
        text(
            "SELECT d.id, h.header_confidence FROM documents d "
            "LEFT JOIN document_headers h ON h.document_id = d.id AND h.deleted_at IS NULL "
            "WHERE d.tenant_id = :t AND d.status = 'needs_review' AND d.deleted_at IS NULL"
        ),
        {"t": str(tenant_id)},
    ).mappings().all()
    rules = schema.rules()
    for row in rows:
        session.execute(
            text(
                "UPDATE documents SET overall_confidence = :c, field_schema_version = :v "
                "WHERE id = :id"
            ),
            {
                "id": str(row["id"]),
                "c": str(schema.overall_confidence(row["header_confidence"] or {})),
                "v": schema.version or None,
            },
        )
        validate_document(session, tenant_id, UUID(str(row["id"])), rules)
    return len(rows)
