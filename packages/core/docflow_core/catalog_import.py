"""
Catalog and customer-list import (CLAUDE.md Section 7.15.2 Steps 4-5;
DECISIONS.md D-108; migration 0012).

"Same upload component, parser, and validator as any catalog upload -- there
is only one." This is the one. The flow:

  upload -> parse (worker: `catalog_parsing`, the only code that opens the
  file) -> column mapping (auto-detected, adjustable, remembered per tenant)
  -> validation report -> inline fixes -> diff -> commit.

Everything after parsing is here, on the stored text table, so the API can
do it without ever touching the file (Section 7.11). The preview and the
commit call the SAME `evaluate` function: what the founder was shown is what
gets committed, recomputed at commit time against the catalog as it is then.

Hard rules, each with a test:
  * nothing is committed while a blocker remains;
  * re-uploads are diffs: a SKU missing from the new file is RETIRED (soft
    delete, `retired_by_import_id`), never hard-deleted, and a retiring SKU
    still used by an active learned rule is flagged before commit;
  * buyers are never retired by a customer-list import (they are created
    from POs too) and near-duplicates are flagged for the founder, never
    merged (Section 7.6);
  * nothing is guessed: an unmapped unit of measure stays empty rather than
    taking the column's 'EA' default (Section 0 rule 4).

Report entries are catalog codes (CAT-0xx, BUY-0xx) with row numbers that
match the founder's spreadsheet; failures are IMP-0xx.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core.buyers import find_near_duplicate_candidates, normalize_buyer_name

KINDS = ("catalog", "buyers")

# Formats a catalog or customer list may arrive in. Anything else on the
# intake allowlist (a PDF, an image) is refused with IMP-001: a catalog has to
# be a table. One list, used by the API's upload check and the worker's parser.
TABLE_FORMATS = frozenset({"csv", "xlsx", "xlsm", "xls", "txt"})
PREVIEW_ROWS = 20
MAX_REPORTED_ROWS = 50


class CatalogImportError(Exception):
    """An import request that cannot be carried out. Carries an IMP-0xx code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ── What each kind of file must provide ─────────────────────────────────────


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    required: bool
    max_length: int
    synonyms: tuple[str, ...]


FIELDS: dict[str, tuple[FieldSpec, ...]] = {
    "catalog": (
        FieldSpec("sku", "SKU", True, 64,
                  ("sku", "item", "item number", "item no", "item #", "item code", "part",
                   "part number", "part no", "product code", "code", "stock code")),
        FieldSpec("description", "Description", True, 500,
                  ("description", "desc", "item description", "product description", "name",
                   "item name", "product", "product name")),
        FieldSpec("unit_of_measure", "Unit of measure", False, 20,
                  ("uom", "unit", "units", "unit of measure", "um", "u/m", "pack")),
        FieldSpec("barcode", "Barcode", False, 64, ("barcode", "upc", "ean", "gtin")),
        FieldSpec("external_id", "Their own ID", False, 64,
                  ("external id", "id", "erp id", "internal id", "record id")),
    ),
    "buyers": (
        FieldSpec("name", "Customer name", True, 200,
                  ("name", "customer", "customer name", "buyer", "buyer name", "company",
                   "company name", "account name")),
        FieldSpec("external_account_number", "Account number", False, 64,
                  ("account", "account number", "account no", "acct", "acct no",
                   "customer number", "customer no", "customer id", "id")),
        FieldSpec("contact_email", "Email", False, 254,
                  ("email", "e-mail", "email address", "contact email")),
    ),
}

KEY_FIELD = {"catalog": "sku", "buyers": "name"}


def _norm_header(value: str) -> str:
    value = value.casefold().replace("_", " ").replace(".", " ")
    value = re.sub(r"[^\w#/ -]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def auto_map(
    kind: str, columns: list[str], template: dict[str, str] | None = None
) -> dict[str, int | None]:
    """
    {field: column index | None}. A saved template (header TEXT per field)
    wins over guessing; otherwise each field takes the first column whose
    header is one of its synonyms. A column is never used twice.
    """
    normalized = [_norm_header(c) for c in columns]
    used: set[int] = set()
    mapping: dict[str, int | None] = {}
    for spec in FIELDS[kind]:
        chosen: int | None = None
        wanted = (template or {}).get(spec.name)
        if wanted:
            for i, header in enumerate(normalized):
                if i not in used and header == _norm_header(wanted):
                    chosen = i
                    break
        if chosen is None:
            for synonym in spec.synonyms:
                for i, header in enumerate(normalized):
                    if i not in used and header == synonym:
                        chosen = i
                        break
                if chosen is not None:
                    break
        mapping[spec.name] = chosen
        if chosen is not None:
            used.add(chosen)
    return mapping


def template_from_mapping(columns: list[str], mapping: dict[str, int | None]) -> dict[str, str]:
    return {f: columns[i] for f, i in mapping.items() if i is not None and i < len(columns)}


# ── Cleaning ────────────────────────────────────────────────────────────────

# Zero-width and formatting characters that make two identical-looking SKUs
# different strings. Removed, and reported (CAT-004), never silently.
_INVISIBLE = re.compile("[\u00ad\u200b-\u200f\u2028-\u202f\u2060-\u206f\ufeff]")


def clean(value: str | None) -> tuple[str | None, bool]:
    """(cleaned value or None if empty, whether anything was changed)."""
    if value is None:
        return None, False
    cleaned = _INVISIBLE.sub("", value).replace("\u00a0", " ").strip()
    return (cleaned or None), cleaned != value


# ── Records, findings, report ───────────────────────────────────────────────


@dataclass
class Record:
    row_number: int
    values: dict[str, str | None]
    raw: dict[str, str]


@dataclass
class Finding:
    code: str
    severity: str  # 'blocker' | 'warning' | 'info'
    field: str | None
    rows: list[int] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)  # SKUs / names, for non-row findings

    def as_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "field": self.field,
            "count": len(self.rows) or len(self.keys),
            "rows": sorted(self.rows)[:MAX_REPORTED_ROWS],
            "keys": sorted(self.keys)[:MAX_REPORTED_ROWS],
        }


def build_records(
    kind: str,
    columns: list[str],
    rows: list[list[str]],
    header_row_number: int,
    mapping: dict[str, int | None],
    overrides: dict[str, dict[str, str]],
) -> tuple[list[Record], set[int]]:
    """Records for every non-blank row, and the row numbers whose key field
    had to be cleaned (for CAT-004 / BUY-007)."""
    records: list[Record] = []
    cleaned_rows: set[int] = set()
    for index, row in enumerate(rows):
        if not any(cell.strip() for cell in row):
            continue
        row_number = header_row_number + 1 + index
        raw = {columns[i]: row[i] for i in range(min(len(columns), len(row)))}
        values: dict[str, str | None] = {}
        fixes = overrides.get(str(row_number), {})
        for spec in FIELDS[kind]:
            if spec.name in fixes:
                source: str | None = fixes[spec.name]
            else:
                idx = mapping.get(spec.name)
                source = row[idx] if idx is not None and idx < len(row) else None
            value, changed = clean(source)
            if changed and spec.name == KEY_FIELD[kind] and value is not None:
                cleaned_rows.add(row_number)
            values[spec.name] = value
        records.append(Record(row_number=row_number, values=values, raw=raw))
    return records, cleaned_rows


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _norm_description(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def validate(kind: str, records: list[Record], cleaned_rows: set[int]) -> list[Finding]:
    """The file-internal checks. Checks against existing data are in the diff."""
    findings: dict[str, Finding] = {}

    def add(code: str, severity: str, field_name: str | None, row: int) -> None:
        findings.setdefault(code, Finding(code, severity, field_name)).rows.append(row)

    for spec in FIELDS[kind]:
        for record in records:
            value = record.values[spec.name]
            if value is not None and len(value) > spec.max_length:
                add("CAT-005" if kind == "catalog" else "BUY-005", "blocker", spec.name, record.row_number)

    if kind == "catalog":
        by_sku: dict[str, list[int]] = defaultdict(list)
        by_description: dict[str, set[str]] = defaultdict(set)
        description_rows: dict[str, list[int]] = defaultdict(list)
        for record in records:
            sku, description = record.values["sku"], record.values["description"]
            if sku is None:
                add("CAT-001", "blocker", "sku", record.row_number)
            else:
                by_sku[sku].append(record.row_number)
            if description is None:
                add("CAT-006", "warning", "description", record.row_number)
            elif sku is not None:
                key = _norm_description(description)
                by_description[key].add(sku)
                description_rows[key].append(record.row_number)
        for sku, row_numbers in by_sku.items():
            if len(row_numbers) > 1:
                for row in row_numbers:
                    add("CAT-002", "blocker", "sku", row)
        for key, skus in by_description.items():
            if len(skus) > 1:
                for row in description_rows[key]:
                    add("CAT-003", "warning", "description", row)
        for row in cleaned_rows:
            add("CAT-004", "info", "sku", row)
    else:
        by_name: dict[str, list[int]] = defaultdict(list)
        by_account: dict[str, list[int]] = defaultdict(list)
        for record in records:
            name = record.values["name"]
            if name is None:
                add("BUY-001", "blocker", "name", record.row_number)
            else:
                by_name[normalize_buyer_name(name)].append(record.row_number)
            account = record.values["external_account_number"]
            if account is not None:
                by_account[account].append(record.row_number)
            email = record.values["contact_email"]
            if email is not None and not _EMAIL.match(email):
                add("BUY-006", "warning", "contact_email", record.row_number)
        for row_numbers in by_name.values():
            if len(row_numbers) > 1:
                for row in row_numbers:
                    add("BUY-002", "blocker", "name", row)
        for row_numbers in by_account.values():
            if len(row_numbers) > 1:
                for row in row_numbers:
                    add("BUY-004", "blocker", "external_account_number", row)
        for row in cleaned_rows:
            add("BUY-007", "info", "name", row)

    return list(findings.values())


# ── Diff against what the tenant has now ────────────────────────────────────

CATALOG_COMPARED = ("description", "unit_of_measure", "barcode", "external_id")


@dataclass
class Diff:
    insert: list[Record] = field(default_factory=list)
    update: list[tuple[Record, str]] = field(default_factory=list)      # (record, item id)
    reinstate: list[tuple[Record, str]] = field(default_factory=list)   # (record, item id)
    unchanged: list[tuple[Record, str]] = field(default_factory=list)
    retire: list[tuple[str, str]] = field(default_factory=list)         # (sku, item id)
    near_duplicates: dict[int, list[Any]] = field(default_factory=dict)  # buyers: row -> candidates
    existing_buyer: dict[int, str] = field(default_factory=dict)         # buyers: row -> buyer id

    def counts(self) -> dict[str, int]:
        return {
            "insert": len(self.insert),
            "update": len(self.update),
            "reinstate": len(self.reinstate),
            "unchanged": len(self.unchanged),
            "retire": len(self.retire),
        }


def _catalog_diff(session: Session, records: list[Record]) -> tuple[Diff, list[Finding]]:
    rows = session.execute(
        text(
            "SELECT DISTINCT ON (sku) id, sku, description, unit_of_measure, barcode, external_id, "
            "deleted_at IS NULL AS live FROM items "
            "ORDER BY sku, (deleted_at IS NULL) DESC, deleted_at DESC NULLS FIRST"
        )
    ).mappings().all()
    live = {r["sku"]: r for r in rows if r["live"]}
    retired = {r["sku"]: r for r in rows if not r["live"]}

    diff = Diff()
    seen: set[str] = set()
    for record in records:
        sku = record.values["sku"]
        if sku is None or sku in seen:
            continue
        seen.add(sku)
        if sku in live:
            item = live[sku]
            changed = any((item[f] or None) != record.values[f] for f in CATALOG_COMPARED)
            (diff.update if changed else diff.unchanged).append((record, str(item["id"])))
        elif sku in retired:
            diff.reinstate.append((record, str(retired[sku]["id"])))
        else:
            diff.insert.append(record)
    diff.retire = [(sku, str(item["id"])) for sku, item in live.items() if sku not in seen]

    findings: list[Finding] = []
    if diff.retire:
        used = session.execute(
            text(
                "SELECT DISTINCT match_value->>'item_id' AS item_id FROM learned_rules "
                "WHERE rule_type = 'sku_mapping' AND status = 'active' AND deleted_at IS NULL "
                "AND match_value->>'item_id' = ANY(CAST(string_to_array(:ids, ',') AS text[]))"
            ),
            {"ids": ",".join(item_id for _, item_id in diff.retire)},
        ).scalars().all()
        if used:
            used_set = set(used)
            finding = Finding("CAT-007", "warning", "sku")
            finding.keys = [sku for sku, item_id in diff.retire if item_id in used_set]
            findings.append(finding)
    return diff, findings


def _buyers_diff(session: Session, records: list[Record]) -> tuple[Diff, list[Finding]]:
    rows = session.execute(
        text("SELECT id, name, normalized_name FROM buyers WHERE deleted_at IS NULL")
    ).mappings().all()
    by_normalized = {r["normalized_name"]: r for r in rows}
    existing_names = [(r["id"], r["name"]) for r in rows]

    diff = Diff()
    near = Finding("BUY-003", "warning", "name")
    for record in records:
        name = record.values["name"]
        if name is None:
            continue
        existing = by_normalized.get(normalize_buyer_name(name))
        if existing is not None:
            diff.existing_buyer[record.row_number] = str(existing["id"])
            diff.update.append((record, str(existing["id"])))
            continue
        diff.insert.append(record)
        candidates = find_near_duplicate_candidates(name, existing_names)
        if candidates:
            diff.near_duplicates[record.row_number] = candidates
            near.rows.append(record.row_number)
    return diff, ([near] if near.rows else [])


# ── Evaluation: the one computation both preview and commit use ─────────────


@dataclass
class Evaluation:
    records: list[Record]
    findings: list[Finding]
    diff: Diff

    @property
    def can_commit(self) -> bool:
        return not any(f.severity == "blocker" for f in self.findings)

    def report(self) -> list[dict[str, Any]]:
        order = {"blocker": 0, "warning": 1, "info": 2}
        return [f.as_json() for f in sorted(self.findings, key=lambda f: (order[f.severity], f.code))]


def missing_required(kind: str, mapping: dict[str, int | None]) -> list[str]:
    return [s.name for s in FIELDS[kind] if s.required and mapping.get(s.name) is None]


def evaluate(session: Session, row: dict[str, Any]) -> Evaluation:
    kind = row["kind"]
    mapping = {k: v for k, v in (row["mapping"] or {}).items()}
    if missing_required(kind, mapping):
        raise CatalogImportError("IMP-007")
    records, cleaned_rows = build_records(
        kind,
        row["columns"],
        row["rows"],
        row["header_row_number"],
        mapping,
        row["overrides"] or {},
    )
    findings = validate(kind, records, cleaned_rows)
    diff, diff_findings = (_catalog_diff if kind == "catalog" else _buyers_diff)(session, records)
    return Evaluation(records=records, findings=findings + diff_findings, diff=diff)


# ── Database: the import's life (tenant-scoped session required) ────────────

_IMPORT_COLUMNS = (
    "id, tenant_id, kind, status, source, intake_file_id, original_filename, storage_path, "
    "file_sha256, file_type, error_code, columns, rows, row_count, header_row_number, mapping, "
    "overrides, summary, created_by, acting_as_tenant_id, created_at, parsed_at, committed_by, "
    "committed_at"
)


def create_import(
    session: Session,
    tenant_id: UUID,
    *,
    kind: str,
    source: str,
    original_filename: str,
    storage_path: str,
    file_sha256: str,
    file_type: str,
    created_by: UUID,
    intake_file_id: UUID | None = None,
    acting_as_tenant_id: UUID | None = None,
) -> UUID:
    if kind not in KINDS:
        raise ValueError(kind)
    import_id = uuid4()
    session.execute(
        text(
            """
            INSERT INTO catalog_imports
                (id, tenant_id, kind, status, source, intake_file_id, original_filename,
                 storage_path, file_sha256, file_type, created_by, acting_as_tenant_id)
            VALUES
                (:id, :tenant_id, :kind, 'parsing', :source, :intake_file_id, :original_filename,
                 :storage_path, :file_sha256, :file_type, :created_by, :acting_as)
            """
        ),
        {
            "id": str(import_id),
            "tenant_id": str(tenant_id),
            "kind": kind,
            "source": source,
            "intake_file_id": str(intake_file_id) if intake_file_id else None,
            "original_filename": original_filename,
            "storage_path": storage_path,
            "file_sha256": file_sha256,
            "file_type": file_type,
            "created_by": str(created_by),
            "acting_as": str(acting_as_tenant_id) if acting_as_tenant_id else None,
        },
    )
    return import_id


def get_import(session: Session, import_id: UUID, *, lock: bool = False) -> dict[str, Any] | None:
    row = session.execute(
        text(
            f"SELECT {_IMPORT_COLUMNS} FROM catalog_imports WHERE id = :id AND deleted_at IS NULL"
            + (" FOR UPDATE" if lock else "")
        ),
        {"id": str(import_id)},
    ).mappings().first()
    return dict(row) if row else None


def list_imports(session: Session, kind: str) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            "SELECT id, kind, status, original_filename, file_type, row_count, error_code, "
            "summary, created_at, committed_at FROM catalog_imports "
            "WHERE kind = :kind AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 50"
        ),
        {"kind": kind},
    ).mappings().all()
    return [dict(r) for r in rows]


def load_template(session: Session, kind: str) -> dict[str, str] | None:
    return session.execute(
        text("SELECT mapping FROM import_mapping_templates WHERE kind = :kind"), {"kind": kind}
    ).scalar()


def record_parsed(
    session: Session,
    import_id: UUID,
    *,
    columns: list[str],
    rows: list[list[str]],
    header_row_number: int,
    kind: str,
) -> None:
    """Worker: store the table and the auto-detected (or remembered) mapping."""
    mapping = auto_map(kind, columns, load_template(session, kind))
    session.execute(
        text(
            """
            UPDATE catalog_imports
            SET status = 'parsed', columns = CAST(:columns AS jsonb), rows = CAST(:rows AS jsonb),
                row_count = :row_count, header_row_number = :header_row_number,
                mapping = CAST(:mapping AS jsonb), parsed_at = now()
            WHERE id = :id AND status = 'parsing'
            """
        ),
        {
            "id": str(import_id),
            "columns": json.dumps(columns),
            "rows": json.dumps(rows),
            "row_count": len(rows),
            "header_row_number": header_row_number,
            "mapping": json.dumps(mapping),
        },
    )


def record_parse_failure(session: Session, import_id: UUID, code: str) -> None:
    session.execute(
        text(
            "UPDATE catalog_imports SET status = 'failed', error_code = :code "
            "WHERE id = :id AND status = 'parsing'"
        ),
        {"id": str(import_id), "code": code},
    )


def _require_parsed(session: Session, import_id: UUID) -> dict[str, Any]:
    row = get_import(session, import_id, lock=True)
    if row is None:
        raise LookupError(import_id)
    if row["status"] != "parsed":
        raise CatalogImportError("IMP-006")
    return row


def set_mapping(session: Session, import_id: UUID, mapping: dict[str, int | None]) -> None:
    row = _require_parsed(session, import_id)
    width = len(row["columns"])
    fields = {s.name for s in FIELDS[row["kind"]]}
    chosen = [i for i in mapping.values() if i is not None]
    if set(mapping) - fields or any(not 0 <= i < width for i in chosen) or len(chosen) != len(set(chosen)):
        raise CatalogImportError("IMP-008")
    session.execute(
        text("UPDATE catalog_imports SET mapping = CAST(:m AS jsonb) WHERE id = :id"),
        {"id": str(import_id), "m": json.dumps({f: mapping.get(f) for f in fields})},
    )


def set_override(
    session: Session, import_id: UUID, row_number: int, field_name: str, value: str | None
) -> None:
    """An inline fix: this row's value for this field, instead of the file's."""
    row = _require_parsed(session, import_id)
    if field_name not in {s.name for s in FIELDS[row["kind"]]}:
        raise CatalogImportError("IMP-008")
    first = row["header_row_number"] + 1
    if not first <= row_number < first + len(row["rows"]):
        raise CatalogImportError("IMP-008")
    overrides = dict(row["overrides"] or {})
    overrides.setdefault(str(row_number), {})[field_name] = value if value is not None else ""
    session.execute(
        text("UPDATE catalog_imports SET overrides = CAST(:o AS jsonb) WHERE id = :id"),
        {"id": str(import_id), "o": json.dumps(overrides)},
    )


def discard_import(session: Session, import_id: UUID) -> None:
    row = get_import(session, import_id, lock=True)
    if row is None:
        raise LookupError(import_id)
    if row["status"] in ("committed", "discarded"):
        raise CatalogImportError("IMP-006")
    session.execute(
        text("UPDATE catalog_imports SET status = 'discarded' WHERE id = :id"), {"id": str(import_id)}
    )


def preview(session: Session, import_id: UUID) -> dict[str, Any]:
    """Everything the import screen shows, from one `evaluate`."""
    row = get_import(session, import_id)
    if row is None:
        raise LookupError(import_id)
    base: dict[str, Any] = {
        "id": str(row["id"]),
        "kind": row["kind"],
        "status": row["status"],
        "original_filename": row["original_filename"],
        "error_code": row["error_code"],
        "summary": row["summary"],
        "fields": [
            {"name": s.name, "label": s.label, "required": s.required, "max_length": s.max_length}
            for s in FIELDS[row["kind"]]
        ],
    }
    if row["status"] != "parsed":
        return base
    base.update(
        {
            "columns": row["columns"],
            "row_count": row["row_count"],
            "mapping": row["mapping"],
            "overrides": row["overrides"] or {},
            "missing_required": missing_required(row["kind"], row["mapping"] or {}),
        }
    )
    if base["missing_required"]:
        base.update({"report": [], "diff": None, "can_commit": False, "preview": []})
        return base
    evaluation = evaluate(session, row)
    flagged = {r for f in evaluation.findings for r in f.rows}
    shown = [r for r in evaluation.records if r.row_number in flagged][:PREVIEW_ROWS]
    room = max(0, PREVIEW_ROWS - len(shown))
    shown += [r for r in evaluation.records if r.row_number not in flagged][:room]
    base.update(
        {
            "report": evaluation.report(),
            "diff": evaluation.diff.counts(),
            "retiring": [sku for sku, _ in evaluation.diff.retire][:MAX_REPORTED_ROWS],
            "can_commit": evaluation.can_commit,
            "preview": [
                {"row_number": r.row_number, "values": r.values}
                for r in sorted(shown, key=lambda r: r.row_number)
            ],
        }
    )
    return base


def commit_import(
    session: Session,
    tenant_id: UUID,
    import_id: UUID,
    *,
    user_id: UUID,
    acting_as_tenant_id: UUID | None = None,
) -> dict[str, Any]:
    """
    Apply the import in this transaction. Recomputes everything first -- the
    catalog may have changed since the preview -- and refuses while any
    blocker remains (IMP-005). Commits for one tenant are serialized on the
    tenant row, so two imports can never interleave their diffs.
    """
    session.execute(text("SELECT id FROM tenants WHERE id = :id FOR UPDATE"), {"id": str(tenant_id)})
    row = _require_parsed(session, import_id)
    evaluation = evaluate(session, row)
    if not evaluation.can_commit:
        raise CatalogImportError("IMP-005")
    diff = evaluation.diff
    iid = str(import_id)

    if row["kind"] == "catalog":
        for record in diff.insert:
            session.execute(
                text(
                    """
                    INSERT INTO items
                        (id, tenant_id, sku, description, unit_of_measure, barcode, external_id,
                         raw_data, last_import_id, created_at, updated_at)
                    VALUES
                        (:id, :tenant_id, :sku, :description, :uom, :barcode, :external_id,
                         CAST(:raw AS jsonb), :import_id, now(), now())
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "sku": record.values["sku"],
                    "description": record.values["description"],
                    # Explicit, even when None: the column's 'EA' default
                    # would be a guess the file never made.
                    "uom": record.values["unit_of_measure"],
                    "barcode": record.values["barcode"],
                    "external_id": record.values["external_id"],
                    "raw": json.dumps(record.raw),
                    "import_id": iid,
                },
            )
        for record, item_id in diff.update + diff.reinstate:
            session.execute(
                text(
                    """
                    UPDATE items
                    SET description = :description, unit_of_measure = :uom, barcode = :barcode,
                        external_id = :external_id, raw_data = CAST(:raw AS jsonb),
                        last_import_id = :import_id, deleted_at = NULL,
                        retired_by_import_id = NULL, updated_at = now()
                    WHERE id = :id
                    """
                ),
                {
                    "id": item_id,
                    "description": record.values["description"],
                    "uom": record.values["unit_of_measure"],
                    "barcode": record.values["barcode"],
                    "external_id": record.values["external_id"],
                    "raw": json.dumps(record.raw),
                    "import_id": iid,
                },
            )
        for _sku, item_id in diff.retire:
            session.execute(
                text(
                    "UPDATE items SET deleted_at = now(), retired_by_import_id = :import_id, "
                    "updated_at = now() WHERE id = :id AND deleted_at IS NULL"
                ),
                {"id": item_id, "import_id": iid},
            )
        # Section 7.15.2 Step 4: "Advance onboarding_status to catalog_loaded
        # on first commit." Only ever forward.
        advanced = session.execute(
            text(
                "UPDATE tenants SET onboarding_status = 'catalog_loaded', updated_at = now() "
                "WHERE id = :id AND onboarding_status = 'tenant_created'"
            ),
            {"id": str(tenant_id)},
        ).rowcount
        if advanced:
            session.execute(
                text(
                    """
                    INSERT INTO tenant_lifecycle_events
                        (id, tenant_id, event_type, actor_user_id, payload, created_at)
                    VALUES (:id, :tenant_id, 'onboarding_catalog_loaded', :actor, :payload, now())
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "actor": str(user_id),
                    "payload": json.dumps(
                        {
                            "import_id": iid,
                            "acting_as_tenant_id": str(acting_as_tenant_id) if acting_as_tenant_id else None,
                        }
                    ),
                },
            )
    else:
        from docflow_core.buyers import _flag_merge_candidates

        for record in diff.insert:
            buyer_id = uuid4()
            session.execute(
                text(
                    """
                    INSERT INTO buyers
                        (id, tenant_id, name, normalized_name, external_account_number,
                         contact_email, last_import_id, created_at, updated_at)
                    VALUES
                        (:id, :tenant_id, :name, :normalized, :account, :email, :import_id,
                         now(), now())
                    """
                ),
                {
                    "id": str(buyer_id),
                    "tenant_id": str(tenant_id),
                    "name": record.values["name"],
                    "normalized": normalize_buyer_name(record.values["name"]),
                    "account": record.values["external_account_number"],
                    "email": record.values["contact_email"],
                    "import_id": iid,
                },
            )
            # Flagged for the founder, never merged (Section 7.6).
            _flag_merge_candidates(
                session,
                tenant_id,
                buyer_id,
                diff.near_duplicates.get(record.row_number, []),
                document_id=None,
            )
        for record, buyer_id in diff.update:
            # Fill in what the list adds; never blank out what a buyer
            # record already has.
            session.execute(
                text(
                    """
                    UPDATE buyers
                    SET external_account_number = COALESCE(:account, external_account_number),
                        contact_email = COALESCE(:email, contact_email),
                        last_import_id = :import_id, updated_at = now()
                    WHERE id = :id
                    """
                ),
                {
                    "id": buyer_id,
                    "account": record.values["external_account_number"],
                    "email": record.values["contact_email"],
                    "import_id": iid,
                },
            )

    summary = {
        **diff.counts(),
        "rows": len(evaluation.records),
        "flags": [f.as_json() for f in evaluation.findings if f.severity != "info"],
        "mapping": template_from_mapping(row["columns"], row["mapping"]),
    }
    session.execute(
        text(
            """
            UPDATE catalog_imports
            SET status = 'committed', summary = CAST(:summary AS jsonb), committed_by = :user_id,
                committed_at = now()
            WHERE id = :id
            """
        ),
        {"id": iid, "summary": json.dumps(summary), "user_id": str(user_id)},
    )
    # Remembered for the next upload of this kind (Step 4: "saved as a
    # per-tenant mapping template for re-uploads").
    session.execute(
        text(
            """
            INSERT INTO import_mapping_templates (id, tenant_id, kind, mapping, updated_by, updated_at)
            VALUES (:id, :tenant_id, :kind, CAST(:mapping AS jsonb), :user_id, now())
            ON CONFLICT (tenant_id, kind)
            DO UPDATE SET mapping = EXCLUDED.mapping, updated_by = EXCLUDED.updated_by,
                          updated_at = now()
            """
        ),
        {
            "id": str(uuid4()),
            "tenant_id": str(tenant_id),
            "kind": row["kind"],
            "mapping": json.dumps(summary["mapping"]),
            "user_id": str(user_id),
        },
    )
    return summary


# ── Producing the table (worker) ────────────────────────────────────────────


def run_parse(tenant_id: UUID, import_id: UUID) -> str:
    """
    Read the stored file into the text table. Worker only: it imports the
    file reader lazily, so importing this module stays safe for the API
    (Section 7.11) -- the same arrangement as `export_jobs.run_export`.
    Re-validates the bytes first; never trusts that the upload-time check
    still describes what is on disk. Safe to run twice.
    """
    import logging

    from docflow_core import file_types
    from docflow_core.catalog_parsing import ImportParseError, parse_table
    from docflow_core.db import tenant_session
    from docflow_core.storage import read_file

    logger = logging.getLogger(__name__)
    with tenant_session(tenant_id) as session:
        row = get_import(session, import_id)
    if row is None or row["status"] != "parsing":
        return "skipped"

    content = read_file(row["storage_path"])
    validation = file_types.validate_upload(content, row["original_filename"])
    try:
        if not validation.ok or validation.file_type is None:
            raise ImportParseError("IMP-004", f"revalidation failed: {validation.error_code}")
        table = parse_table(content, validation.file_type.name.value)
    except ImportParseError as exc:
        # Codes and counts only -- never cell text (Section 7.10).
        logger.info("import_parse_failed import_id=%s code=%s reason=%s", import_id, exc.code, exc.detail)
        with tenant_session(tenant_id) as session:
            record_parse_failure(session, import_id, exc.code)
        return "failed"

    with tenant_session(tenant_id) as session:
        record_parsed(
            session,
            import_id,
            columns=table.columns,
            rows=table.rows,
            header_row_number=table.header_row_number,
            kind=row["kind"],
        )
    logger.info(
        "import_parsed import_id=%s rows=%d columns=%d", import_id, len(table.rows), len(table.columns)
    )
    return "parsed"
