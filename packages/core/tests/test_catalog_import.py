"""
Catalog and customer-list import, the parts that need no database
(Section 7.15.2 Steps 4-5; D-108): guessing the columns, cleaning values,
and the validation report -- with row numbers that match the
spreadsheet the founder has open.

Reading the file moved to the parse service in Stage 3c, with its tests
(apps/parse/tests/test_tables.py). The diff and commit, which read and write the tenant's catalog, are tested
against the real database in apps/api/tests/test_catalog_import_api.py.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from docflow_core import catalog_import as ci

# ── Guessing the columns ────────────────────────────────────────────────────


def test_common_headers_map_themselves():
    mapping = ci.auto_map("catalog", ["Item #", "Item Description", "U/M", "UPC", "Notes"])
    assert mapping == {"sku": 0, "description": 1, "unit_of_measure": 2, "barcode": 3, "external_id": None}


def test_a_saved_template_wins_and_survives_reordered_columns():
    template = {"sku": "Stock Ref", "description": "Wording"}
    mapping = ci.auto_map("catalog", ["Wording", "Other", "Stock Ref"], template)
    assert mapping["sku"] == 2 and mapping["description"] == 0


def test_a_column_is_never_mapped_twice():
    mapping = ci.auto_map("catalog", ["Name"])
    assert list(mapping.values()).count(0) == 1


def test_missing_required_columns_are_named():
    assert ci.missing_required("catalog", {"sku": 0, "description": None}) == ["description"]
    assert ci.missing_required("buyers", {"name": 0}) == []


# ── Cleaning ────────────────────────────────────────────────────────────────


def test_invisible_characters_and_edge_spaces_are_removed_and_reported():
    assert ci.clean(" TEST-1 ") == ("TEST-1", True)
    assert ci.clean("TEST" + chr(0x200B) + "-1") == ("TEST-1", True)
    assert ci.clean(chr(0xA0) + "TEST-1") == ("TEST-1", True)
    assert ci.clean("TEST-1") == ("TEST-1", False)
    assert ci.clean("   ") == (None, True)


# ── The validation report ───────────────────────────────────────────────────

COLUMNS = ["SKU", "Description", "UOM"]
MAPPING = {"sku": 0, "description": 1, "unit_of_measure": 2, "barcode": None, "external_id": None}


def _report(rows, overrides=None, header_row_number=1, kind="catalog", columns=COLUMNS, mapping=MAPPING):
    records, cleaned = ci.build_records(kind, columns, rows, header_row_number, mapping, overrides or {})
    return {f.code: f for f in ci.validate(kind, records, cleaned)}, records


def test_a_clean_catalog_has_no_findings():
    findings, records = _report([["TEST-1", "Beans", "CS"], ["TEST-2", "Cups", "BOX"]])
    assert findings == {}
    assert [r.values["sku"] for r in records] == ["TEST-1", "TEST-2"]


def test_blank_and_duplicate_skus_block_with_spreadsheet_row_numbers():
    # Header on row 3; data starts at row 4; row 6 is blank and skipped.
    rows = [["TEST-1", "Beans", ""], ["", "Mystery", ""], ["", "", ""], ["TEST-1", "Beans again", ""]]
    findings, _ = _report(rows, header_row_number=3)
    assert findings["CAT-001"].severity == "blocker" and findings["CAT-001"].rows == [5]
    assert findings["CAT-002"].severity == "blocker" and sorted(findings["CAT-002"].rows) == [4, 7]


def test_same_description_under_different_skus_is_only_a_warning():
    findings, _ = _report([["TEST-1", "Test Beans 5lb", ""], ["TEST-2", "test  beans 5LB", ""]])
    assert findings["CAT-003"].severity == "warning"
    assert sorted(findings["CAT-003"].rows) == [2, 3]
    assert "CAT-002" not in findings


def test_cleaned_skus_are_reported_as_information_and_then_compared_cleaned():
    findings, records = _report([[" TEST-1", "Beans", ""], ["TEST-1" + chr(0x200B), "Beans 2", ""]])
    assert findings["CAT-004"].severity == "info"
    # After cleaning they are the same SKU: that is a real duplicate.
    assert findings["CAT-002"].severity == "blocker"
    assert records[0].values["sku"] == "TEST-1"


def test_over_long_values_block():
    findings, _ = _report([["X" * 65, "Beans", ""]])
    assert findings["CAT-005"].severity == "blocker" and findings["CAT-005"].field == "sku"


def test_a_blank_description_is_a_warning_not_a_blocker():
    findings, _ = _report([["TEST-1", "", "CS"]])
    assert findings["CAT-006"].severity == "warning"
    assert not any(f.severity == "blocker" for f in findings.values())


def test_an_inline_fix_clears_the_blocker_it_was_for():
    rows = [["", "Mystery", ""]]
    findings, _ = _report(rows)
    assert "CAT-001" in findings
    findings, records = _report(rows, overrides={"2": {"sku": "TEST-9"}})
    assert "CAT-001" not in findings
    assert records[0].values["sku"] == "TEST-9"


def test_an_unmapped_unit_stays_empty_rather_than_guessed():
    mapping = {**MAPPING, "unit_of_measure": None}
    _, records = _report([["TEST-1", "Beans", "CS"]], mapping=mapping)
    assert records[0].values["unit_of_measure"] is None


# ── Customer lists ──────────────────────────────────────────────────────────

BUYER_COLUMNS = ["Customer", "Account", "Email"]
BUYER_MAPPING = {"name": 0, "external_account_number": 1, "contact_email": 2}


def test_customer_list_checks():
    rows = [
        ["Acme Test Cafe", "A-1", "orders@example.com"],
        ["ACME TEST CAFE.", "A-2", "not-an-email"],
        ["", "A-3", ""],
        ["Beacon Test Bistro", "A-1", ""],
    ]
    findings, _ = _report(rows, kind="buyers", columns=BUYER_COLUMNS, mapping=BUYER_MAPPING)
    assert sorted(findings["BUY-002"].rows) == [2, 3]  # same name, normalized
    assert findings["BUY-001"].rows == [4]
    assert sorted(findings["BUY-004"].rows) == [2, 5]  # account A-1 twice
    assert findings["BUY-006"].severity == "warning" and findings["BUY-006"].rows == [3]


def test_every_code_the_import_can_report_is_in_the_catalog():
    import inspect
    import re
    from pathlib import Path

    from docflow_core.errors import CATALOG

    # The table reader moved to the parse service in Stage 3c; its codes
    # still have to be in this catalog, so its source is read by path.
    parse_service = Path(__file__).resolve().parents[3] / "apps" / "parse" / "parse_service"
    tables = parse_service / "parsing" / "tables.py"
    source = inspect.getsource(ci) + tables.read_text(encoding="utf-8")
    for code in set(re.findall(r'"((?:CAT|BUY|IMP)-\d{3})"', source)):
        assert code in CATALOG, code
