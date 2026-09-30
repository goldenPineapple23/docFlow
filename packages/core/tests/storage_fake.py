"""
In-memory stand-in for Supabase Storage, for unit tests that must not touch
the network (Stage 3b item 10). Lives in the test folders only; product code
never imports it (test_storage.py asserts that). The api and worker suites
load this same file by path (see their conftest.py), so there is one fake.

It records every call, so a test can assert Storage was never contacted,
and it can be told to fail like an outage or like a missing object.
"""

from __future__ import annotations

from docflow_core.storage import StorageObjectMissingError, StorageUnavailableError


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str | None] = {}
        self.calls: list[tuple[str, str]] = []
        # Set to "unavailable" to make every call fail like an outage.
        self.fail_with: str | None = None
        # Keys whose delete silently does nothing (a partial batch failure).
        self.undeletable: set[str] = set()
        # Called before each call; lets a test write something mid-operation.
        self.before_call = None

    def _enter(self, op: str, key: str) -> None:
        self.calls.append((op, key))
        if self.before_call is not None:
            self.before_call(op, key)
        if self.fail_with == "unavailable":
            raise StorageUnavailableError(f"{op} {key}: fake outage")

    def put(self, key: str, content: bytes, content_type: str | None) -> None:
        self._enter("put", key)
        self.objects[key] = bytes(content)
        self.content_types[key] = content_type

    def get(self, key: str) -> bytes:
        self._enter("get", key)
        if key not in self.objects:
            raise StorageObjectMissingError(f"get {key}: not found")
        return self.objects[key]

    def copy(self, source_key: str, dest_key: str) -> None:
        self._enter("copy", source_key)
        if source_key not in self.objects:
            raise StorageObjectMissingError(f"copy {source_key}: not found")
        self.objects[dest_key] = self.objects[source_key]
        self.content_types[dest_key] = self.content_types.get(source_key)

    def delete(self, key: str) -> None:
        self._enter("delete", key)
        if key not in self.undeletable:
            self.objects.pop(key, None)

    def list_keys(self, prefix: str) -> list[str]:
        self._enter("list", prefix)
        return sorted(k for k in self.objects if k.startswith(prefix))

    def delete_keys(self, keys: list[str]) -> None:
        self._enter("delete_many", keys[0] if keys else "")
        for key in keys:
            if key not in self.undeletable:
                self.objects.pop(key, None)
