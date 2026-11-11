"""
The pages backfill's selection against the real database (viewer A6;
DECISIONS.md D-236): which documents it queues, in which tenant, and that a
second run queues only what is still not drawn. The render itself is A3's
task, tested in test_render_pages_db.py.

The database is shared with other tests, so every assertion looks only at
this file's own tenants.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from docflow_core.db import platform_session
from sqlalchemy import text

import app.backfill_pages as backfill
from tests.conftest import requires_pages_schema
from tests.db_helpers import WorkerTestTenant

pytestmark = requires_pages_schema

_MODEL_ANSWER = json.dumps({"header": {}, "line_items": []})


def _insert(
    tenant: WorkerTestTenant,
    status: str,
    *,
    minutes_ago: int,
    page_count: int | None = None,
    deleted: bool = False,
) -> UUID:
    """A document row as the pipeline would have left it in `status`. No
    file is stored: the selection reads rows only."""
    document_id = uuid4()
    reviewable = status in ("needs_review", "approved", "exported", "rejected")
    approved = status in ("approved", "exported")
    with platform_session() as session:
        session.execute(
            text(
                "INSERT INTO documents (id, tenant_id, original_filename, storage_path, source, status, "
                "content_sha256, created_at, raw_json, quarantine_reason, failure_code, "
                "approved_by, approved_at, approved_json, approved_snapshot_hash, page_count, pages, "
                "deleted_at) "
                "VALUES (:id, :tid, 'po.pdf', :path, 'upload', :status, :sha, "
                "now() - make_interval(mins => CAST(:ago AS integer)), CAST(:raw AS jsonb), "
                "CAST(:qreason AS text), "
                "CAST(:fcode AS text), "
                "CAST(:approved_by AS uuid), CASE WHEN CAST(:approved AS boolean) THEN now() END, "
                "CASE WHEN CAST(:approved AS boolean) THEN CAST('{}' AS jsonb) END, "
                "CASE WHEN CAST(:approved AS boolean) THEN 'acme-test' END, "
                "CAST(:page_count AS integer), CASE WHEN CAST(:page_count AS integer) IS NULL THEN NULL "
                "ELSE CAST('[]' AS jsonb) END, "
                "CASE WHEN CAST(:deleted AS boolean) THEN now() END)"
            ),
            {
                "id": str(document_id),
                "tid": str(tenant.tenant_id),
                "path": f"tenants/{tenant.tenant_id}/originals/{document_id}",
                "status": status,
                "sha": "0" * 64,
                "ago": minutes_ago,
                "raw": _MODEL_ANSWER if reviewable else None,
                "qreason": "manual" if status == "quarantined" else None,
                "fcode": "DOC-022" if status == "failed" else None,
                "approved_by": str(tenant.user_id) if approved else None,
                "approved": approved,
                "page_count": page_count,
                "deleted": deleted,
            },
        )
    return document_id


def _selected(*tenants: WorkerTestTenant) -> list[backfill.Candidate]:
    mine = {tenant.tenant_id for tenant in tenants}
    return [candidate for candidate in backfill.select_candidates() if candidate.tenant_id in mine]


def test_it_selects_every_reviewable_document_with_no_pages_and_nothing_else():
    with WorkerTestTenant("Acme Test Backfill Selection") as tenant:
        wanted = [
            _insert(tenant, status, minutes_ago=60 - n)
            for n, status in enumerate(("needs_review", "approved", "exported", "rejected"))
        ]
        # Never selected (founder, 2026-11-11): the live pipeline's own, never
        # parsed, and failed; plus drawn, zero-page and deleted documents.
        for status in ("pending", "processing", "quarantined", "failed"):
            _insert(tenant, status, minutes_ago=90)
        _insert(tenant, "needs_review", minutes_ago=90, page_count=0)
        _insert(tenant, "needs_review", minutes_ago=90, deleted=True)

        assert [c.document_id for c in _selected(tenant)] == wanted  # oldest first
        assert {c.tenant_id for c in _selected(tenant)} == {tenant.tenant_id}


def test_a_quarantined_document_with_no_pages_is_never_queued():
    """The founder's named case (2026-11-11): quarantined is never parsed,
    so it is never sent to the parse service by the backfill either."""
    with WorkerTestTenant("Acme Test Backfill Quarantine") as tenant:
        held = _insert(tenant, "quarantined", minutes_ago=5)
        sent: list[backfill.Candidate] = []
        code = backfill.main([], select=lambda: _selected(tenant), send=sent.append, queue_length=lambda: 0)
        assert code == 0
        assert held not in [c.document_id for c in sent]
        assert sent == []


def test_each_document_is_queued_with_its_own_tenant_and_tenants_are_kept_apart():
    with (
        WorkerTestTenant("Acme Test Backfill North") as north,
        WorkerTestTenant("Acme Test Backfill South") as south,
    ):
        north_doc = _insert(north, "needs_review", minutes_ago=30)
        south_doc = _insert(south, "needs_review", minutes_ago=20)

        sent: list[backfill.Candidate] = []
        backfill.main([], select=lambda: _selected(north, south), send=sent.append, queue_length=lambda: 0)
        assert sent == [
            backfill.Candidate(north.tenant_id, north_doc),
            backfill.Candidate(south.tenant_id, south_doc),
        ]
        # Each tenant's read sees only its own documents (its own session).
        north_reads = [candidate for _, candidate in backfill.tenant_candidates(north.tenant_id)]
        assert north_reads == [backfill.Candidate(north.tenant_id, north_doc)]


def test_a_tenant_being_deleted_is_left_out():
    with WorkerTestTenant("Acme Test Backfill Leaving") as tenant:
        _insert(tenant, "needs_review", minutes_ago=10)
        with platform_session() as session:
            session.execute(
                text("UPDATE tenants SET status = 'pending_deletion' WHERE id = :id"),
                {"id": str(tenant.tenant_id)},
            )
        assert _selected(tenant) == []


def test_a_second_run_queues_only_what_is_still_not_drawn():
    with WorkerTestTenant("Acme Test Backfill Resume") as tenant:
        first = _insert(tenant, "needs_review", minutes_ago=30)
        second = _insert(tenant, "approved", minutes_ago=20)
        assert [c.document_id for c in _selected(tenant)] == [first, second]

        # The first one's render wrote its list (A3: both columns, once).
        with platform_session() as session:
            session.execute(
                text("UPDATE documents SET page_count = 0, pages = '[]'::jsonb WHERE id = :id"),
                {"id": str(first)},
            )
        assert [c.document_id for c in _selected(tenant)] == [second]
