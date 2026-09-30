"""Storage path rules (CLAUDE.md Section 7.5 / 7.11) and the Stage 3b
Storage layer: the tenant prefix checked on read, fixed derived keys, the
hard-delete prefix sweep, and how Storage failures are classified."""

from __future__ import annotations

import ast
import pathlib
from uuid import uuid4

import pytest
from botocore.stub import Stubber

from docflow_core import storage
from docflow_core.storage import (
    StorageObjectMissingError,
    StorageUnavailableError,
    UnsafeStoragePathError,
    build_storage_path,
)
from tests.storage_fake import FakeStorage

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture()
def fake():
    backend = FakeStorage()
    with storage.set_backend_for_tests(backend):
        yield backend


# ── Paths ──────────────────────────────────────────────────────────────────


def test_exports_live_under_the_tenant_prefix_in_their_own_folder():
    tenant_id = uuid4()
    path = build_storage_path(tenant_id, "export.csv", area="exports")
    assert path.startswith(f"tenants/{tenant_id}/exports/")
    assert path.endswith(".csv")


def test_uploads_are_still_the_default_area():
    tenant_id = uuid4()
    assert build_storage_path(tenant_id, "po.pdf").startswith(f"tenants/{tenant_id}/uploads/")


@pytest.mark.parametrize("area", ["../other", "exports/../../x", "", "Exports", "derived"])
def test_an_area_outside_the_write_once_set_is_refused(area):
    with pytest.raises(UnsafeStoragePathError):
        build_storage_path(uuid4(), "x.csv", area=area)


# ── Item 3: the tenant prefix is checked on read, before any network call ──


def test_tenant_a_cannot_read_a_tenant_b_path_and_storage_is_never_contacted(fake):
    """The 7.5 isolation test for files: a path under another tenant is
    refused by the layer itself, so the project-wide S3 key never sees it."""
    tenant_a, tenant_b = uuid4(), uuid4()
    b_path = storage.save_file(tenant_b, "po.pdf", b"tenant b's order")
    fake.calls.clear()

    with pytest.raises(UnsafeStoragePathError):
        storage.read_file(tenant_a, b_path)
    assert fake.calls == []


def test_another_tenants_folder_is_told_apart_from_a_malformed_path():
    """The founder's split (2026-09-30): a well-formed path under another
    tenant alerts, a malformed one only logs -- so the check must tell them
    apart, and neither may pass as a missing file."""
    tenant_a, tenant_b = uuid4(), uuid4()

    with pytest.raises(storage.CrossTenantStoragePathError) as crossed:
        storage.check_tenant_path(tenant_a, f"tenants/{tenant_b}/uploads/po.pdf")
    assert crossed.value.named_tenant_id == str(tenant_b)

    for malformed in (
        "tenants/seed/po.txt",
        f"tenants/{tenant_b}/secrets/po.pdf",
        f"tenants/{str(tenant_a).upper()}/uploads/po.pdf",
        f"staging/{tenant_b}/po.pdf",
    ):
        with pytest.raises(UnsafeStoragePathError) as refused:
            storage.check_tenant_path(tenant_a, malformed)
        assert not isinstance(refused.value, storage.CrossTenantStoragePathError), malformed

    assert not issubclass(UnsafeStoragePathError, storage.StorageError)


@pytest.mark.parametrize(
    "path_for",
    [
        lambda t: f"tenants/{t}/uploads/../../{uuid4()}/uploads/x.pdf",
        lambda t: f"tenants/{t}/uploads//x.pdf",
        lambda t: f"tenants/{t}/uploads/./x.pdf",
        lambda t: f"tenants/{t}\\uploads\\x.pdf",
        lambda t: f"tenants/{t}/secrets/x.pdf",
        lambda t: f"tenants/{t}/uploads",
        lambda t: f"/tenants/{t}/uploads/x.pdf",
        lambda t: f"staging/{t}/x.pdf",
        lambda t: f"tenants/{t}/uploads/x\x00.pdf",
        lambda t: f"tenants/{str(t).upper()}/uploads/x.pdf",
        lambda t: "",
    ],
)
def test_malformed_or_foreign_paths_are_refused_before_storage(fake, path_for):
    tenant = uuid4()
    with pytest.raises(UnsafeStoragePathError):
        storage.read_file(tenant, path_for(tenant))
    with pytest.raises(UnsafeStoragePathError):
        storage.delete_tenant_file(tenant, path_for(tenant))
    assert fake.calls == []


def test_a_staging_file_is_reachable_only_through_its_own_intake(fake):
    intake, other_intake, tenant = uuid4(), uuid4(), uuid4()
    path = storage.save_staging_file(intake, "catalog.xlsx", b"rows")
    assert storage.read_staging_file(intake, path) == b"rows"
    fake.calls.clear()
    with pytest.raises(UnsafeStoragePathError):
        storage.read_staging_file(other_intake, path)
    with pytest.raises(UnsafeStoragePathError):
        storage.read_file(tenant, path)
    assert fake.calls == []


def test_a_tenant_reads_its_own_file(fake):
    tenant = uuid4()
    path = storage.save_file(tenant, "po.pdf", b"%PDF-", content_type="application/pdf")
    assert storage.read_file(tenant, path) == b"%PDF-"
    assert fake.content_types[path] == "application/pdf"


# ── Item 4: fixed keys for derived files ───────────────────────────────────


def test_a_retried_preview_overwrites_its_fixed_key_and_leaves_one_object(fake):
    tenant, document = uuid4(), uuid4()
    first = storage.save_derived(tenant, document, "preview", b"v1", content_type="image/png")
    second = storage.save_derived(tenant, document, "preview", b"v2", content_type="image/png")
    assert first == second == f"tenants/{tenant}/derived/{document}/preview"
    assert [k for k in fake.objects if "/derived/" in k] == [first]
    assert storage.read_file(tenant, first) == b"v2"


def test_extracted_text_has_its_own_fixed_key(fake):
    tenant, document = uuid4(), uuid4()
    path = storage.save_derived(tenant, document, "extracted_text", b"text")
    assert path == f"tenants/{tenant}/derived/{document}/extracted.txt"


def test_an_unknown_derived_kind_is_refused():
    with pytest.raises(UnsafeStoragePathError):
        storage.derived_path(uuid4(), uuid4(), "../../x")


# ── Item 8: tenant creation copies staging files server side ───────────────


def test_copy_into_tenant_uses_a_server_side_copy_under_the_new_prefix(fake):
    intake, tenant = uuid4(), uuid4()
    original = storage.save_staging_file(intake, "catalog.xlsx", b"rows")
    copied = storage.copy_into_tenant(intake, original, tenant, area="onboarding")
    assert copied.startswith(f"tenants/{tenant}/onboarding/") and copied.endswith(".xlsx")
    assert ("copy", original) in fake.calls
    assert storage.read_file(tenant, copied) == b"rows"
    assert original in fake.objects  # a copy, so creation can still roll back


def test_copy_into_tenant_refuses_a_path_outside_the_named_intake(fake):
    with pytest.raises(UnsafeStoragePathError):
        storage.copy_into_tenant(uuid4(), f"tenants/{uuid4()}/uploads/x.pdf", uuid4(), area="onboarding")
    assert fake.calls == []


# ── Item 7: the hard-delete prefix sweep ───────────────────────────────────


def test_delete_tenant_storage_removes_only_that_tenant_and_counts(fake):
    doomed, kept = uuid4(), uuid4()
    for _ in range(3):
        storage.save_file(doomed, "po.pdf", b"x")
    storage.save_derived(doomed, uuid4(), "preview", b"p")
    keep = storage.save_file(kept, "po.pdf", b"y")
    assert storage.delete_tenant_storage(doomed) == 4
    assert list(fake.objects) == [keep]
    assert storage.delete_tenant_storage(doomed) == 0  # safe to run again


def test_delete_tenant_storage_raises_if_anything_is_left(fake):
    tenant = uuid4()
    stuck = storage.save_file(tenant, "po.pdf", b"x")
    fake.undeletable.add(stuck)
    with pytest.raises(StorageUnavailableError):
        storage.delete_tenant_storage(tenant)


# ── Item 1: the S3 client and how failures are classified ──────────────────


@pytest.fixture()
def s3(monkeypatch):
    monkeypatch.setenv("STORAGE_S3_ENDPOINT", "https://example-ref.storage.supabase.co/storage/v1/s3")
    monkeypatch.setenv("STORAGE_S3_REGION", "us-east-1")
    monkeypatch.setenv("STORAGE_S3_ACCESS_KEY_ID", "test-key-id")
    monkeypatch.setenv("STORAGE_S3_SECRET_ACCESS_KEY", "test-secret")
    from docflow_core.config import get_settings

    get_settings.cache_clear()
    backend = storage.S3Backend()
    yield backend
    get_settings.cache_clear()


def test_the_client_is_configured_for_supabase(s3):
    config = s3._client.meta.config
    assert config.connect_timeout == 5 and config.read_timeout == 30
    assert config.retries == {"total_max_attempts": 3, "mode": "standard"}  # 3 tries in all
    assert config.s3["addressing_style"] == "path"
    # Supabase has no S3 upload checksums; boto3 sends one by default.
    assert config.request_checksum_calculation == "when_required"


def test_a_missing_object_is_a_data_fault_not_an_outage(s3):
    with Stubber(s3._client) as stub:
        stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
        with pytest.raises(StorageObjectMissingError):
            s3.get("tenants/x/uploads/y.pdf")


@pytest.mark.parametrize(
    "code,status",
    [("InternalError", 500), ("SlowDown", 503), ("AccessDenied", 403), ("NoSuchBucket", 404)],
)
def test_anything_else_is_unavailable(s3, code, status):
    with Stubber(s3._client) as stub:
        stub.add_client_error("get_object", service_error_code=code, http_status_code=status)
        with pytest.raises(StorageUnavailableError):
            s3.get("tenants/x/uploads/y.pdf")


def test_a_partial_batch_delete_raises(s3):
    with Stubber(s3._client) as stub:
        stub.add_response(
            "delete_objects",
            {"Errors": [{"Key": "tenants/x/uploads/a", "Code": "InternalError", "Message": "m"}]},
        )
        with pytest.raises(StorageUnavailableError):
            s3.delete_keys(["tenants/x/uploads/a"])


def test_missing_configuration_fails_safe_as_unavailable(monkeypatch):
    for name in (
        "STORAGE_S3_ENDPOINT",
        "STORAGE_S3_REGION",
        "STORAGE_S3_ACCESS_KEY_ID",
        "STORAGE_S3_SECRET_ACCESS_KEY",
    ):
        monkeypatch.setenv(name, "")
    from docflow_core.config import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(StorageUnavailableError):
            storage.S3Backend()
    finally:
        get_settings.cache_clear()


# ── The fake is test-only ──────────────────────────────────────────────────


def test_product_code_never_imports_the_storage_fake():
    offenders = []
    for root in ("packages/core/docflow_core", "apps/api/app", "apps/worker/app", "scripts"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                if any("storage_fake" in n for n in names):
                    offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_no_product_code_writes_to_the_local_storage_folder():
    """From the switch on, nothing reads or writes the local folder except
    the copy script (item 11)."""
    offenders = []
    for root in ("packages/core/docflow_core", "apps/api/app", "apps/worker/app"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            if "storage_root" in path.read_text(encoding="utf-8") and path.name != "config.py":
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []
