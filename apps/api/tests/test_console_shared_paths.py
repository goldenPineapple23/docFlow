"""
Phase 5 exit criterion (build prompt Section 6): "the Console never calls a
parsing, extraction, or review function the tenant surface doesn't also call
(verified by module-dependency check, not by inspection)". Section 10: never
"write a second catalog parser, upload handler, extraction entry point, or
review component for the Console -- it calls the tenant surface's, with a
different actor."

Checked from the code itself:
  * the admin router imports no extraction, review, matching or validation
    module, and no worker code -- it cannot run any of them itself;
  * every document it sends for extraction goes through the same task the
    tenant's upload and email intake enqueue;
  * its test-batch upload is the tenant surface's own `ingest_upload`;
  * every acting-as route (/admin/tenants/{id}/act/...) is served by the very
    same endpoint function as the tenant's route (D-111);
  * there is one catalog parser, used only by the one catalog import.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.main import app

APP_DIR = Path(__file__).resolve().parents[1] / "app"
REPO_ROOT = Path(__file__).resolve().parents[3]
ADMIN_ROUTER = APP_DIR / "routers" / "admin.py"

# Modules that parse, extract, or review a document. The Console reaches these
# only through the tenant surface's routes and tasks, never by importing them.
PIPELINE_MODULES = {
    "docflow_core.extraction",
    "docflow_core.review",
    "docflow_core.matching",
    "docflow_core.validation",
    "docflow_core.buyers",
    "docflow_core.duplicates",
    "docflow_core.catalog_parsing",
    "docflow_core.previews",
}
PIPELINE_TASKS = {"docflow.parse_and_extract"}
_TASK_RE = re.compile(r"send_task\(\s*\"([a-z_.]+)\"")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            # `from docflow_core import review` imports a module too.
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _tasks(path: Path) -> set[str]:
    return set(_TASK_RE.findall(path.read_text(encoding="utf-8")))


def test_the_admin_router_imports_no_parsing_extraction_or_review_module():
    imported = _imports(ADMIN_ROUTER)
    assert not (imported & PIPELINE_MODULES), sorted(imported & PIPELINE_MODULES)
    assert not any(name.startswith("app.tasks") for name in imported)


def test_the_console_uses_example_prompting_only_for_its_switch():
    """The Console shows and flips the per-tenant flag (D-141); choosing and
    sending examples happens only inside the one extraction task."""
    used = set(re.findall(r"example_prompting\.(\w+)", ADMIN_ROUTER.read_text(encoding="utf-8")))
    assert used == {"overview", "set_enabled", "ExamplePromptingError"}, sorted(used)


def test_the_console_sends_documents_to_extraction_only_through_the_tenant_surfaces_task():
    console_tasks = _tasks(ADMIN_ROUTER) & PIPELINE_TASKS
    assert console_tasks == {"docflow.parse_and_extract"}  # the scan finds it at all
    tenant_tasks = _tasks(APP_DIR / "routers" / "documents.py") | _tasks(
        REPO_ROOT / "packages" / "core" / "docflow_core" / "email_intake.py"
    )
    assert console_tasks <= tenant_tasks


def test_the_console_test_batch_upload_is_the_tenant_upload_handler():
    tree = ast.parse(ADMIN_ROUTER.read_text(encoding="utf-8"))
    from_routers = {
        (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app.routers")
        for alias in node.names
    }
    assert from_routers == {("app.routers.documents", "ingest_upload")}


def _all_routes() -> list[tuple[str, frozenset[str], object]]:
    """(full path, methods, endpoint function) for every API route, walking
    included routers -- `app.routes` alone hides them in this FastAPI."""
    found = []
    for route in app.router.routes:
        if hasattr(route, "original_router"):
            prefix = getattr(route.include_context, "prefix", "") or ""
            for inner in route.original_router.routes:
                if hasattr(inner, "methods"):
                    found.append((prefix + inner.path, frozenset(inner.methods), inner.endpoint))
        elif hasattr(route, "methods") and hasattr(route, "endpoint"):
            found.append((route.path, frozenset(route.methods), route.endpoint))
    return found


def test_every_acting_as_route_is_the_tenant_route_itself():
    """D-111: the same routers mounted twice. The Console's review and export
    screens are served by the tenant's endpoint functions -- not copies."""
    prefix = "/admin/tenants/{acting_tenant_id}/act"
    routes = _all_routes()
    tenant = {
        (path, methods): endpoint for path, methods, endpoint in routes if not path.startswith("/admin")
    }
    acting = [(path, methods, endpoint) for path, methods, endpoint in routes if path.startswith(prefix)]
    assert len(acting) >= 10, "too few acting-as routes found -- the scan is looking in the wrong place"
    for path, methods, endpoint in acting:
        twin = tenant.get((path[len(prefix):], methods))
        assert twin is endpoint, f"{path} is not served by the tenant's own endpoint"


def test_there_is_one_catalog_parser_and_only_the_one_catalog_import_uses_it():
    users = {
        path.relative_to(REPO_ROOT).as_posix()
        for base in (REPO_ROOT / "apps", REPO_ROOT / "packages")
        for path in base.rglob("*.py")
        if ".venv" not in path.parts
        and "tests" not in path.parts
        and "node_modules" not in path.parts
        and "docflow_core.catalog_parsing" in _imports(path)
    }
    assert users == {"packages/core/docflow_core/catalog_import.py"}, sorted(users)
