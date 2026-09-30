"""
Stage 3b item 7: a tenant hard delete removes the files FIRST and the rows
only once the tenant's prefix is empty, then sweeps the prefix again after
the rows commit. Driven here without a database: the platform session is a
recording fake and Storage is the in-memory fake, so the ORDER of events is
what is tested. The staging suites cover the real SQL.
"""

from __future__ import annotations

import contextlib
from uuid import uuid4

import pytest

from docflow_core import admin_data_access, storage
from docflow_core.admin_data_access import ConsoleError
from tests.storage_fake import FakeStorage

NAME = "Acme Test Distributor"


class _Result:
    rowcount = 0

    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _Session:
    def __init__(self, log, tenant_row):
        self._log = log
        self._tenant_row = tenant_row

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self._log.append(("sql", sql[:60]))
        if sql.startswith("SELECT name, status"):
            return _Result(self._tenant_row)
        return _Result()


@pytest.fixture()
def world(monkeypatch):
    log: list[tuple[str, str]] = []
    tenant_row = {"name": NAME, "status": "pending_deletion", "due": True}
    fake = FakeStorage()
    fake.before_call = lambda op, key: log.append(("storage", op))

    @contextlib.contextmanager
    def platform_session():
        yield _Session(log, tenant_row)

    monkeypatch.setattr(admin_data_access, "platform_session", platform_session)
    with storage.set_backend_for_tests(fake):
        yield log, fake, tenant_row


def _delete(tenant_id):
    admin_data_access.delete_tenant(
        platform_admin_user_id=uuid4(),
        tenant_id=tenant_id,
        confirm_name=NAME,
        reason="customer asked, window elapsed",
    )


def test_files_go_before_any_row_and_the_prefix_is_confirmed_empty(world):
    log, fake, _ = world
    tenant = uuid4()
    for _ in range(3):
        storage.save_file(tenant, "po.pdf", b"x")
    log.clear()

    _delete(tenant)

    first_delete_sql = next(i for i, e in enumerate(log) if e[0] == "sql" and e[1].startswith("DELETE FROM"))
    storage_before = [e[1] for e in log[:first_delete_sql] if e[0] == "storage"]
    # list, delete, list-again-to-confirm-empty, all before the first row goes
    assert storage_before == ["list", "delete_many", "list"]
    assert not [k for k in fake.objects if k.startswith(f"tenants/{tenant}/")]


def test_a_failed_file_removal_leaves_the_database_untouched(world):
    log, fake, _ = world
    tenant = uuid4()
    stuck = storage.save_file(tenant, "po.pdf", b"x")
    fake.undeletable.add(stuck)
    log.clear()

    with pytest.raises(ConsoleError) as info:
        _delete(tenant)
    assert info.value.code == "LIFE-007"
    writes = [e for e in log if e[0] == "sql" and not e[1].startswith("SELECT")]
    assert writes == []


def test_the_final_sweep_removes_an_export_written_during_the_delete(world, monkeypatch):
    """A pending_deletion tenant keeps export access (7.14), so an export can
    land between the file step and the database step; the sweep after the
    commit removes it, and records itself as its own admin action."""
    log, fake, _ = world
    tenant = uuid4()
    storage.save_file(tenant, "po.pdf", b"x")
    late = f"tenants/{tenant}/exports/late.csv"
    real_check = admin_data_access._check_deletable

    def check_then_export(session, tenant_id, confirm_name, *, lock):
        real_check(session, tenant_id, confirm_name, lock=lock)
        if lock:  # the second check: files already gone, rows not yet
            fake.objects[late] = b"late"

    monkeypatch.setattr(admin_data_access, "_check_deletable", check_then_export)

    _delete(tenant)

    assert late not in fake.objects
    audits = [e for e in log if e[0] == "sql" and "INSERT INTO admin_actions" in e[1]]
    assert len(audits) == 2  # tenant_delete, then tenant_delete_final_sweep


def test_a_tenant_not_ready_to_delete_is_refused_before_any_file_is_touched(world):
    log, fake, tenant_row = world
    tenant = uuid4()
    storage.save_file(tenant, "po.pdf", b"x")
    tenant_row["status"] = "active"
    log.clear()

    with pytest.raises(ConsoleError) as info:
        _delete(tenant)
    assert info.value.code == "LIFE-006"
    assert not [e for e in log if e[0] == "storage"]
