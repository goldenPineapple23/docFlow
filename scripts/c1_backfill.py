"""
C1 staging backfill and report (Phase 5.5 Stage 1; DECISIONS.md D-149, D-154).

Before migration 0026, Postgres rounded document numbers on the way in
(`numeric(14,4)`, `numeric(14,2)`, `numeric(12,2)`) and padded the rest
("2" became 2.0000). This script compares every stored number with what the
model returned (`documents.raw_json`, immutable) and with what a reviewer typed
(`review_actions.changes`), and sorts every difference into one of four lists:

  RESTORE    never edited by a person, differs from raw_json, and the order is
             not approved or exported. `--apply` puts back exactly what the
             model returned and re-runs validation on that order.
  FROZEN     never edited, differs from raw_json, and the order is approved or
             exported. Listed only: the approved snapshot is immutable (Section
             7.3) and the founder reviews these by hand (founder decision
             2026-09-25). Their exports are listed too.
  TYPED      edited by a person, and the stored value differs from what they
             typed (the audit trail keeps the typed text). Listed only.
  (padding)  each list says whether the difference is only trailing zeros the
             old column added (47.50 stored as 47.5000 -- same number, not the
             printed text) or a real change of value (0.00345 -> 0.0035).

Run after migration 0026 is applied, from apps/api:
  .venv/Scripts/python.exe ../../scripts/c1_backfill.py            # report only
  .venv/Scripts/python.exe ../../scripts/c1_backfill.py --apply    # RESTORE list

Reads across tenants through the platform-admin session, like the other
maintenance scripts; writes only the RESTORE list's working-copy columns.
Staging holds test data only.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "core"))

from docflow_core.db import platform_session, tenant_session
from docflow_core.numbers import parse_document_number, plain
from docflow_core.validation import validate_document
from sqlalchemy import text

HEADER_FIELDS = ("order_total",)
LINE_FIELDS = ("quantity", "unit_price", "line_total")
DONE = ("approved", "exported")


@dataclass
class Finding:
    kind: str  # RESTORE | FROZEN | TYPED
    tenant: str
    document_id: str
    po_number: str | None
    status: str
    field: str
    line_number: int | None
    line_id: str | None
    stored: str | None
    original: str
    padding_only: bool


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _human(provenance: Any, name: str) -> bool:
    value = (_json(provenance) or {}).get(name)
    return isinstance(value, str) and value.startswith("human_edit")


def _typed_values(session: Any, document_id: str) -> dict[tuple[str, int | None], str | None]:
    """The last value a person typed for each number field, from the audit trail."""
    typed: dict[tuple[str, int | None], str | None] = {}
    rows = session.execute(
        text(
            "SELECT changes FROM review_actions WHERE document_id = :id AND action = 'edited' "
            "ORDER BY sequence"
        ),
        {"id": document_id},
    )
    for (changes,) in rows:
        for change in _json(changes) or []:
            name = change.get("field")
            if name in HEADER_FIELDS + LINE_FIELDS:
                line = change.get("line_number")
                typed[(name, int(line) if line is not None else None)] = change.get("after")
    return typed


def _compare(stored: Decimal | None, original: str) -> tuple[bool, bool]:
    """(differs, padding_only) between a stored value and the original text."""
    if stored is not None and plain(stored) == original:
        return False, False
    if stored is None:
        return True, False
    parsed = parse_document_number(original)
    return True, parsed is not None and parsed == stored


def collect() -> list[Finding]:
    findings: list[Finding] = []
    with platform_session() as session:
        documents = session.execute(
            text(
                """
                SELECT d.id, d.status, d.raw_json, t.name AS tenant, h.po_number,
                       h.order_total, h.field_provenance
                FROM documents d
                JOIN tenants t ON t.id = d.tenant_id
                JOIN document_headers h ON h.document_id = d.id
                WHERE d.raw_json ? 'header' AND d.deleted_at IS NULL
                ORDER BY t.name, d.created_at
                """
            )
        ).mappings().all()
        for doc in documents:
            document_id = str(doc["id"])
            raw = _json(doc["raw_json"]) or {}
            typed = _typed_values(session, document_id)
            lines = session.execute(
                text(
                    "SELECT id, line_number, quantity, unit_price, line_total, field_provenance "
                    "FROM document_lines WHERE document_id = :id AND deleted_at IS NULL"
                ),
                {"id": document_id},
            ).mappings().all()
            raw_lines = {item.get("line_number"): item for item in raw.get("line_items") or []}

            for name in HEADER_FIELDS:
                _consider(findings, doc, typed, name, doc[name], (raw.get("header") or {}).get(name),
                          doc["field_provenance"], None, None)
            for line in lines:
                item = raw_lines.get(line["line_number"]) or {}
                for name in LINE_FIELDS:
                    _consider(findings, doc, typed, name, line[name], item.get(name),
                              line["field_provenance"], line["line_number"], str(line["id"]))
    return findings


def _consider(findings: list[Finding], doc: Any, typed: dict, name: str, stored: Decimal | None,
              printed: Any, provenance: Any, line_number: int | None, line_id: str | None) -> None:
    if _human(provenance, name):
        original = typed.get((name, line_number))
        kind = "TYPED"
    else:
        original = printed
        kind = "FROZEN" if doc["status"] in DONE else "RESTORE"
    if not isinstance(original, str) or parse_document_number(original) is None:
        return  # nothing exact to compare with (M2 cases, missing values)
    differs, padding_only = _compare(stored, original)
    if differs:
        findings.append(
            Finding(kind, doc["tenant"], str(doc["id"]), doc["po_number"], doc["status"], name,
                    line_number, line_id, plain(stored) if stored is not None else None,
                    original, padding_only)
        )


def frozen_exports(document_ids: set[str]) -> list[dict[str, Any]]:
    if not document_ids:
        return []
    with platform_session() as session:
        rows = session.execute(
            text(
                "SELECT e.id, e.document_id, e.format, e.status, e.generated_at FROM exports e "
                "WHERE e.document_id = ANY(string_to_array(:ids, ',')::uuid[]) AND e.deleted_at IS NULL "
                "ORDER BY e.document_id, e.generated_at"
            ),
            {"ids": ",".join(sorted(document_ids))},
        ).mappings().all()
    return [dict(row) for row in rows]


def apply_restore(findings: list[Finding]) -> int:
    """Put back the model's own text for RESTORE findings, then re-validate."""
    by_document: dict[str, list[Finding]] = {}
    for finding in findings:
        if finding.kind == "RESTORE":
            by_document.setdefault(finding.document_id, []).append(finding)
    with platform_session() as session:
        tenants = dict(
            session.execute(
                text("SELECT id::text, tenant_id FROM documents WHERE id = ANY(string_to_array(:ids, ',')::uuid[])"),
                {"ids": ",".join(by_document) or "00000000-0000-0000-0000-000000000000"},
            ).all()
        )
    for document_id, items in by_document.items():
        tenant_id = tenants[document_id]
        with tenant_session(tenant_id) as session:
            for f in items:
                # Guarded: only while the order is still in review and the
                # field is still as extracted.
                if f.line_id is None:
                    session.execute(
                        text(
                            f"UPDATE document_headers SET {f.field} = :v WHERE document_id = :d "
                            "AND NOT coalesce(field_provenance->>:f, '') LIKE 'human_edit%' "
                            "AND (SELECT status FROM documents WHERE id = :d) NOT IN ('approved','exported')"
                        ),
                        {"v": f.original, "d": document_id, "f": f.field},
                    )
                else:
                    session.execute(
                        text(
                            f"UPDATE document_lines SET {f.field} = :v WHERE id = :l "
                            "AND NOT coalesce(field_provenance->>:f, '') LIKE 'human_edit%' "
                            "AND (SELECT status FROM documents WHERE id = :d) NOT IN ('approved','exported')"
                        ),
                        {"v": f.original, "l": f.line_id, "d": document_id, "f": f.field},
                    )
            validate_document(session, tenant_id, document_id)
    return sum(len(items) for items in by_document.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="restore the RESTORE list (writes)")
    args = parser.parse_args()

    findings = collect()
    for kind in ("RESTORE", "FROZEN", "TYPED"):
        chosen = [f for f in findings if f.kind == kind]
        changed = [f for f in chosen if not f.padding_only]
        print(f"\n== {kind}: {len(chosen)} values ({len(changed)} changed value, "
              f"{len(chosen) - len(changed)} padding only) in "
              f"{len({f.document_id for f in chosen})} orders")
        for f in chosen:
            where = f"line {f.line_number} {f.field}" if f.line_number is not None else f.field
            tag = "padding" if f.padding_only else "VALUE CHANGED"
            print(f"  {f.tenant} | PO {f.po_number} | {f.status} | {f.document_id} | {where}: "
                  f"stored {f.stored!r}, original {f.original!r} [{tag}]")
    with platform_session() as session:
        unchecked = session.execute(
            text(
                "SELECT count(*) FROM documents WHERE deleted_at IS NULL "
                "AND NOT coalesce(raw_json ? 'header', false)"
            )
        ).scalar_one()
    print(f"\n== Not checkable: {unchecked} orders have no model answer in raw_json (seeded "
          "directly, or failed before extraction), so there is no printed value to compare with")
    exported = frozen_exports({f.document_id for f in findings if f.kind == "FROZEN"})
    print(f"\n== Exports made from FROZEN orders' snapshots: {len(exported)}")
    for row in exported:
        print(f"  {row['document_id']} | {row['format']} | {row['status']} | {row['generated_at']}")

    if args.apply:
        restored = apply_restore(findings)
        print(f"\nRestored {restored} values from raw_json and re-validated their orders.")
    else:
        print("\nReport only. Run with --apply to restore the RESTORE list.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
