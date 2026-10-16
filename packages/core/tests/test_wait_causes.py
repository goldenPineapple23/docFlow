"""
The wait causes the code writes and the ones the database allows are one list
(Q14, D-206). The check on documents.wait_cause was last set by migration
0038; its reverse goes back to 0035's three. Read from the files, no database:
the database tests check the live constraint the same way.
"""

from __future__ import annotations

import re
from pathlib import Path

from docflow_core import document_status
from docflow_core.constants import DATABASE_MAX_WAIT_HOURS, PROVIDER_MAX_WAIT_HOURS

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "supabase" / "migrations"


def _causes(sql: str) -> list[set[str]]:
    """Every list given to documents_wait_cause_check in a file, in order."""
    found = re.findall(
        r"documents_wait_cause_check\s+check\s*\(wait_cause in \(([^)]*)\)\)", sql, flags=re.IGNORECASE
    )
    return [set(re.findall(r"'([a-z_]+)'", group)) for group in found]


def test_the_latest_migration_to_set_the_check_allows_exactly_the_codes_causes():
    latest = None
    for path in sorted(MIGRATIONS.glob("*.sql")):
        lists = _causes(path.read_text(encoding="utf-8"))
        if lists:
            latest = (path.name, lists[-1])
    assert latest is not None and latest[0] == "0038_database_wait.sql"
    assert latest[1] == set(document_status.WAIT_CAUSES)


def test_the_reverse_goes_back_to_the_three_causes_of_0035():
    reverse = (ROOT / "supabase" / "reverse" / "0038_reverse.sql").read_text(encoding="utf-8")
    assert _causes(reverse) == [set(document_status.WAIT_CAUSES) - {"database"}]
    assert "wait_cause = 'database'" in reverse, "it says what happens to documents already waiting"


def test_the_database_wait_has_the_same_six_hour_maximum_as_the_provider():
    """Founder, 2026-10-16: the provider wait's interval logic and a 6 h maximum."""
    assert DATABASE_MAX_WAIT_HOURS == PROVIDER_MAX_WAIT_HOURS == 6
