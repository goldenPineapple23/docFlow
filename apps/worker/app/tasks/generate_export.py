"""
Export generation (CLAUDE.md Section 7.4, Phase 4; DECISIONS.md D-098).

A thin wrapper: everything that matters -- building from the approved
snapshot, the round-trip and determinism checks, storage, the `exports`
row -- is `docflow_core.export_jobs.run_export`, so the tests that prove it
do not need a queue. It runs here, not in the API, because building and
re-reading an .xlsx loads openpyxl, and the web process never does
(Section 7.11).

`tenant_id` arrives from the API, which resolved it from the authenticated
session (Section 7.5), exactly as `parse_and_extract` receives it.
"""

from __future__ import annotations

from uuid import UUID

from docflow_core.export_jobs import run_export

from app.celery_app import celery_app


@celery_app.task(name="docflow.generate_export")
def generate_export(tenant_id: str, export_id: str) -> str:
    return run_export(UUID(tenant_id), UUID(export_id)).status
