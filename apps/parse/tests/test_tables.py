"""
Reading a catalog or customer-list file into a text table (Section 7.15.2
Steps 4-5; D-108). Moved from packages/core/tests/test_catalog_import.py in
Stage 3c with the code it tests. All data is fictional (CLAUDE.md Section 0
rule 4).
"""

from __future__ import annotations

import io

import pytest

from parse_service.parsing import tables as cp

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


def test_the_job_answer_for_a_catalog_file():
    answer = cp.parse(b"SKU,Description\nTEST-1,Test Beans\n", "catalog.csv")
    assert answer == {
        "outcome": "ok",
        "columns": ["SKU", "Description"],
        "rows": [["TEST-1", "Test Beans"]],
        "header_row_number": 1,
    }


def test_the_job_answer_for_a_file_that_is_not_a_table():
    content = b"MZ\x90\x00" + b"\x00" * 64
    assert cp.parse(content, "catalog.csv") == {"outcome": "rejected", "code": "IMP-004"}
