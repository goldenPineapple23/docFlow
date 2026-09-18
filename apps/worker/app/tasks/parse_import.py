"""
Reading an uploaded catalog or customer list (CLAUDE.md Section 7.15.2
Steps 4-5; DECISIONS.md D-108).

A thin wrapper: the work is `docflow_core.catalog_import.run_parse`, so the
tests that prove it do not need a queue. It runs here because it opens the
file (`docflow_core.catalog_parsing`), and only the isolated worker ever does
that (Section 7.11).
"""

from __future__ import annotations

from uuid import UUID

from docflow_core.catalog_import import run_parse

from app.celery_app import celery_app


@celery_app.task(name="docflow.parse_import")
def parse_import(tenant_id: str, import_id: str) -> str:
    return run_parse(UUID(tenant_id), UUID(import_id))
