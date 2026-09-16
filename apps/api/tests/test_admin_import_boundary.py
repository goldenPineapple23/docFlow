"""
CLAUDE.md Section 7.15.1 / Section 10: `docflow_core.admin_data_access` may
only be imported from /admin/* route handlers and the nightly rollup job.
This is a dependency-graph test, not a code review comment -- it fails CI
if the boundary is ever crossed.
"""

from __future__ import annotations

import ast
from pathlib import Path

ALLOWED_IMPORTERS = {
    # app/routers/admin.py and any future /admin/* route handler files.
    "app/routers/admin.py",
    # Phase 5/6 adds the nightly rollup job here, e.g. "app/jobs/nightly_rollup.py".
}

APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def _imports_admin_data_access(py_file: Path) -> bool:
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level = alias.name.split(".")[0]
                if top_level == "docflow_core" and "admin_data_access" in alias.name:
                    return True
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if "admin_data_access" in module:
                return True
            if module == "docflow_core" and any(alias.name == "admin_data_access" for alias in node.names):
                return True
    return False


def test_admin_data_access_only_imported_from_allowed_locations():
    violations = []
    for py_file in APP_ROOT.rglob("*.py"):
        relative = py_file.relative_to(APP_ROOT.parent).as_posix()
        if relative in ALLOWED_IMPORTERS:
            continue
        if _imports_admin_data_access(py_file):
            violations.append(relative)

    assert not violations, (
        "docflow_core.admin_data_access imported outside the allowed locations "
        f"{sorted(ALLOWED_IMPORTERS)}: {violations}. See CLAUDE.md Section 7.15.1."
    )
