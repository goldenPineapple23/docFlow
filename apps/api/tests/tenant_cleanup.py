"""
One purge for the API suite's throwaway tenants (the Stage 3 checkpoint's
list; D-197, founder 2026-10-08 and 2026-10-09).

Before this, nine tenant helpers and four cleanup functions each kept a delete
list of their own, and only some of them removed a tenant's founder alerts,
e-mails and scheduled jobs, so a finished run still left rows behind on
docflow-staging. Every helper now ends in `purge_test_tenant`, which removes
the tenant's files, every row that points at the tenant, and the tenant.

Two rules (founder, 2026-10-09):

* **It acts only on a tenant created in this run.** Each helper registers the
  id when it creates the tenant (`register_test_tenant`). An id that was not
  registered in this process is refused with `NotThisRunsTenant`, whatever the
  tenant is called: staging holds tenants no test may ever remove.
* **Test code only.** Nothing under `app/` or `docflow_core/` imports this
  module (`test_tenant_cleanup.py` checks). The product's own delete is
  `admin_data_access.delete_tenant`, which keeps the tenant row, its
  lifecycle log and the founder's audit trail; a test tenant keeps nothing.

The order of the deletes is read from the live schema, children before
parents, so a table added later is purged without anyone editing a list. A
table with no `tenant_id` column that points at a tenant must be named in
`_OUTSIDE_LINKS`; one that isn't stops the purge with its name.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from docflow_core.config import get_settings
from docflow_core.db import platform_session
from sqlalchemy import text
from sqlalchemy.orm import Session


class NotThisRunsTenant(RuntimeError):
    """The purge was asked to remove a tenant this run did not create."""


_registered: set[str] = set()
_purged: set[str] = set()

# Tables with no tenant_id column whose rows point at a tenant, and what the
# purge does with those rows. "delete": the Console's audit rows about a test
# tenant go with it. "unlink": the row outlives the tenant (the test that made
# an intake removes it itself).
_OUTSIDE_LINKS: dict[tuple[str, str], str] = {
    ("admin_actions", "target_tenant_id"): "delete",
    ("onboarding_intakes", "linked_tenant_id"): "unlink",
}


def register_test_tenant(tenant_id: Any) -> None:
    """Called by a helper at the moment it creates a tenant."""
    _registered.add(str(UUID(str(tenant_id))))


def is_registered(tenant_id: Any) -> bool:
    return str(UUID(str(tenant_id))) in _registered


def tenant_tables(session: Session) -> list[str]:
    """Every table in `public` that carries a `tenant_id`."""
    return [
        row[0]
        for row in session.execute(
            text(
                """
                SELECT c.relname FROM pg_class c
                JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
                WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
                ORDER BY c.relname
                """
            )
        )
    ]


def _foreign_keys(session: Session) -> list[tuple[str, str, str, str]]:
    """(child table, child column, parent table, on-delete action) for every
    single-column foreign key in `public`."""
    return [
        (row[0], row[1], row[2], row[3])
        for row in session.execute(
            text(
                """
                SELECT child.relname, a.attname, parent.relname, c.confdeltype::text
                FROM pg_constraint c
                JOIN pg_class child ON child.oid = c.conrelid
                JOIN pg_class parent ON parent.oid = c.confrelid
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
                WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
                """
            )
        )
    ]


def purge_order(session: Session) -> list[str]:
    """
    The tenant tables, each before every table it points at.

    A link that cascades needs no order: the parent's delete takes the child.
    Every other link does, SET NULL included -- a parent deleted first would
    blank the child's column, and a row left half-blank can break a CHECK
    (a line's matched item and its match method go together).
    """
    tables = tenant_tables(session)
    known = set(tables)
    must_go_first: dict[str, set[str]] = {table: set() for table in tables}
    for child, _column, parent, on_delete in _foreign_keys(session):
        if child in known and parent in known and child != parent and on_delete != "c":
            must_go_first[parent].add(child)
    order: list[str] = []
    while must_go_first:
        ready = sorted(table for table, before in must_go_first.items() if not before)
        if not ready:
            raise RuntimeError(
                f"the foreign keys among {sorted(must_go_first)} form a loop; the test purge "
                "(tests/tenant_cleanup.py) needs a rule for it"
            )
        for table in ready:
            order.append(table)
            del must_go_first[table]
        for before in must_go_first.values():
            before.difference_update(ready)
    return order


def outside_links(session: Session) -> list[tuple[str, str, str]]:
    """(table, column, what to do) for every table with no tenant_id that
    points at `tenants`. Raises for one `_OUTSIDE_LINKS` doesn't name."""
    known = set(tenant_tables(session))
    found: list[tuple[str, str, str]] = []
    for child, column, parent, _on_delete in _foreign_keys(session):
        if parent != "tenants" or child in known or child == "tenants":
            continue
        action = _OUTSIDE_LINKS.get((child, column))
        if action is None:
            raise RuntimeError(
                f"{child}.{column} points at a tenant and the test purge (tests/tenant_cleanup.py) "
                "has no rule for it: add it to _OUTSIDE_LINKS"
            )
        found.append((child, column, action))
    return sorted(found)


_bucket: Any = None


def _real_bucket_is_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.storage_s3_endpoint
        and settings.storage_s3_region
        and settings.storage_s3_access_key_id
        and settings.storage_s3_secret_access_key
    )


def _remove_files(tenant_id: UUID) -> None:
    """
    Everything under `tenants/{id}/` in the real bucket. The backend is built
    here, not taken from `storage._get_backend()`: a test may have swapped
    that for the in-memory fake (the outage tests leave it failing while their
    tenant is cleaned up), and a fake holds nothing that outlives the test.
    On a machine with no Storage settings nothing can have been written, so
    there is nothing to remove.
    """
    if not _real_bucket_is_configured():
        return
    from docflow_core.storage import S3Backend, StorageUnavailableError

    global _bucket
    if _bucket is None:
        _bucket = S3Backend()
    backend = _bucket
    prefix = f"tenants/{tenant_id}/"
    keys = backend.list_keys(prefix)
    if keys:
        backend.delete_keys(keys)
        remaining = backend.list_keys(prefix)
        if remaining:
            raise StorageUnavailableError(f"{len(remaining)} objects still under {prefix}")


def purge_test_tenant(tenant_id: Any) -> None:
    """
    Remove a tenant this run created: its files, every row that points at it,
    and the tenant row. Safe to call twice (a cleanup function and then the
    helper's own exit both call it).
    """
    tid = str(UUID(str(tenant_id)))
    if tid not in _registered:
        raise NotThisRunsTenant(
            f"tenant {tid} was not created by this test run, so the test purge will not touch it "
            "(tests/tenant_cleanup.py: register_test_tenant at creation)"
        )
    if tid in _purged:
        return
    _remove_files(UUID(tid))
    with platform_session() as session:
        # One round trip for the whole purge: some thirty statements a tenant,
        # several hundred tenants a run, each a network hop on staging. A
        # statement sent with no parameters may hold several commands, so the
        # id is written into the text -- it is the output of UUID() above,
        # hex and dashes only, and the names come from the database's catalog.
        session.connection().exec_driver_sql(";\n".join(_statements(session, tid)))
    _purged.add(tid)


_plan: tuple[list[tuple[str, str, str]], list[str]] | None = None


def _statements(session: Session, tid: str) -> list[str]:
    global _plan
    if _plan is None:  # the schema does not change during a run
        _plan = (outside_links(session), purge_order(session))
    outside, order = _plan
    literal = f"'{UUID(tid)}'::uuid"
    statements = [
        f'DELETE FROM "{table}" WHERE "{column}" = {literal}'
        if action == "delete"
        else f'UPDATE "{table}" SET "{column}" = NULL WHERE "{column}" = {literal}'
        for table, column, action in outside
    ]
    statements += [f'DELETE FROM "{table}" WHERE tenant_id = {literal}' for table in order]
    statements.append(f"DELETE FROM tenants WHERE id = {literal}")
    return statements


def rows_left(session: Session, tenant_id: Any) -> dict[str, int]:
    """
    Every place a row of this tenant could still be: each table's `tenant_id`,
    every other column in `public` whose name ends in `tenant_id`, and the
    tenant row itself. Table -> rows found (only the non-zero ones).
    """
    tid = str(UUID(str(tenant_id)))
    columns = session.execute(
        text(
            """
            SELECT c.relname, a.attname FROM pg_class c
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname LIKE '%tenant\\_id' AND NOT a.attisdropped
            WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
            ORDER BY 1, 2
            """
        )
    ).all()
    left: dict[str, int] = {}
    for table, column in columns:
        count = session.execute(
            text(f"SELECT count(*) FROM {table} WHERE {column} = :t"), {"t": tid}
        ).scalar_one()
        if count:
            left[f"{table}.{column}"] = int(count)
    if session.execute(text("SELECT count(*) FROM tenants WHERE id = :t"), {"t": tid}).scalar_one():
        left["tenants.id"] = 1
    return left
