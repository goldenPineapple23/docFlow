"""
The staging sweep's purge (tests/tenant_cleanup.py `purge_swept_tenant`;
D-224, founder 2026-10-27), against the real database: every check refuses
on its own and leaves the tenant whole, the backup holds every row and file
before anything goes, a swept tenant leaves nothing, a tenant beside it is
untouched, and the script's lock refuses while a suite holds the run lock.

The script's environment guard needs no database and is tested in
packages/core/tests/test_sweep_script_guard.py.

Every tenant here is planted by the test, registered with the run, and
removed by the run's own purge at the end if the sweep didn't take it. All
data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from docflow_core import storage
from docflow_core.db import platform_session
from sqlalchemy import text

from tests import tenant_cleanup
from tests.conftest import requires_console_schema
from tests.tenant_cleanup import (
    SweepRefused,
    purge_swept_tenant,
    purge_test_tenant,
    records_naming,
    register_test_tenant,
    rows_left,
    sweep_listing,
    sweep_session,
)
from tests.test_validation import _TestValidationTenant

pytestmark = requires_console_schema

_REPO = pathlib.Path(__file__).resolve().parents[3]
# Sweep A's id, as DECISIONS D-220 item 7 records it.
_RECORDED_ID = "73602e07-9ab2-4050-9693-a31909f289e5"

requires_the_real_bucket = pytest.mark.skipif(
    not tenant_cleanup._real_bucket_is_configured(),
    reason="the STORAGE_S3_* settings are not set -- this test sweeps a file from the real bucket. SETUP.md.",
)


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_storage():
    return _load(_REPO / "packages/core/tests/storage_fake.py", "docflow_storage_fake").FakeStorage()


@pytest.fixture()
def planted():
    """Plants a tenant; the run's purge removes whatever the sweep didn't."""
    made: list[UUID] = []

    def plant(name: str, *, days_old: int = 2, **columns) -> UUID:
        tid = uuid4()
        register_test_tenant(tid)
        made.append(tid)
        values = {
            "status": "active",
            "onboarding_status": "tenant_created",
            "went_live_at": None,
            "setup_fee_billing": None,
            **columns,
        }
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO tenants (id, name, status, onboarding_status, went_live_at, "
                    "setup_fee_billing, created_at, updated_at, status_changed_at) VALUES (:id, :name, "
                    ":status, :onboarding, :went_live_at, :setup_fee_billing, "
                    "now() - make_interval(days => :days), now(), now())"
                ),
                {
                    "id": str(tid),
                    "name": name,
                    "status": values["status"],
                    "onboarding": values["onboarding_status"],
                    "went_live_at": values["went_live_at"],
                    "setup_fee_billing": values["setup_fee_billing"],
                    "days": days_old,
                },
            )
        return tid

    yield plant
    for tid in made:
        purge_test_tenant(tid)


def _name(label: str = "Target") -> str:
    return f"Acme Test Sweep {label} {uuid4().hex[:8]}"


def _exists(tid: UUID) -> bool:
    with platform_session() as session:
        found = session.execute(text("SELECT count(*) FROM tenants WHERE id = :t"), {"t": str(tid)})
        return bool(found.scalar_one())


def _sweep(tid, name, folder, bucket):
    with sweep_session("docflow-sweep test") as session:
        return purge_swept_tenant(session, tid, name, folder, bucket)


@pytest.mark.parametrize(
    "case, name, columns, typed, refusal",
    [
        ("wrong name", _name(), {}, "Acme Test Sweep Someone Else", "is not the tenant's name"),
        ("young", _name(), {"days_old": 0}, None, "less than 1 day old"),
        ("not Acme", "Example Test Sweep Target", {}, None, "doesn't start 'Acme Test'"),
        ("went live", _name(), {"went_live_at": "2026-09-01T00:00:00Z"}, None, "marked LIVE"),
        ("live", _name(), {"onboarding_status": "live", "setup_fee_billing": "invoiced_manually"}, None,
         "marked LIVE"),
        ("deleted", _name(), {"status": "deleted"}, None, "marked DELETED BY THE PRODUCT"),
        ("pending deletion", _name(), {"status": "pending_deletion"}, None, "marked PENDING DELETION"),
        ("named in the records", "Acme Test Sweep B", {}, None, "marked NAMED IN RECORDS ("),
        ("a seed's name", "Acme Test Cafe, LLC", {}, None, "marked SEED NAME"),
        ("a seed's built name", "Acme Test Live E2E 20261027", {}, None, "marked SEED NAME"),
    ],
)
def test_each_check_refuses_and_leaves_the_tenant_whole(
    planted, tmp_path, case, name, columns, typed, refusal
):
    tid = planted(name, **columns)
    folder = tmp_path / "backup"
    with pytest.raises(SweepRefused) as refused:
        _sweep(tid, typed if typed is not None else name, folder, _fake_storage())
    assert refusal in str(refused.value), case
    assert _exists(tid)
    assert not folder.exists(), "a refused sweep wrote a backup"


def test_a_record_names_a_tenant_by_its_id_alone():
    assert records_naming(_RECORDED_ID, "")
    assert records_naming(uuid4(), "") == []


@pytest.mark.parametrize("where", ["inside the repository", "not empty"])
def test_the_backup_folder_must_be_outside_the_repository_and_empty(planted, tmp_path, where):
    name = _name()
    tid = planted(name)
    if where == "inside the repository":
        folder = _REPO / f"sweep-backup-{uuid4().hex[:8]}"
        expected = "inside the repository"
    else:
        folder = tmp_path / "backup"
        folder.mkdir()
        (folder / "earlier.txt").write_text("an earlier backup\n", encoding="utf-8")
        expected = "already holds something"
    with pytest.raises(SweepRefused, match=expected):
        _sweep(tid, name, folder, _fake_storage())
    assert _exists(tid)
    if where == "inside the repository":
        assert not folder.exists()


def test_the_sweep_needs_its_own_repeatable_read_transaction(planted, tmp_path):
    name = _name()
    tid = planted(name)
    with platform_session() as session, pytest.raises(SweepRefused, match="REPEATABLE READ"):
        purge_swept_tenant(session, tid, name, tmp_path / "backup", _fake_storage())
    assert _exists(tid)


def test_the_scripts_lock_refuses_while_a_suite_holds_the_run_lock():
    """This suite holds the shared key for its whole run (suite_lock.py), so
    the sweep's exclusive attempt must fail and name the holder."""
    script = _load(_REPO / "scripts" / "sweep_test_tenants.py", "sweep_test_tenants_under_test")
    with sweep_session("docflow-sweep lock test", read_only=True) as session:
        with pytest.raises(script.SuiteLockHeld, match="holds the suites' run lock"):
            script.take_lock(session)


def test_files_that_change_after_the_backup_stop_the_sweep(planted, tmp_path):
    """A file written under the tenant between the backup and the removal
    isn't in the backup: the sweep stops and the rollback keeps every row."""
    name = _name()
    tid = planted(name)
    fake = _fake_storage()
    fake.put(f"tenants/{tid}/uploads/po.txt", b"PO Number: TEST-SWEEP\n", "text/plain")
    lists = []

    def a_late_file(op, _key):
        if op == "list":
            lists.append(op)
            if len(lists) == 2:  # the backup's list was the first
                fake.objects[f"tenants/{tid}/uploads/late.txt"] = b"written after the backup\n"

    fake.before_call = a_late_file
    with pytest.raises(SweepRefused, match="changed after the backup"):
        _sweep(tid, name, tmp_path / "backup", fake)
    assert _exists(tid)
    assert sorted(fake.objects) == [f"tenants/{tid}/uploads/late.txt", f"tenants/{tid}/uploads/po.txt"]


def test_the_listing_shows_old_acme_test_tenants_with_what_they_hold(planted):
    old, young, other = _name("Old"), _name("Young"), "Example Test Sweep Listed"
    old_id = planted(old)
    young_id = planted(young, days_old=0)
    other_id = planted(other)
    fake = _fake_storage()
    fake.put(f"tenants/{old_id}/uploads/po.txt", b"PO Number: TEST-SWEEP\n", "text/plain")
    with sweep_session("docflow-sweep listing test", read_only=True) as session:
        listing = {entry["id"]: entry for entry in sweep_listing(session, fake)}
        expected_rows = rows_left(session, old_id)
    assert str(old_id) in listing
    assert str(young_id) not in listing and str(other_id) not in listing
    entry = listing[str(old_id)]
    assert entry["name"] == old and entry["marks"] == []
    assert entry["rows"] == expected_rows == {"tenants.id": 1}
    assert entry["objects"] == 1


@requires_the_real_bucket
def test_a_swept_tenant_leaves_nothing_and_its_backup_holds_everything(planted, tmp_path):
    """A tenant with a user, a document with lines and a stored file; a second
    tenant beside it. The sweep takes the first, with its file, and leaves the
    second exactly as it was; the backup reads back to what was removed."""
    bucket = storage.S3Backend()
    name = _name()
    tenant = _TestValidationTenant(name)
    tenant.__enter__()
    tid = tenant.tenant_id
    beside = planted(_name("Beside"))
    try:
        tenant.create_document(
            lines=[{"quantity": "2", "unit_price": "5.00", "line_total": "10.00", "confidence": "0.97"}]
        )
        key = storage.save_file(tid, "po.txt", b"PO Number: TEST-SWEEP\n", content_type="text/plain")
        with platform_session() as session:
            session.execute(
                text("UPDATE tenants SET created_at = now() - make_interval(days => 2) WHERE id = :t"),
                {"t": str(tid)},
            )
            held = tenant_cleanup._holdings(session, str(tid))
            beside_before = rows_left(session, beside)
        for column in ("documents.tenant_id", "document_lines.tenant_id", "users.tenant_id"):
            assert held.get(column), f"the seed has no row in {column}: {held}"

        folder = tmp_path / "backup"
        result = _sweep(tid, name, folder, bucket)

        with platform_session() as session:
            assert rows_left(session, tid) == {}
            assert rows_left(session, beside) == beside_before
        assert bucket.list_keys(f"tenants/{tid}/") == []
        assert _exists(beside)

        assert result.rows == held
        assert result.objects == [key]
        assert result.auth_emails == [f"reviewer-{tenant.user_id.hex[:8]}@example.test"]
        for file, sha in result.files:
            assert hashlib.sha256((folder / file).read_bytes()).hexdigest() == sha, file
        assert {file for file, _sha in result.files} == {"rows.json", "manifest.json", f"storage/{key}"}
        assert (folder / "storage" / key).read_bytes() == b"PO Number: TEST-SWEEP\n"
        rows = json.loads((folder / "rows.json").read_text(encoding="utf-8"), parse_float=Decimal)
        assert {label: len(found) for label, found in rows["tables"].items()} == held
        assert rows["tables"]["document_lines.tenant_id"][0]["line_total"] == Decimal("10.00")
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["orphaned_auth_emails"] == result.auth_emails
        assert manifest["objects"][0]["sha256"] == hashlib.sha256(b"PO Number: TEST-SWEEP\n").hexdigest()
    finally:
        tenant.__exit__(None, None, None)  # the run's own purge: nothing left to remove after a sweep
