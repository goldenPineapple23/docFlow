"""
The API suite's one purge for its throwaway tenants (tests/tenant_cleanup.py;
the Stage 3 checkpoint's list, D-197; founder's requirements of 2026-10-09).

What must hold, each against the real database and the real bucket:

* after the purge nothing of the tenant is left -- not a row in any table, not
  a file under its storage prefix -- for a tenant that had a document, a model
  run, a stored file, a founder alert with its e-mail, and a scheduled job;
* a tenant this run did not create is refused, whatever it is called;
* the order of the deletes agrees with every foreign key in the live schema,
  and every table that points at a tenant without a `tenant_id` has a rule;
* nothing outside the API's tests imports the purge.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from docflow_core import founder_alerts, storage
from docflow_core.config import get_settings
from docflow_core.db import platform_session
from sqlalchemy import text

from tests import tenant_cleanup
from tests.conftest import requires_console_schema
from tests.tenant_cleanup import (
    NotThisRunsTenant,
    outside_links,
    purge_order,
    purge_test_tenant,
    register_test_tenant,
    rows_left,
    tenant_tables,
)
from tests.test_validation import _TestValidationTenant

requires_the_real_bucket = pytest.mark.skipif(
    not tenant_cleanup._real_bucket_is_configured(),
    reason=(
        "the STORAGE_S3_* settings are not set -- this test writes a file to the real bucket and "
        "proves the purge removes it. See SETUP.md."
    ),
)


def _count(session, table: str, row_id) -> int:
    query = text(f"SELECT count(*) FROM {table} WHERE id = :id")
    return int(session.execute(query, {"id": str(row_id)}).scalar_one())


@requires_console_schema
@requires_the_real_bucket
def test_the_purge_leaves_no_row_and_no_file_of_a_test_tenant(monkeypatch):
    """A tenant with a document, a model run, a stored file, a founder alert,
    the alert's e-mail and a scheduled job; then the purge; then nothing.

    The tenant row itself is deleted, so the database's own foreign keys vouch
    for every row that points at it or at one of its rows; `rows_left` reads
    every column in the schema that names a tenant on top of that."""
    # Without an address the alert writes no e-mail, and the seed would be short.
    monkeypatch.setenv("FOUNDER_ALERT_EMAIL", "founder@example.test")
    get_settings.cache_clear()
    bucket = storage.S3Backend()
    run_id, job_id = uuid4(), uuid4()
    try:
        tenant = _TestValidationTenant("Acme Test Purge")
        tenant.__enter__()
        tid = tenant.tenant_id
        prefix = f"tenants/{tid}/"
        try:
            document_id = tenant.create_document(
                lines=[{"quantity": "2", "unit_price": "5.00", "line_total": "10.00", "confidence": "0.97"}]
            )
            stored = storage.save_file(tid, "po.txt", b"PO Number: TEST-PURGE\n", content_type="text/plain")
            with platform_session() as session:
                session.execute(
                    text(
                        "INSERT INTO extraction_runs "
                        "(id, tenant_id, document_id, est_cost_usd, succeeded, created_at) "
                        "VALUES (:id, :t, :d, :c, true, now())"
                    ),
                    {"id": str(run_id), "t": str(tid), "d": str(document_id), "c": "0.0100"},
                )
                session.execute(
                    text(
                        "INSERT INTO scheduled_jobs (id, tenant_id, job_type, run_at, dedupe_key) "
                        "VALUES (:id, :t, 'first_week_checkin', now() + make_interval(days => 7), :k)"
                    ),
                    {"id": str(job_id), "t": str(tid), "k": f"test-purge:{job_id}"},
                )
                assert founder_alerts.raise_alert(
                    session,
                    alert_type="scheduled_job_failed",
                    severity="high",
                    tenant_id=tid,
                    payload={"test": "purge"},
                )

            # The seed is really there, so "nothing left" below means something.
            with platform_session() as session:
                seeded = rows_left(session, tid)
            for column in (
                "documents.tenant_id",
                "document_headers.tenant_id",
                "document_lines.tenant_id",
                "extraction_runs.tenant_id",
                "founder_alerts.tenant_id",
                "email_outbox.tenant_id",
                "scheduled_jobs.tenant_id",
                "users.tenant_id",
                "tenants.id",
            ):
                assert seeded.get(column), f"the seed has no row in {column}: {seeded}"
            assert bucket.list_keys(prefix) == [stored]
        finally:
            tenant.__exit__(None, None, None)  # the purge

        with platform_session() as session:
            assert rows_left(session, tid) == {}
            assert _count(session, "documents", document_id) == 0
            assert _count(session, "extraction_runs", run_id) == 0
            assert _count(session, "scheduled_jobs", job_id) == 0
        assert bucket.list_keys(prefix) == []
    finally:
        get_settings.cache_clear()


@requires_console_schema
def test_the_purge_refuses_a_tenant_this_run_did_not_register():
    """Registration, not the name, is what lets the purge act: a tenant called
    like a test tenant but never registered is refused and still there."""
    tid = uuid4()
    with platform_session() as session:
        session.execute(
            text(
                "INSERT INTO tenants "
                "(id, name, status, onboarding_status, created_at, updated_at, status_changed_at) "
                "VALUES (:id, 'Acme Test Not Registered', 'active', 'tenant_created', now(), now(), now())"
            ),
            {"id": str(tid)},
        )
    try:
        with pytest.raises(NotThisRunsTenant):
            purge_test_tenant(tid)
        with platform_session() as session:
            assert _count(session, "tenants", tid) == 1
    finally:
        # This test made the row, so it may remove it -- by registering it first.
        register_test_tenant(tid)
        purge_test_tenant(tid)
    with platform_session() as session:
        assert _count(session, "tenants", tid) == 0

    # An id that names nothing at all is refused the same way.
    with pytest.raises(NotThisRunsTenant):
        purge_test_tenant(uuid4())


@requires_console_schema
def test_the_purge_order_agrees_with_every_foreign_key_in_the_live_schema():
    """Read from the database, so a table or a link added later is either
    purged in the right place or stops this test by name."""
    with platform_session() as session:
        tables = tenant_tables(session)
        order = purge_order(session)
        outside = outside_links(session)  # raises for a link with no rule
        links = session.execute(
            text(
                """
                SELECT child.relname, parent.relname, c.conname
                FROM pg_constraint c
                JOIN pg_class child ON child.oid = c.conrelid
                JOIN pg_class parent ON parent.oid = c.confrelid
                WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
                  AND c.confdeltype <> 'c' AND c.conrelid <> c.confrelid
                """
            )
        ).all()
    assert sorted(order) == sorted(tables), "every table with a tenant_id is purged, once"
    position = {table: i for i, table in enumerate(order)}
    between = [link for link in links if link[0] in position and link[1] in position]
    assert between, "no links found -- the query is broken, not the order"
    wrong = [
        f"{child} -> {parent} ({name})"
        for child, parent, name in between
        if position[child] > position[parent]
    ]
    assert not wrong, f"purged after the table it points at: {wrong}"
    # No rule for a link that no longer exists, and none missing.
    assert {(table, column) for table, column, _action in outside} == set(tenant_cleanup._OUTSIDE_LINKS)


def test_nothing_outside_the_api_tests_imports_the_purge():
    """Test code only (founder, 2026-10-09): the product's delete is
    `admin_data_access.delete_tenant`, behind the founder's typed name."""
    repo = Path(__file__).resolve().parents[3]
    offenders = []
    product = (
        "apps/api/app",
        "apps/worker/app",
        "apps/parse/parse_service",
        "packages/core/docflow_core",
        "scripts",
    )
    for folder in product:
        root = repo / folder
        assert root.is_dir(), f"{folder} has moved; this check no longer covers it"
        for path in root.rglob("*.py"):
            if "tenant_cleanup" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(repo)))
    assert not offenders, f"product code names the test purge: {offenders}"
