"""
Catalog and customer-list import, the parts that need no database
(Section 7.15.2 Steps 4-5; D-108): reading the file, guessing the columns,
cleaning values, and the validation report -- with row numbers that match the
spreadsheet the founder has open.

The diff and commit, which read and write the tenant's catalog, are tested
against the real database in apps/api/tests/test_catalog_import_api.py.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import io

import pytest

from docflow_core import catalog_import as ci
from docflow_core import catalog_parsing as cp

# ── Reading the file ────────────────────────────────────────────────────────


def _xlsx(rows: list[list[object]]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_a_csv_with_a_byte_order_mark_reads_cleanly():
    table = cp.parse_table("﻿SKU,Description\nTEST-1,Test Beans\n".encode(), "csv")
    assert table.columns == ["SKU", "Description"]
    assert table.rows == [["TEST-1", "Test Beans"]]
    assert table.header_row_number == 1


def test_a_semicolon_csv_is_detected():
    table = cp.parse_table(b"sku;description\nTEST-1;Test Beans\n", "csv")
    assert table.rows == [["TEST-1", "Test Beans"]]


def test_excel_numbers_arrive_as_the_text_the_spreadsheet_shows():
    """A SKU of 1002 must not become "1002.0", nor a price 47.500000000000004."""
    table = cp.parse_table(_xlsx([["SKU", "Price"], [1002, 47.5], [7, 0.1 + 0.2]]), "xlsx")
    assert table.rows[0] == ["1002", "47.5"]
    assert table.rows[1][0] == "7"
    assert "e" not in table.rows[1][1].lower()


def test_row_numbers_survive_a_title_above_the_header_and_blank_rows():
    content = b"\n\nSKU,Description\nTEST-1,Beans\n\nTEST-2,Cups\n\n\n"
    table = cp.parse_table(content, "csv")
    assert table.header_row_number == 3
    # The blank row between the two items is kept, trailing blanks dropped.
    assert table.rows == [["TEST-1", "Beans"], ["", ""], ["TEST-2", "Cups"]]


def test_short_rows_are_padded_and_unnamed_trailing_columns_dropped():
    table = cp.parse_table(b"SKU,Description,,\nTEST-1\nTEST-2,Cups,extra,more\n", "csv")
    assert table.columns == ["SKU", "Description"]
    assert table.rows == [["TEST-1", ""], ["TEST-2", "Cups"]]


@pytest.mark.parametrize(
    "content, file_type, code",
    [
        (b"", "csv", "IMP-002"),
        (b"SKU,Description\n", "csv", "IMP-002"),
        (b"%PDF-1.4 not a table", "pdf", "IMP-001"),
        (b"PK\x03\x04 not really a workbook", "xlsx", "IMP-004"),
    ],
)
def test_files_that_are_not_usable_tables_fail_with_a_catalog_code(content, file_type, code):
    with pytest.raises(cp.ImportParseError) as excinfo:
        cp.parse_table(content, file_type)
    assert excinfo.value.code == code


def test_a_file_over_the_row_cap_is_refused(monkeypatch):
    monkeypatch.setattr(cp, "MAX_ROWS", 2)
    with pytest.raises(cp.ImportParseError) as excinfo:
        cp.parse_table(b"SKU\nA\nB\nC\n", "csv")
    assert excinfo.value.code == "IMP-003"


def test_formula_cells_arrive_as_their_value_never_the_formula():
    table = cp.parse_table(_xlsx([["SKU", "Qty"], ["TEST-1", "=1+1"]]), "xlsx")
    # openpyxl stores "=1+1" as a formula with no cached value; data_only
    # reads the value (none here), never the formula text.
    assert table.rows[0][1] in ("", "2")


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

    from docflow_core.errors import CATALOG

    source = inspect.getsource(ci) + inspect.getsource(cp)
    for code in set(re.findall(r'"((?:CAT|BUY|IMP)-\d{3})"', source)):
        assert code in CATALOG, code
