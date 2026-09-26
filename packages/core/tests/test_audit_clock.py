"""
Every `admin_actions` row is stamped by the database's clock (D-164).

The Console's Audit tab merges `admin_actions` with `tenant_lifecycle_events`,
which the database stamps with `clock_timestamp()`. An insert that sends the
application server's own time would put two clocks in one timeline, and a
server clock running behind lists events out of order (it did: 0.66 s on the
development machine). This fails the build if any insert into
`admin_actions` supplies `created_at` from the application.
"""

import re
from pathlib import Path

SOURCES = Path(__file__).resolve().parents[1] / "docflow_core"
_INSERT = re.compile(r"INSERT INTO admin_actions.*?\)\s*VALUES\s*\((.*?)\)\s*\"\"\"", re.S)


def test_every_admin_actions_insert_uses_the_database_clock():
    found = 0
    for path in SOURCES.rglob("*.py"):
        for match in _INSERT.finditer(path.read_text(encoding="utf-8")):
            found += 1
            assert "clock_timestamp()" in match.group(1), (
                f"{path.name}: admin_actions stamped by the app clock"
            )
    assert found >= 2, "expected the Console writer and the tier-version writer"
