"""
scripts/copy_storage_to_bucket.py (Stage 3b item 5): dry run, apply,
verification, the problem lists and the orphan count -- against the
in-memory fake and a temporary local folder. The database side (which rows
it collects) runs against staging when the founder runs the script.
"""

from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import sys
from uuid import uuid4

import pytest

from tests.storage_fake import FakeStorage

_SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "copy_storage_to_bucket.py"
_spec = importlib.util.spec_from_file_location("copy_storage_to_bucket", _SCRIPT)
copy_script = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = copy_script  # dataclasses look their module up by name
_spec.loader.exec_module(copy_script)
Reference = copy_script.Reference


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture()
def local(tmp_path):
    def write(key: str, data: bytes) -> str:
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    return tmp_path, write


def _ref(key, data=None, content_type=None):
    return Reference(key=key, expected_sha256=_sha(data) if data is not None else None,
                     content_type=content_type, source="documents.storage_path")


def test_a_dry_run_writes_nothing_and_says_what_it_would_copy(local):
    root, write = local
    tenant = uuid4()
    key = write(f"tenants/{tenant}/uploads/a.pdf", b"order")
    fake = FakeStorage()
    report = copy_script.run(False, fake, {key: _ref(key, b"order")}, root)
    assert report.would_copy == 1 and report.copied == 0
    assert fake.objects == {}
    assert not report.needs_a_human()


def test_apply_copies_under_the_same_key_and_a_second_run_finds_it_identical(local):
    root, write = local
    tenant = uuid4()
    key = write(f"tenants/{tenant}/derived/{uuid4()}/preview", b"png")
    refs = {key: _ref(key, content_type="image/png")}
    fake = FakeStorage()

    first = copy_script.run(True, fake, refs, root)
    assert first.copied == 1 and fake.objects[key] == b"png"
    assert fake.content_types[key] == "image/png"

    second = copy_script.run(True, fake, refs, root)  # the delta copy
    assert second.copied == 0 and second.identical == 1


def test_a_copy_that_reads_back_differently_is_a_failure(local):
    root, write = local
    key = write(f"tenants/{uuid4()}/uploads/a.pdf", b"order")

    class Corrupting(FakeStorage):
        def put(self, k, content, content_type):
            super().put(k, content + b"!", content_type)

    report = copy_script.run(True, Corrupting(), {key: _ref(key, b"order")}, root)
    assert report.copied == 0 and report.failed and report.needs_a_human()


def test_problems_are_listed_and_nothing_is_overwritten(local):
    root, write = local
    tenant, intake = uuid4(), uuid4()
    gone = f"tenants/{tenant}/uploads/gone.pdf"
    tampered = write(f"tenants/{tenant}/uploads/t.pdf", b"not what was received")
    differs = write(f"staging/{intake}/c.csv", b"local version")
    foreign = write(f"elsewhere/{tenant}/x.pdf", b"x")
    fake = FakeStorage()
    fake.objects[differs] = b"bucket version"

    refs = {
        gone: _ref(gone, b"anything"),
        tampered: _ref(tampered, b"the received bytes"),
        differs: _ref(differs),
        foreign: _ref(foreign),
    }
    report = copy_script.run(True, fake, refs, root)

    assert [e.split(" ")[0] for e in report.missing_locally] == [gone]
    assert [e.split(" ")[0] for e in report.local_hash_mismatch] == [tampered]
    assert [e.split(" ")[0] for e in report.bucket_differs] == [differs]
    assert [e.split(" ")[0] for e in report.unsafe_paths] == [foreign]
    assert fake.objects[differs] == b"bucket version"
    assert report.copied == 0 and report.needs_a_human()


def test_orphans_are_counted_and_left_in_place(local):
    root, write = local
    tenant = uuid4()
    kept = write(f"tenants/{tenant}/uploads/kept.pdf", b"k")
    orphan = write(f"tenants/{tenant}/uploads/orphan.pdf", b"o")
    report = copy_script.run(True, FakeStorage(), {kept: _ref(kept, b"k")}, root)
    assert report.orphans == [orphan]
    assert (root / orphan).exists()
    assert not report.needs_a_human()  # orphans are reported, not a failure


def test_a_storage_outage_is_a_failure_not_a_crash(local):
    root, write = local
    key = write(f"tenants/{uuid4()}/uploads/a.pdf", b"order")
    fake = FakeStorage()
    fake.fail_with = "unavailable"
    report = copy_script.run(True, fake, {key: _ref(key, b"order")}, root)
    assert report.failed and report.needs_a_human()
