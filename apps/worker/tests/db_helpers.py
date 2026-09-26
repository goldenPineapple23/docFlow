"""
Real-database helpers for the worker's own tests (review M14: until Phase 5.5
the worker's SQL -- where C1, H3 and M3 lived -- had no test against a real
database; every worker test patched `tenant_session` out).

`WorkerTestTenant` is a throwaway tenant. `run_extraction` drives the real
`parse_and_extract` task end to end -- storage, the file allowlist, the
content builder, `extract_document`'s own response parsing, every DB write,
matching and validation -- with only the Anthropic client replaced by a fake
that answers with a payload the test chose.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from docflow_core import storage
from docflow_core.db import platform_session
from sqlalchemy import text

PO_TEXT = b"PURCHASE ORDER\nPO Number: WT-1001\nBuyer: Acme Test Distributor\n"


def model_payload(
    *,
    header: dict[str, Any] | None = None,
    lines: list[dict[str, Any]] | None = None,
    header_confidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A structured-output answer in the exact shape RESPONSE_SCHEMA fixes."""
    base_header: dict[str, Any] = {
        "po_number": "WT-1001",
        "order_date": "2026-03-14",
        "requested_delivery_date": None,
        "buyer_name": "Acme Test Distributor",
        "buyer_contact_email": None,
        "ship_to_address": None,
        "payment_terms": None,
        "order_total": None,
        "currency": "USD",
        "notes": None,
    }
    base_header.update(header or {})
    confidence = {name: 0.99 for name in base_header}
    confidence.update(header_confidence or {})
    items = []
    for number, line in enumerate(lines or [], start=1):
        item = {
            "line_number": number,
            "sku": f"WT-{number:03d}",
            "description": f"Test widget {number}",
            "quantity": None,
            "unit": "EA",
            "unit_price": None,
            "line_total": None,
            "confidence": 0.99,
        }
        item.update(line)
        items.append(item)
    return {
        "header": base_header,
        "header_confidence": confidence,
        "line_items": items,
        "document_notes": "",
        "injection_suspected": False,
        "currency_inferred": False,
    }


@dataclass
class FakeAnthropic:
    """Stands in for `anthropic.Anthropic`; answers every call with `payload`."""

    payload: dict[str, Any]
    stop_reason: str = "end_turn"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, *args: Any, **kwargs: Any) -> "FakeAnthropic":
        return self

    @property
    def messages(self) -> "FakeAnthropic":
        return self

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
            stop_reason=self.stop_reason,
        )


class WorkerTestTenant:
    """A throwaway tenant, removed with everything it owns on exit."""

    def __init__(self, name: str):
        self.name = name
        self.tenant_id = uuid4()
        self.user_id = uuid4()

    def __enter__(self) -> "WorkerTestTenant":
        try:
            with platform_session() as session:
                session.execute(
                    text(
                        "INSERT INTO tenants (id, name, status, onboarding_status, created_at, "
                        "updated_at, status_changed_at) "
                        "VALUES (:id, :name, 'active', 'tenant_created', now(), now(), now())"
                    ),
                    {"id": str(self.tenant_id), "name": self.name},
                )
                session.execute(
                    text(
                        "INSERT INTO users (id, tenant_id, email, role, created_at, updated_at) "
                        "VALUES (:id, :tid, :email, 'owner', now(), now())"
                    ),
                    {
                        "id": str(self.user_id),
                        "tid": str(self.tenant_id),
                        "email": f"worker-test-{self.user_id.hex[:8]}@example.test",
                    },
                )
        except Exception:
            # A failed __enter__ skips __exit__; never strand a half-made tenant.
            self._purge()
            raise
        return self

    def create_pending_document(self, content: bytes = PO_TEXT, filename: str = "po.txt") -> UUID:
        document_id = uuid4()
        path = storage.save_file(self.tenant_id, filename, content)
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO documents (id, tenant_id, original_filename, storage_path, source, "
                    "status, content_sha256, created_at) "
                    "VALUES (:id, :tid, :name, :path, 'upload', 'pending', :sha, now())"
                ),
                {
                    "id": str(document_id),
                    "tid": str(self.tenant_id),
                    "name": filename,
                    "path": path,
                    "sha": uuid4().hex,
                },
            )
        return document_id

    def __exit__(self, *exc: object) -> None:
        self._purge()

    def _purge(self) -> None:
        """Delete every row carrying this tenant_id, in whatever order the
        foreign keys allow, then the tenant, then its files."""
        tid = str(self.tenant_id)
        with platform_session() as session:
            tables = [
                row[0]
                for row in session.execute(
                    text(
                        "SELECT c.table_name FROM information_schema.columns c "
                        "JOIN information_schema.tables t "
                        "  ON t.table_name = c.table_name AND t.table_schema = c.table_schema "
                        "WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id' "
                        "  AND t.table_type = 'BASE TABLE' AND c.table_name <> 'tenants'"
                    )
                )
            ]
        # Self- and cross-references between documents go first.
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE documents SET duplicate_of_document_id = NULL, "
                    "change_order_of_document_id = NULL WHERE tenant_id = :tid"
                ),
                {"tid": tid},
            )
        remaining = list(tables)
        for _ in range(len(tables) + 1):
            if not remaining:
                break
            still: list[str] = []
            for table in remaining:
                try:
                    with platform_session() as session:
                        session.execute(text(f"DELETE FROM {table} WHERE tenant_id = :tid"), {"tid": tid})
                except Exception:
                    still.append(table)
            if still == remaining:
                break
            remaining = still
        with platform_session() as session:
            session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tid})
        storage.delete_tenant_storage(self.tenant_id)


def run_extraction(
    monkeypatch: Any, tenant: WorkerTestTenant, document_id: UUID, payload: dict
) -> FakeAnthropic:
    """Run the real task with the model replaced by `payload`."""
    import app.tasks.parse_and_extract as task_module

    fake = FakeAnthropic(payload)
    monkeypatch.setattr(task_module.anthropic, "Anthropic", fake)
    task_module.parse_and_extract(str(tenant.tenant_id), str(document_id))
    return fake


def numeric_text(document_id: UUID) -> dict[str, Any]:
    """The stored document numbers exactly as Postgres renders them -- text,
    so nothing between the database and the assertion can reformat them."""
    with platform_session() as session:
        header = session.execute(
            text("SELECT order_total::text AS order_total FROM document_headers WHERE document_id = :id"),
            {"id": str(document_id)},
        ).mappings().first()
        lines = session.execute(
            text(
                "SELECT id, line_number, quantity::text AS quantity, unit_price::text AS unit_price, "
                "line_total::text AS line_total FROM document_lines WHERE document_id = :id "
                "ORDER BY line_number"
            ),
            {"id": str(document_id)},
        ).mappings().all()
    return {"header": dict(header) if header else {}, "lines": [dict(line) for line in lines]}
