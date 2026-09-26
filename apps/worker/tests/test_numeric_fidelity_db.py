"""
C1: no value is ever rounded between the document and the export (D-149).

Review finding C1: `order_total numeric(12,2)`, `quantity`/`unit_price`
`numeric(14,4)` and `line_total numeric(14,2)` made Postgres round what the
model read and what a reviewer typed, silently, on insert -- and the export
round-trip test passed anyway, because it compared each file with a snapshot
that had already been rounded. These tests compare with the ORIGINAL strings:
what the model returned, or what the human typed.

They run the real worker task, the real review functions and the real export
builders against the real database, with only the Anthropic client faked.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from uuid import UUID, uuid4

from docflow_core import exports
from docflow_core.db import platform_session, tenant_session
from docflow_core.review import (
    EditRequest,
    WarningAcknowledgement,
    apply_edits,
    approve_document,
    current_snapshot,
)
from docflow_core.validation import open_warnings, validate_document
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import text

from tests.conftest import requires_documents_schema
from tests.db_helpers import WorkerTestTenant, model_payload, numeric_text, run_extraction

FORMATS = ("csv", "xlsx", "json", "iif")
NUMERIC_LINE_FIELDS = ("quantity", "unit_price", "line_total")


def _plain_sum(values: list[str]) -> str:
    with localcontext() as ctx:
        ctx.prec = 200
        return format(sum((Decimal(v) for v in values), Decimal(0)), "f")


def _line_ids(document_id: UUID) -> list[UUID]:
    with platform_session() as session:
        return [
            row[0]
            for row in session.execute(
                text("SELECT id FROM document_lines WHERE document_id = :id ORDER BY line_number"),
                {"id": str(document_id)},
            )
        ]


def _approve(tenant: WorkerTestTenant, document_id: UUID) -> dict:
    """Re-check, acknowledge every open warning, approve; return the snapshot."""
    with tenant_session(tenant.tenant_id) as session:
        validate_document(session, tenant.tenant_id, document_id)
        acknowledgements = [
            WarningAcknowledgement(warning_id=w["id"], code=w["code"], text=w["code"])
            for w in open_warnings(session, document_id)
            if w["status"] == "open"
        ]
        approve_document(
            session,
            tenant.tenant_id,
            document_id,
            user_id=tenant.user_id,
            acknowledgements=acknowledgements,
        )
        row = current_snapshot(session, document_id)
    assert row is not None
    return {"snapshot": row["snapshot"], "hash": row["snapshot_sha256"]}


def _exported_numbers(snapshot: dict, snapshot_hash: str, fmt: str) -> dict:
    built = exports.build_export(snapshot, snapshot_hash, fmt)
    parsed = exports.PARSERS[fmt](built.content)
    return {
        "order_total": parsed["header"]["order_total"],
        "lines": [{name: line[name] for name in NUMERIC_LINE_FIELDS} for line in parsed["lines"]],
    }


@requires_documents_schema
def test_C1_worker_stores_extracted_numbers_exactly(monkeypatch):
    printed = {"quantity": "3.5", "unit_price": "0.00345", "line_total": "0.012075"}
    with WorkerTestTenant("Acme Test C1 Worker") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(
            monkeypatch,
            tenant,
            document_id,
            model_payload(header={"order_total": "12.345"}, lines=[printed]),
        )
        stored = numeric_text(document_id)
    assert stored["header"]["order_total"] == "12.345"
    assert {k: stored["lines"][0][k] for k in NUMERIC_LINE_FIELDS} == printed


@requires_documents_schema
def test_C1_human_edit_is_stored_exactly(monkeypatch):
    with WorkerTestTenant("Acme Test C1 Edit") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(
            monkeypatch,
            tenant,
            document_id,
            model_payload(
                header={"order_total": "570.00"},
                lines=[{"quantity": "12", "unit_price": "47.50", "line_total": "570.00"}],
            ),
        )
        (line_id,) = _line_ids(document_id)
        with tenant_session(tenant.tenant_id) as session:
            apply_edits(
                session,
                tenant.tenant_id,
                document_id,
                user_id=tenant.user_id,
                request=EditRequest(
                    header={"order_total": "0.0414"},
                    lines={line_id: {"unit_price": "0.00345", "line_total": "0.0414"}},
                ),
            )
            after_values = [
                change["after"]
                for row in session.execute(
                    text(
                        "SELECT changes FROM review_actions WHERE document_id = :id "
                        "AND action = 'edited'"
                    ),
                    {"id": str(document_id)},
                )
                for change in row[0]
            ]
        stored = numeric_text(document_id)
    assert stored["header"]["order_total"] == "0.0414"
    assert stored["lines"][0]["unit_price"] == "0.00345"
    assert stored["lines"][0]["line_total"] == "0.0414"
    # The audit trail's "after" and the stored value are the same digits.
    assert sorted(after_values) == sorted(["0.0414", "0.00345", "0.0414"])


@requires_documents_schema
def test_C1_every_export_matches_raw_json_for_unedited_fields(monkeypatch):
    lines = [
        {"quantity": "2", "unit_price": "0.00345", "line_total": "0.0069"},
        {"quantity": "1000", "unit_price": "1.234567", "line_total": "1234.567"},
    ]
    order_total = _plain_sum([line["line_total"] for line in lines])
    with WorkerTestTenant("Acme Test C1 Export") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(
            monkeypatch, tenant, document_id, model_payload(header={"order_total": order_total}, lines=lines)
        )
        with platform_session() as session:
            raw = session.execute(
                text("SELECT raw_json FROM documents WHERE id = :id"), {"id": str(document_id)}
            ).scalar_one()
        approved = _approve(tenant, document_id)

    expected = {
        "order_total": raw["header"]["order_total"],
        "lines": [{name: item[name] for name in NUMERIC_LINE_FIELDS} for item in raw["line_items"]],
    }
    for fmt in FORMATS:
        assert _exported_numbers(approved["snapshot"], approved["hash"], fmt) == expected, fmt


# ── Property: random decimals, end to end ───────────────────────────────────


@st.composite
def decimal_text(draw) -> str:
    """A canonical decimal string: 0-10 places, tiny to very large, trailing
    zeros kept (they are part of what was printed)."""
    places = draw(st.integers(min_value=0, max_value=10))
    kind = draw(st.sampled_from(["tiny", "ordinary", "large"]))
    if kind == "tiny":
        places = max(places, 1)
        integer = "0"
    elif kind == "ordinary":
        integer = str(draw(st.integers(min_value=0, max_value=99_999)))
    else:
        integer = str(draw(st.integers(min_value=10**12, max_value=10**24)))
    if places == 0:
        return integer
    fraction = "".join(str(draw(st.integers(0, 9))) for _ in range(places))
    return f"{integer}.{fraction}"


@st.composite
def order_case(draw) -> dict:
    count = draw(st.integers(min_value=1, max_value=3))
    lines = [{name: draw(decimal_text()) for name in NUMERIC_LINE_FIELDS} for _ in range(count)]
    # Human edits: some fields of some lines get a new typed value.
    edits = {
        index: {name: draw(decimal_text()) for name in draw(st.sets(st.sampled_from(NUMERIC_LINE_FIELDS)))}
        for index in draw(st.sets(st.integers(min_value=0, max_value=count - 1)))
    }
    return {"lines": lines, "edits": {i: e for i, e in edits.items() if e}}


@requires_documents_schema
@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(case=order_case())
def test_C1_property_random_decimals_survive_model_db_edit_approve_and_every_export(monkeypatch, case):
    lines = case["lines"]
    # IIF refuses an order whose lines don't add up to its total (EXP-006), so
    # the order total is the exact sum -- of the model's values and, after
    # edits, of the edited ones.
    extracted_total = _plain_sum([line["line_total"] for line in lines])

    expected_lines = [dict(line) for line in lines]
    for index, fields in case["edits"].items():
        expected_lines[index].update(fields)
    expected_total = _plain_sum([line["line_total"] for line in expected_lines])

    with WorkerTestTenant(f"Acme Test C1 Property {uuid4().hex[:6]}") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(
            monkeypatch,
            tenant,
            document_id,
            model_payload(
                header={"order_total": extracted_total, "po_number": uuid4().hex[:10]}, lines=lines
            ),
        )
        stored = numeric_text(document_id)
        assert stored["header"]["order_total"] == extracted_total
        assert [{k: line[k] for k in NUMERIC_LINE_FIELDS} for line in stored["lines"]] == lines

        line_ids = _line_ids(document_id)
        request = EditRequest(
            header={"order_total": expected_total} if expected_total != extracted_total else {},
            lines={line_ids[index]: dict(fields) for index, fields in case["edits"].items()},
        )
        if request.header or request.lines:
            with tenant_session(tenant.tenant_id) as session:
                apply_edits(
                    session, tenant.tenant_id, document_id, user_id=tenant.user_id, request=request
                )
        approved = _approve(tenant, document_id)

    snapshot_lines = approved["snapshot"]["lines"]
    assert approved["snapshot"]["header"]["order_total"] == expected_total
    assert [{k: line[k] for k in NUMERIC_LINE_FIELDS} for line in snapshot_lines] == expected_lines
    for fmt in FORMATS:
        exported = _exported_numbers(approved["snapshot"], approved["hash"], fmt)
        assert exported == {"order_total": expected_total, "lines": expected_lines}, fmt


@requires_documents_schema
def test_C1_numeric_columns_are_unconstrained_so_postgres_cannot_round():
    """The schema half of C1: a typmod on any of these columns is a rounding
    cliff, whatever scale it names (D-149)."""
    with platform_session() as session:
        rows = session.execute(
            text(
                "SELECT table_name, column_name, numeric_precision, numeric_scale "
                "FROM information_schema.columns WHERE table_schema = 'public' AND ("
                " (table_name = 'document_headers' AND column_name = 'order_total') OR"
                " (table_name = 'document_lines' AND column_name IN ('quantity','unit_price','line_total')))"
            )
        ).all()
    assert len(rows) == 4
    for table, column, precision, scale in rows:
        assert (precision, scale) == (None, None), f"{table}.{column} is numeric({precision},{scale})"

