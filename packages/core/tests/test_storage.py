"""Storage path rules (CLAUDE.md Section 7.5 / 7.11)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from docflow_core.storage import UnsafeStoragePathError, build_storage_path


def test_exports_live_under_the_tenant_prefix_in_their_own_folder():
    tenant_id = uuid4()
    path = build_storage_path(tenant_id, "export.csv", area="exports")
    assert path.startswith(f"tenants/{tenant_id}/exports/")
    assert path.endswith(".csv")


def test_uploads_are_still_the_default_area():
    tenant_id = uuid4()
    assert build_storage_path(tenant_id, "po.pdf").startswith(f"tenants/{tenant_id}/uploads/")


@pytest.mark.parametrize("area", ["../other", "exports/../../x", "", "Exports"])
def test_an_area_outside_the_fixed_set_is_refused(area):
    with pytest.raises(UnsafeStoragePathError):
        build_storage_path(uuid4(), "x.csv", area=area)
