"""
Copy staging's files from the local `storage/` folder into the Supabase
Storage bucket, under the same keys (Stage 3b item 5). No database row
changes: every row already holds the key its file will have in the bucket.

    python scripts/copy_storage_to_bucket.py            # dry run: report only
    python scripts/copy_storage_to_bucket.py --apply    # copy, then verify

Run it from the repo root, on the machine that holds the local folder (the
founder's), with the root `.env` holding DATABASE_URL and the four
STORAGE_S3_* settings. Use `apps/api/.venv`'s Python, as the other scripts do.

What it does:

* Collects every path a row still references, soft-deleted rows included
  (their data is kept until a hard delete): documents.storage_path,
  preview_storage_path and extracted_text_path; exports.storage_path;
  onboarding_intake_files.storage_path; catalog_imports.storage_path.
* Checks each path with the storage layer's own tenant / intake prefix rules
  before touching it.
* For each file: if the bucket already has an identical copy, it says so; if
  not, it uploads it (with --apply), reads it back from the bucket and
  compares SHA-256 with the local file -- and with the row's own SHA-256
  where the table records one. It never overwrites a different object that
  is already in the bucket; it reports it.
* Reports: copied; already there and identical; referenced but missing
  locally (listed); local file not matching its row's hash (listed); in the
  bucket but different (listed); and the orphan count -- local files no row
  references, which are left where they are.

Safe to run as often as you like, and it is run twice in the rollout: once
before the switch, and again straight after it (the delta copy, item 11) to
pick up anything the old code wrote in between. It exits non-zero if anything
needs a human, so a clean run is a zero exit and a report with nothing listed.

The local folder is never changed or deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from docflow_core import storage
from docflow_core.config import get_settings
from docflow_core.db import platform_session
from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]

# (table, path column, hash column or None, media-type column or None)
REFERENCES = (
    ("documents", "storage_path", "content_sha256", None),
    ("documents", "preview_storage_path", None, "preview_media_type"),
    ("documents", "extracted_text_path", None, None),
    ("exports", "storage_path", "sha256", None),
    ("onboarding_intake_files", "storage_path", "sha256", None),
    ("catalog_imports", "storage_path", "file_sha256", None),
)


@dataclass
class Reference:
    key: str
    expected_sha256: str | None
    content_type: str | None
    source: str


@dataclass
class Report:
    copied: int = 0
    identical: int = 0
    would_copy: int = 0
    missing_locally: list[str] = field(default_factory=list)
    local_hash_mismatch: list[str] = field(default_factory=list)
    bucket_differs: list[str] = field(default_factory=list)
    unsafe_paths: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)

    def needs_a_human(self) -> bool:
        return bool(
            self.missing_locally
            or self.local_hash_mismatch
            or self.bucket_differs
            or self.unsafe_paths
            or self.failed
        )


def _local_root() -> Path:
    root = Path(get_settings().storage_root)
    return root if root.is_absolute() else REPO_ROOT / root


def collect_references() -> dict[str, Reference]:
    """Every referenced key, once. A key referenced twice keeps the first
    hash found for it."""
    refs: dict[str, Reference] = {}
    with platform_session() as session:
        for table, path_col, hash_col, type_col in REFERENCES:
            columns = [path_col] + [c for c in (hash_col, type_col) if c]
            rows = session.execute(
                text(f"SELECT {', '.join(columns)} FROM {table} WHERE {path_col} IS NOT NULL")
            ).mappings()
            for row in rows:
                key = row[path_col]
                if key in refs:
                    if refs[key].expected_sha256 is None and hash_col and row[hash_col]:
                        refs[key].expected_sha256 = row[hash_col]
                    continue
                refs[key] = Reference(
                    key=key,
                    expected_sha256=row[hash_col] if hash_col else None,
                    content_type=row[type_col] if type_col else None,
                    source=f"{table}.{path_col}",
                )
    return refs


def check_key(key: str) -> None:
    """The storage layer's own prefix rules: tenants/{uuid}/{area}/... or
    staging/{uuid}/... Raises UnsafeStoragePathError otherwise."""
    parts = key.split("/")
    if len(parts) < 3:
        raise storage.UnsafeStoragePathError(key)
    owner = UUID(parts[1])
    if parts[0] == "tenants":
        storage.check_tenant_path(owner, key)
    elif parts[0] == "staging":
        storage.check_staging_path(owner, key)
    else:
        raise storage.UnsafeStoragePathError(key)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(apply: bool, backend: storage.StorageBackend, refs: dict[str, Reference], root: Path) -> Report:
    report = Report()
    for key in sorted(refs):
        ref = refs[key]
        try:
            check_key(key)
        except (storage.UnsafeStoragePathError, ValueError):
            report.unsafe_paths.append(f"{key} ({ref.source})")
            continue

        local = root / key
        if not local.is_file():
            report.missing_locally.append(f"{key} ({ref.source})")
            continue
        data = local.read_bytes()
        local_sha = _sha(data)
        if ref.expected_sha256 and local_sha != ref.expected_sha256:
            report.local_hash_mismatch.append(f"{key} ({ref.source})")
            continue

        try:
            existing = backend.get(key)
        except storage.StorageObjectMissingError:
            existing = None
        except storage.StorageError as exc:
            report.failed.append(f"{key}: {type(exc).__name__}")
            continue
        if existing is not None:
            if _sha(existing) == local_sha:
                report.identical += 1
            else:
                report.bucket_differs.append(f"{key} ({ref.source})")
            continue

        if not apply:
            report.would_copy += 1
            continue
        try:
            backend.put(key, data, ref.content_type)
            if _sha(backend.get(key)) != local_sha:
                report.failed.append(f"{key}: read back differs")
                continue
        except storage.StorageError as exc:
            report.failed.append(f"{key}: {type(exc).__name__}")
            continue
        report.copied += 1

    if root.is_dir():
        referenced = set(refs)
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = path.relative_to(root).as_posix()
            if rel not in referenced:
                report.orphans.append(rel)
    return report


def print_report(report: Report, apply: bool, root: Path) -> None:
    mode = "APPLY" if apply else "DRY RUN (nothing written; add --apply to copy)"
    print(f"copy_storage_to_bucket -- {mode}")
    print(f"local folder: {root}")
    print(f"bucket:       {storage.BUCKET}")
    print()
    if apply:
        print(f"copied and verified:            {report.copied}")
    else:
        print(f"would copy:                     {report.would_copy}")
    print(f"already there and identical:    {report.identical}")
    print(f"referenced but missing locally: {len(report.missing_locally)}")
    print(f"local file != its row's hash:   {len(report.local_hash_mismatch)}")
    print(f"in the bucket but different:    {len(report.bucket_differs)}")
    print(f"path refused by prefix rules:   {len(report.unsafe_paths)}")
    print(f"failed:                         {len(report.failed)}")
    print(f"orphans (left in place):        {len(report.orphans)}")
    for title, items in (
        ("Referenced but missing locally", report.missing_locally),
        ("Local file doesn't match its row's hash", report.local_hash_mismatch),
        ("In the bucket but different (not overwritten)", report.bucket_differs),
        ("Refused by the prefix rules", report.unsafe_paths),
        ("Failed", report.failed),
    ):
        if items:
            print(f"\n{title}:")
            for item in items:
                print(f"  {item}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="copy (default: dry run)")
    args = parser.parse_args(argv)

    root = _local_root()
    refs = collect_references()
    backend = storage._get_backend()  # the same client and checks product code uses
    report = run(args.apply, backend, refs, root)
    print_report(report, args.apply, root)
    return 1 if report.needs_a_human() else 0


if __name__ == "__main__":
    sys.exit(main())
