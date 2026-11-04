"""
A migration round trip's last state against its first (D-232).

A migration that adds columns adds them at the END of their tables when it
runs again after its reverse, behind any column a later migration added
since (0040 put `page_count` and `pages` behind 0037's `fault_keys`). So a
round trip's forward-again state may have exactly that migration's own
columns moved to the end of their tables, in the order it adds them; every
other column must be where it was, every column must still be there, and
everything else in the state must be equal.

Read-only, and no database: the round-trip scripts call it with the states
they read, and packages/core/tests holds it to account.
"""

from __future__ import annotations

from typing import Any


def columns_after_rerun(first: list[dict[str, Any]], own: set[tuple[str, str]]) -> list[dict[str, Any]]:
    """The column list the forward-again state must read: per table, in the
    first state's table order, the other columns in their order, then the
    migration's own in theirs."""
    tables = list(dict.fromkeys(column["table"] for column in first))
    result: list[dict[str, Any]] = []
    for table in tables:
        columns = [column for column in first if column["table"] == table]
        result += [c for c in columns if (table, c["column"]) not in own]
        result += [c for c in columns if (table, c["column"]) in own]
    return result


def same_after_rerun(first: dict[str, Any], again: dict[str, Any], own: set[tuple[str, str]]) -> bool:
    """True when `again` is `first` with only the migration's own columns
    moved to the end of their tables (or left where they were)."""
    if {key for key in first if key != "columns"} != {key for key in again if key != "columns"}:
        return False
    if any(first[key] != again[key] for key in first if key != "columns"):
        return False
    return again["columns"] in (first["columns"], columns_after_rerun(first["columns"], own))
