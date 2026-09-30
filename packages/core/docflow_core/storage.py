"""
Document blob storage: one private Supabase Storage bucket, reached through
its S3-compatible endpoint (Stage 3b; CLAUDE.md Section 7.5: "storage paths
are `tenants/{tenant_id}/...` and the same layer enforces the prefix";
Section 7.11: filenames are untrusted, storage paths are generated
server-side).

Tenant isolation for files lives here, not in the key. The S3 access key is
project-wide and bypasses RLS (Supabase's own documentation), so every call
that names a path checks it against the caller's tenant (or intake) BEFORE
any network call:

* `read_file(tenant_id, path)` / `delete_tenant_file(tenant_id, path)`: the
  path must be `tenants/{that tenant}/{uploads|exports|onboarding|derived}/
  ...`, with no `..`, no `.`, no empty segment, no backslash and no control
  character. Anything else raises `UnsafeStoragePathError` and Storage is
  never contacted.
* `read_staging_file(intake_id, path)` / `delete_staging_file(...)`: the same
  for `staging/{that intake}/...`. It is the only way to reach a staging
  file.

Failures are split in two, because callers must treat them differently:

* `StorageUnavailableError`: Storage could not be reached or answered with
  anything other than "that object does not exist" (timeouts, connection
  errors, 5xx, throttling, a missing bucket, a refused key). An outage:
  retrying later is the right answer.
* `StorageObjectMissingError`: Storage answered, and the object is not
  there. A data fault, never an outage: waiting will not bring it back.

Every write goes to a key nobody else writes (a fresh random name, or a
derived file's fixed key that only its own document's task writes), so every
call is safe to repeat and the client retries all of them (item 1).

The backend is swappable only for tests: `set_backend_for_tests` takes the
in-memory fake that lives in the test folders. Product code never imports it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol
from uuid import UUID, uuid4

from docflow_core.config import get_settings

logger = logging.getLogger(__name__)

BUCKET = "docflow-files"


class UnsafeStoragePathError(Exception):
    """A path the prefix rules refuse. Never sent to Storage. Deliberately not
    a StorageError: a refused path is a bug or bad data, not a missing file
    or an outage, and the log must be able to tell them apart."""


class CrossTenantStoragePathError(UnsafeStoragePathError):
    """A well-formed path under a *different* tenant's folder. Section 7.5's
    isolation rule caught something, so callers alert the founder
    (`founder_alerts.report_refused_storage_path`), not only log it."""

    def __init__(self, message: str, *, named_tenant_id: str) -> None:
        super().__init__(message)
        self.named_tenant_id = named_tenant_id


class StorageError(Exception):
    """Base for a failed Storage call. Never shown to a tenant as-is: callers
    map it to a catalog code (Section 7.16.5)."""


class StorageUnavailableError(StorageError):
    """Storage could not be reached, or refused for a reason that is not
    'no such object'. An outage: try again later."""


class StorageObjectMissingError(StorageError):
    """Storage answered and the object does not exist. A data fault."""


# ── Paths ──────────────────────────────────────────────────────────────────


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
# a path segment: `uploads` holds what arrived, `exports` holds files DocFlow
# generated from an approved snapshot (Section 7.4), `onboarding` holds a
# prospect's files moved out of staging when their tenant is created (Section
# 7.15.2 Step 2), and `derived` holds each document's preview and extracted
# text under fixed keys (Stage 3b item 4).
STORAGE_AREAS: frozenset[str] = frozenset({"uploads", "exports", "onboarding", "derived"})

# Areas written once under a fresh random name. `derived` is not one: its
# keys are fixed per document (derived_path).
WRITE_ONCE_AREAS: frozenset[str] = frozenset({"uploads", "exports", "onboarding"})

DERIVED_KINDS: dict[str, str] = {"preview": "preview", "extracted_text": "extracted.txt"}


def build_storage_path(tenant_id: UUID, original_filename: str, *, area: str = "uploads") -> str:
    """
    Generates a server-side storage path under the mandatory tenant prefix.
    Never derived from user input beyond a sanitized extension.
    """
    if area not in WRITE_ONCE_AREAS:
        raise UnsafeStoragePathError(f"Unknown storage area: {area!r}")
    ext = _safe_extension(original_filename)
    return f"tenants/{tenant_id}/{area}/{uuid4().hex}{ext}"


def derived_path(tenant_id: UUID, document_id: UUID, kind: str) -> str:
    """The fixed key of a document's preview or extracted text. A retry
    overwrites the same key, so it never leaves an orphan (item 4)."""
    if kind not in DERIVED_KINDS:
        raise UnsafeStoragePathError(f"Unknown derived kind: {kind!r}")
    return f"tenants/{tenant_id}/derived/{UUID(str(document_id))}/{DERIVED_KINDS[kind]}"


def build_staging_path(intake_id: UUID, original_filename: str) -> str:
    return f"staging/{intake_id}/{uuid4().hex}{_safe_extension(original_filename)}"


def _check_segments(storage_path: str) -> list[str]:
    if not isinstance(storage_path, str) or not storage_path:
        raise UnsafeStoragePathError("Empty storage path")
    if "\\" in storage_path or any(ord(c) < 32 or ord(c) == 127 for c in storage_path):
        raise UnsafeStoragePathError(f"Storage path has a forbidden character: {storage_path!r}")
    segments = storage_path.split("/")
    if any(s in ("", ".", "..") for s in segments):
        raise UnsafeStoragePathError(f"Storage path has an empty or relative segment: {storage_path!r}")
    return segments


def check_tenant_path(tenant_id: UUID, storage_path: str) -> str:
    """
    Section 7.5's prefix rule, on read and delete as well as write: the path
    must sit under `tenants/{tenant_id}/{area}/` for THIS tenant. Raises
    before anything is sent to Storage.
    """
    segments = _check_segments(storage_path)
    own = str(UUID(str(tenant_id)))
    well_formed = len(segments) >= 4 and segments[0] == "tenants" and segments[2] in STORAGE_AREAS
    if well_formed and segments[1] == own:
        return storage_path
    if well_formed and _is_uuid(segments[1]):
        raise CrossTenantStoragePathError(
            f"Storage path is under another tenant's prefix: tenant={tenant_id} path={storage_path!r}",
            named_tenant_id=segments[1],
        )
    raise UnsafeStoragePathError(
        f"Storage path is not under this tenant's prefix: tenant={tenant_id} path={storage_path!r}"
    )


def _is_uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def check_staging_path(intake_id: UUID, storage_path: str) -> str:
    segments = _check_segments(storage_path)
    if len(segments) < 3 or segments[0] != "staging" or segments[1] != str(UUID(str(intake_id))):
        raise UnsafeStoragePathError(
            f"Storage path is not under this intake's prefix: intake={intake_id} path={storage_path!r}"
        )
    return storage_path


# ── Backend ────────────────────────────────────────────────────────────────


class StorageBackend(Protocol):
    def put(self, key: str, content: bytes, content_type: str | None) -> None: ...
    def get(self, key: str) -> bytes: ...
    def copy(self, source_key: str, dest_key: str) -> None: ...
    def delete(self, key: str) -> None: ...
    def list_keys(self, prefix: str) -> list[str]: ...
    def delete_keys(self, keys: list[str]) -> None: ...


# Error codes that mean "that object is not there". NoSuchBucket is also a
# 404 but is configuration, not data: it is an outage (Unavailable).
_MISSING_CODES = frozenset({"NoSuchKey", "NotFound", "404"})


def _label_body_as_xml(request, **_kwargs) -> None:  # type: ignore[no-untyped-def]
    request.headers["Content-Type"] = "application/xml"


class S3Backend:
    """
    boto3 against Supabase Storage's S3 endpoint.

    * 5 s connect and 30 s read timeouts, up to 3 tries ("standard" retry
      mode: connection errors, 5xx and throttling), so the worst case is about
      two minutes -- inside every Stage 3a time limit (item 1).
    * Path-style addressing (Supabase requires it).
    * Checksums only when an operation requires them: Supabase does not
      support S3 upload checksums, and current boto3 sends one on every PUT
      by default.
    """

    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        settings = get_settings()
        missing = [
            name
            for name, value in (
                ("STORAGE_S3_ENDPOINT", settings.storage_s3_endpoint),
                ("STORAGE_S3_REGION", settings.storage_s3_region),
                ("STORAGE_S3_ACCESS_KEY_ID", settings.storage_s3_access_key_id),
                ("STORAGE_S3_SECRET_ACCESS_KEY", settings.storage_s3_secret_access_key),
            )
            if not value
        ]
        if missing:
            # Configuration, not an outage -- but a caller must still fail
            # safe (no document lost), so it surfaces as Unavailable and the
            # log names exactly what is missing.
            logger.error("storage_not_configured missing=%s", ",".join(missing))
            raise StorageUnavailableError(f"Storage is not configured: {', '.join(missing)}")
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.storage_s3_endpoint,
            region_name=settings.storage_s3_region,
            aws_access_key_id=settings.storage_s3_access_key_id,
            aws_secret_access_key=settings.storage_s3_secret_access_key,
            config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=30,
                retries={"total_max_attempts": 3, "mode": "standard"},
                s3={"addressing_style": "path"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )
        # Supabase reads a DeleteObjects body only when it is labelled XML;
        # boto3 sends none, and Supabase answers 400 "must have required
        # property 'Body'" (found on staging, 2026-09-30).
        self._client.meta.events.register("before-sign.s3.DeleteObjects", _label_body_as_xml)

    @staticmethod
    def _translate(exc: Exception, op: str, key: str) -> StorageError:
        from botocore.exceptions import ClientError

        if isinstance(exc, ClientError):
            error = exc.response.get("Error", {}) or {}
            code = str(error.get("Code", ""))
            status = (exc.response.get("ResponseMetadata", {}) or {}).get("HTTPStatusCode")
            if code in _MISSING_CODES or (status == 404 and code != "NoSuchBucket"):
                return StorageObjectMissingError(f"{op} {key}: not found")
            logger.error("storage_call_failed op=%s code=%s status=%s", op, code, status)
            return StorageUnavailableError(f"{op} {key}: {code or status}")
        logger.error("storage_call_failed op=%s error_type=%s", op, type(exc).__name__)
        return StorageUnavailableError(f"{op} {key}: {type(exc).__name__}")

    def _call(self, op: str, key: str, fn, **kwargs):  # type: ignore[no-untyped-def]
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return fn(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            raise self._translate(exc, op, key) from exc

    def put(self, key: str, content: bytes, content_type: str | None) -> None:
        kwargs: dict[str, object] = {"Bucket": BUCKET, "Key": key, "Body": content}
        if content_type:
            kwargs["ContentType"] = content_type
        self._call("put", key, self._client.put_object, **kwargs)

    def get(self, key: str) -> bytes:
        from botocore.exceptions import BotoCoreError

        response = self._call("get", key, self._client.get_object, Bucket=BUCKET, Key=key)
        try:
            return response["Body"].read()
        except BotoCoreError as exc:  # the stream dropped mid-read
            raise self._translate(exc, "get", key) from exc

    def copy(self, source_key: str, dest_key: str) -> None:
        self._call(
            "copy",
            source_key,
            self._client.copy_object,
            Bucket=BUCKET,
            Key=dest_key,
            CopySource={"Bucket": BUCKET, "Key": source_key},
        )

    def delete(self, key: str) -> None:
        # S3 DELETE of a missing key succeeds: the goal state is "absent".
        self._call("delete", key, self._client.delete_object, Bucket=BUCKET, Key=key)

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, object] = {"Bucket": BUCKET, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kwargs["ContinuationToken"] = token
            page = self._call("list", prefix, self._client.list_objects_v2, **kwargs)
            keys.extend(obj["Key"] for obj in page.get("Contents", []) or [])
            if not page.get("IsTruncated"):
                return keys
            token = page.get("NextContinuationToken")

    def delete_keys(self, keys: list[str]) -> None:
        for start in range(0, len(keys), 1000):
            batch = keys[start : start + 1000]
            response = self._call(
                "delete_many",
                batch[0],
                self._client.delete_objects,
                Bucket=BUCKET,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )
            errors = response.get("Errors") or []
            if errors:
                logger.error("storage_delete_many_partial failed=%d", len(errors))
                raise StorageUnavailableError(f"delete_many: {len(errors)} objects not deleted")


_backend: StorageBackend | None = None


def _get_backend() -> StorageBackend:
    global _backend
    if _backend is None:
        _backend = S3Backend()
    return _backend


@contextmanager
def set_backend_for_tests(backend: StorageBackend) -> Iterator[StorageBackend]:
    """Tests only: swap in the in-memory fake from the test folders."""
    global _backend
    previous = _backend
    _backend = backend
    try:
        yield backend
    finally:
        _backend = previous


# ── Tenant files ───────────────────────────────────────────────────────────


def save_file(
    tenant_id: UUID,
    original_filename: str,
    content: bytes,
    *,
    area: str = "uploads",
    content_type: str | None = None,
) -> str:
    """Writes `content` under a fresh server-generated path and returns that path."""
    storage_path = build_storage_path(tenant_id, original_filename, area=area)
    check_tenant_path(tenant_id, storage_path)
    _get_backend().put(storage_path, content, content_type)
    return storage_path


def save_derived(
    tenant_id: UUID, document_id: UUID, kind: str, content: bytes, *, content_type: str | None = None
) -> str:
    """Writes a document's preview or extracted text to its fixed key."""
    storage_path = derived_path(tenant_id, document_id, kind)
    check_tenant_path(tenant_id, storage_path)
    _get_backend().put(storage_path, content, content_type)
    return storage_path


def read_file(tenant_id: UUID, storage_path: str) -> bytes:
    """Read one of THIS tenant's files. A path under any other prefix raises
    UnsafeStoragePathError without contacting Storage (item 3)."""
    check_tenant_path(tenant_id, storage_path)
    return _get_backend().get(storage_path)


def delete_tenant_file(tenant_id: UUID, storage_path: str) -> None:
    """Remove one of this tenant's objects. Missing is fine."""
    check_tenant_path(tenant_id, storage_path)
    _get_backend().delete(storage_path)


# ── Pre-tenant staging (Section 7.15.2 Step 1) ──────────────────────────────
#
# A prospect's files arrive before any tenant exists, so they cannot live
# under a tenant prefix. They live under `staging/{intake_id}/`, which no
# tenant path can ever reach, until tenant creation copies them across.


def save_staging_file(intake_id: UUID, original_filename: str, content: bytes) -> str:
    storage_path = build_staging_path(intake_id, original_filename)
    check_staging_path(intake_id, storage_path)
    _get_backend().put(storage_path, content, None)
    return storage_path


def read_staging_file(intake_id: UUID, storage_path: str) -> bytes:
    check_staging_path(intake_id, storage_path)
    return _get_backend().get(storage_path)


def delete_staging_file(intake_id: UUID, storage_path: str) -> None:
    check_staging_path(intake_id, storage_path)
    _get_backend().delete(storage_path)


def copy_into_tenant(intake_id: UUID, storage_path: str, tenant_id: UUID, *, area: str) -> str:
    """
    Copy a staging file under a tenant's prefix (Storage's server-side copy)
    and return the new path. A copy, not a move, so tenant creation can undo
    it: the caller deletes the copy if its transaction fails, or the
    original once it has committed (item 8).
    """
    check_staging_path(intake_id, storage_path)
    new_path = build_storage_path(tenant_id, storage_path, area=area)
    check_tenant_path(tenant_id, new_path)
    _get_backend().copy(storage_path, new_path)
    return new_path


# ── Tenant hard delete (Section 7.14; Stage 3b item 7) ─────────────────────


def delete_tenant_storage(tenant_id: UUID) -> int:
    """
    Remove every object under `tenants/{tenant_id}/` and confirm the prefix
    is empty. Returns how many objects were removed. Raises (Unavailable) if
    anything is left, so the caller can stop before touching the database.
    Safe to run again. Only called from
    `docflow_core.admin_data_access.delete_tenant`, itself reachable only
    after the founder types the tenant's name (Section 7.15.4).
    """
    prefix = f"tenants/{UUID(str(tenant_id))}/"
    backend = _get_backend()
    keys = backend.list_keys(prefix)
    if keys:
        backend.delete_keys(keys)
    remaining = backend.list_keys(prefix)
    if remaining:
        raise StorageUnavailableError(f"{len(remaining)} objects still under {prefix}")
    return len(keys)
