"""
Document blob storage (CLAUDE.md Section 7.5: "storage paths are
`tenants/{tenant_id}/...` and the same layer enforces the prefix";
Section 7.11: filenames are untrusted, storage paths are generated
server-side).

Phase 1 stand-in: local filesystem under `storage_root` (see
docflow_core.config and DECISIONS.md) rather than real Supabase Storage --
no Storage bucket/client wiring exists anywhere in this codebase yet, and
standing one up is out of scope for this slice. The path convention and
prefix enforcement below are written so swapping in a real Supabase Storage
client later is a change to this module only, not to any caller.

Every path this module returns is server-generated (`tenants/{tenant_id}/
uploads/{uuid}{ext}`) -- the original filename is never used in a path, per
Section 7.11.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from docflow_core.config import get_settings


class UnsafeStoragePathError(Exception):
    pass


def _storage_root() -> Path:
    settings = get_settings()
    root = Path(settings.storage_root)
    if not root.is_absolute():
        # Resolve relative to the repo root (this file lives at
        # <repo_root>/packages/core/docflow_core/storage.py) so it's stable
        # regardless of which process/cwd calls in.
        root = Path(__file__).resolve().parents[3] / root
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_extension(original_filename: str) -> str:
    """Only a short, alphanumeric extension is preserved -- never the raw filename."""
    idx = original_filename.rfind(".")
    if idx == -1:
        return ""
    ext = original_filename[idx:].lower()
    if len(ext) > 10 or not all(c.isalnum() or c == "." for c in ext):
        return ""
    return ext


# The folders under a tenant's prefix. A fixed set, so a caller cannot invent
# a path segment: `uploads` holds what arrived (and previews of it), `exports`
# holds files DocFlow generated from an approved snapshot (Section 7.4).
STORAGE_AREAS: frozenset[str] = frozenset({"uploads", "exports"})


def build_storage_path(tenant_id: UUID, original_filename: str, *, area: str = "uploads") -> str:
    """
    Generates a server-side storage path under the mandatory tenant prefix.
    Never derived from user input beyond a sanitized extension.
    """
    if area not in STORAGE_AREAS:
        raise UnsafeStoragePathError(f"Unknown storage area: {area!r}")
    ext = _safe_extension(original_filename)
    return f"tenants/{tenant_id}/{area}/{uuid4().hex}{ext}"


def _resolve(storage_path: str) -> Path:
    root = _storage_root()
    resolved = (root / storage_path).resolve()
    if root not in resolved.parents and resolved != root:
        raise UnsafeStoragePathError(f"Storage path escapes storage root: {storage_path!r}")
    return resolved


def save_file(
    tenant_id: UUID, original_filename: str, content: bytes, *, area: str = "uploads"
) -> str:
    """Writes `content` under a fresh server-generated path and returns that path."""
    storage_path = build_storage_path(tenant_id, original_filename, area=area)
    full_path = _resolve(storage_path)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(content)
    return storage_path


def read_file(storage_path: str) -> bytes:
    return _resolve(storage_path).read_bytes()
