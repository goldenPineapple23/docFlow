"""
The extraction task's own logic: what it does with each answer from the
parse service (Stage 3c), previews, the kept text, model runs, and the
confidence rollup. The file readers themselves moved to the parse service
with their tests (apps/parse/tests). Every file here is one of the parse
service's committed fixtures (apps/parse/tests/fixtures), read by the
running service: the dev service on this machine, the real image in CI
(conftest.py's `parse_service` fixture).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from docflow_core import parse_client
from docflow_core.field_schema import DEFAULT_SCHEMA, _resolve
from docflow_core.file_types import FileTypeName

from app.tasks.parse_and_extract import _overall_confidence
from tests.conftest import fixture_bytes

DOCX = fixture_bytes("positive/po.docx")
TIFF = fixture_bytes("positive/po.tif")

def _fixed_derived_key(tenant_id, document_id, kind, data, content_type=None):
    """Stands in for storage.save_derived: returns the fixed key it would write."""
    return f"tenants/{tenant_id}/derived/{document_id}/{kind}"


class _FakeResult:
    rowcount = 1  # every UPDATE finds its row (the storage-outage release, Stage 3b)

    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row

    def scalar_one_or_none(self):
        return None

    def all(self):
        return []


class _FakeSession:
    """Just enough SQLAlchemy surface to drive the task without a database."""

    def __init__(self, row):
        self._row = row
        self.statements: list[tuple[str, dict]] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, params or {}))
        if "tenant_field_schemas" in sql:
            # No saved field schema: the tenant is on DocFlow's defaults,
            # which is what every tenant had before D-120.
            return _FakeResult(None)
        if sql.strip().upper().startswith("SELECT"):
            return _FakeResult(self._row)
        if "RETURNING" in sql.upper():
            # The claim and every status change are compare-and-set UPDATEs
            # (D-158); here the row is always where the task expects it.
            return _FakeResult({"id": "claimed"})
        return _FakeResult(None)

    def begin_nested(self):
        """The savepoint around the digest note (Stage 3a)."""
        return _FakeSavepoint()


class _FakeSavepoint:
    def commit(self):
        pass

    def rollback(self):
        pass


def _to(session, status):
    """The status changes the task asked for (document_status.transition)."""
    return [params for _sql, params in session.statements if params.get("to") == status]


def test_conversion_failure_marks_the_document_failed_and_keeps_the_worker_alive(monkeypatch):
    """
    CLAUDE.md Section 7.11: "a conversion failure is a clean `failed` with a
    catalog-coded message, never a crash", and "a worker that dies takes one
    document to `failed`, never the app". A truncated TIFF is the malformed
    Tier 2 file; the task must return normally with a DOC-coded failure
    recorded.
    """
    import contextlib
    from uuid import uuid4

    import app.tasks.parse_and_extract as mod

    session = _FakeSession({"storage_path": "tenants/x/uploads/y.tif", "original_filename": "fax.tif"})

    @contextlib.contextmanager
    def fake_tenant_session(tenant_id):
        yield session

    monkeypatch.setattr(mod, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(mod, "read_file", lambda tenant_id, path: b"II\x2a\x00" + b"\xff" * 512)

    mod.parse_and_extract(str(uuid4()), str(uuid4()))

    failures = _to(session, "failed")
    assert failures, "the document was not marked failed"
    assert failures[-1]["v_raw_json"]["error_code"].startswith("DOC-")
    assert failures[-1]["v_failure_code"].startswith("DOC-")


def test_a_failure_that_promises_an_alert_raises_it_without_risking_the_failed_status(monkeypatch):
    """
    D-145: DOC-017 tells the customer "DocFlow has already been alerted", so
    the worker must raise that alert -- after the failed status is written,
    and never at its expense: an alert that can't be written is logged.
    """
    import contextlib
    from uuid import uuid4

    import app.tasks.parse_and_extract as mod

    session = _FakeSession({"storage_path": "tenants/x/uploads/y.tif", "original_filename": "fax.tif"})

    @contextlib.contextmanager
    def fake_tenant_session(tenant_id):
        yield session

    calls: list[dict] = []

    def broken_alert(session, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("the alert table is unreachable")

    monkeypatch.setattr(mod, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(mod, "read_file", lambda tenant_id, path: b"II\x2a\x00" + b"\xff" * 512)
    monkeypatch.setattr(mod.founder_alerts, "raise_for_failure", broken_alert)

    tenant_id, document_id = uuid4(), uuid4()
    mod.parse_and_extract(str(tenant_id), str(document_id))

    assert calls, "no alert was attempted"
    assert calls[0]["error_code"].startswith("DOC-")
    assert calls[0]["document_id"] == document_id
    assert _to(session, "failed")


def test_provenance_records_extracted_for_every_present_field():
    from app.tasks.parse_and_extract import _PROVENANCE_HEADER_FIELDS, _extracted_provenance

    header = {
        "po_number": "BCH-9999",
        "buyer_name": "Acme's Test Coffee House",
        "order_date": None,
        "currency": "USD",
    }
    provenance = _extracted_provenance(header, _PROVENANCE_HEADER_FIELDS)

    assert provenance == {"po_number": "extracted", "buyer_name": "extracted", "currency": "extracted"}
    # A field the model returned as null has no value, so it has no provenance.
    assert "order_date" not in provenance


def test_provenance_of_a_document_with_nothing_extracted_is_empty_not_wrong():
    from app.tasks.parse_and_extract import _PROVENANCE_LINE_FIELDS, _extracted_provenance

    assert _extracted_provenance({"sku": None, "description": None}, _PROVENANCE_LINE_FIELDS) == {}


def test_overall_confidence_is_the_minimum_of_required_fields_not_an_average():
    """CLAUDE.md Section 7.1, as the tenant's field schema defines required
    (D-120): an optional field read badly -- or absent -- does not decide how
    much of the order a reviewer should distrust."""
    confidence = {"po_number": 0.98, "buyer_name": 0.9, "order_total": 0.95, "currency": 0.93}
    assert _overall_confidence(confidence, DEFAULT_SCHEMA) == Decimal("0.9")

    with_a_bad_optional = {**confidence, "order_date": 0.4, "payment_terms": 0.0}
    assert _overall_confidence(with_a_bad_optional, DEFAULT_SCHEMA) == Decimal("0.9")

    # Required for this tenant: now it counts.
    stricter = _resolve(2, {"header": {"order_date": "required"}})
    assert _overall_confidence(with_a_bad_optional, stricter) == Decimal("0.4")


def test_overall_confidence_handles_empty():
    assert _overall_confidence({}, DEFAULT_SCHEMA) == Decimal("0")


# ── matching is wired in, and cannot cost the document its extraction ───────


def _no_examples(monkeypatch, mod):
    """The example planner reads the database; these tests have none. A
    tenant with the feature off gets exactly this plan."""
    from docflow_core.example_prompting import ExamplePlan

    monkeypatch.setattr(mod.example_prompting, "plan", lambda *args, **kwargs: ExamplePlan())


def _drive_successful_task(monkeypatch, *, buyer_id, matcher):
    """
    Runs `parse_and_extract` end to end over a plain-text PO with the model
    call, storage and both post-extraction steps replaced. Returns the fake
    session so a test can inspect what was written.
    """
    import contextlib
    from uuid import uuid4

    from docflow_core.buyers import BuyerIdentification
    from docflow_core.extraction import ExtractionResult

    import app.tasks.parse_and_extract as mod

    session = _FakeSession({"storage_path": "tenants/x/uploads/po.txt", "original_filename": "po.txt"})

    @contextlib.contextmanager
    def fake_tenant_session(tenant_id):
        yield session

    header = {key: None for key in mod._PROVENANCE_HEADER_FIELDS}
    header["po_number"] = "BCH-2291"
    header["buyer_name"] = "Acme's Test Coffee House"
    result = ExtractionResult(
        ok=True,
        model_id="test-model",
        prompt_hash="testhash",
        schema_version="test",
        raw_response={},
        header=header,
        header_confidence={"po_number": 0.97},
        lines=[
            {
                "line_number": 1,
                "sku": "CF-1001",
                "description": "Colombian Whole Bean 5lb",
                "quantity": Decimal("12"),
                "unit": "CS",
                "unit_price": Decimal("47.50"),
                "line_total": Decimal("570.00"),
                "confidence": Decimal("0.95"),
            }
        ],
        injection_suspected=False,
        currency_inferred=False,
    )

    monkeypatch.setattr(mod, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(mod, "read_file", lambda tenant_id, path: b"PURCHASE ORDER\nPO Number: BCH-2291\n")
    monkeypatch.setattr(mod.anthropic, "Anthropic", lambda api_key=None, **kwargs: object())
    monkeypatch.setattr(mod, "extract_document", lambda client, content, **kwargs: result)
    _no_examples(monkeypatch, mod)
    monkeypatch.setattr(
        mod,
        "save_derived",
        _fixed_derived_key,
    )
    monkeypatch.setattr(
        mod,
        "identify_and_link_buyer",
        lambda *args, **kwargs: BuyerIdentification(buyer_id=buyer_id, created=True),
    )
    monkeypatch.setattr(mod, "match_document_lines", matcher)
    monkeypatch.setattr(
        mod,
        "detect_document_relationships",
        lambda *args, **kwargs: type(
            "Relationships", (), {"is_possible_duplicate": False, "is_possible_change_order": False}
        )(),
    )
    # Validation and the alert writer need a real database (their own tests
    # have one); here they only have to be called at the right moment.
    from docflow_core.validation import ValidationSummary

    monkeypatch.setattr(mod, "validate_document", lambda *args, **kwargs: ValidationSummary())
    session.alerts = []  # type: ignore[attr-defined]
    monkeypatch.setattr(
        mod.founder_alerts, "raise_alert", lambda _session, **kwargs: session.alerts.append(kwargs)
    )
    session.digest_notes = []  # type: ignore[attr-defined]
    monkeypatch.setattr(
        mod.review_digest, "note_needs_review", lambda _session, tid, did: session.digest_notes.append(did)
    )

    mod.parse_and_extract(str(uuid4()), str(uuid4()))
    # Every drive is a success: a stub missing something the task reads must
    # fail here, not turn quietly into a failed document (Stage 3a CI lesson).
    assert _to(session, "needs_review") and not _to(session, "failed"), _to(session, "failed")
    assert len(session.digest_notes) == 1
    return session


def test_matching_runs_after_buyer_identification_with_the_resolved_buyer(monkeypatch):
    """
    Matching is buyer-scoped (CLAUDE.md Section 7.6), so it has to see the
    buyer the previous step resolved -- not re-derive one, and not run first.
    """
    from uuid import uuid4

    from docflow_core.matching import MatchingSummary

    buyer_id = uuid4()
    calls: list[dict] = []

    def matcher(session, tenant_id, document_id, *, buyer_id=None):
        calls.append({"tenant_id": tenant_id, "document_id": document_id, "buyer_id": buyer_id})
        return MatchingSummary(lines_considered=1, lines_matched=1)

    _drive_successful_task(monkeypatch, buyer_id=buyer_id, matcher=matcher)

    assert len(calls) == 1
    assert calls[0]["buyer_id"] == buyer_id


def test_a_matching_failure_leaves_the_extraction_intact(monkeypatch):
    """
    DECISIONS.md D-058, extended to matching: extraction is one model call the
    tenant has already paid for. A failure in the deterministic step that runs
    afterwards must never roll it back or mark the document failed -- the
    document stays `needs_review` with unmatched lines, which is the same
    state a document with no catalog hits legitimately has.
    """
    from uuid import uuid4

    def matcher(session, tenant_id, document_id, *, buyer_id=None):
        raise RuntimeError("catalog unavailable")

    session = _drive_successful_task(monkeypatch, buyer_id=uuid4(), matcher=matcher)

    # Still reviewable, but the step that didn't finish is said out loud
    # (H1, D-158): recorded on the document and alerted, never only logged.
    assert _to(session, "needs_review"), "the document never reached review"
    assert not _to(session, "failed")
    issues = [params["issues"] for _sql, params in session.statements if "pipeline_issues" in _sql]
    assert issues == [["matching"]]
    assert [a["alert_type"] for a in session.alerts] == ["pipeline_step_failed"]


# ── previews for real intake (DECISIONS.md D-092) ────────────────────────────


def _preview_for(filename: str, content: bytes):
    """The preview the task builds from the parse service's real answer."""
    from app.tasks.parse_and_extract import build_preview

    answer = parse_client.parse_document(content, filename)
    assert answer.outcome == "ok", (answer.outcome, answer.code, answer.cause)
    return build_preview(answer)


def test_a_word_preview_includes_the_line_items_table():
    """
    A Word PO's line items live in a table. A preview without them would put
    the extracted lines beside a document that seems not to contain them.
    """
    preview = _preview_for("po.docx", fixture_bytes("positive/po.docx"))

    assert preview is not None and preview.kind == "extracted_text"
    assert b"CF-1001" in preview.content
    assert b"CUP-12" in preview.content


@pytest.mark.parametrize(
    "filename", ["po.xlsx", "po.rtf", "po.eml", "po.msg", "po.xls", "po.odt", "po.ods", "po.doc"]
)
def test_every_text_like_format_the_worker_reads_gets_a_text_preview(filename):
    preview = _preview_for(filename, fixture_bytes(f"positive/{filename}"))

    assert preview is not None, f"{filename} would show the 'can't display' fallback"
    assert preview.kind == "extracted_text"
    assert preview.media_type.startswith("text/plain")


@pytest.mark.parametrize("filename", ["po.tif", "po.heic"])
def test_image_like_formats_get_a_converted_image(filename):
    preview = _preview_for(filename, fixture_bytes(f"positive/{filename}"))

    assert preview is not None and preview.kind == "converted_image"
    assert preview.media_type == "image/png"


def test_formats_a_browser_shows_get_no_preview():
    assert _preview_for("po.pdf", fixture_bytes("positive/po.pdf")) is None
    assert _preview_for("po.txt", fixture_bytes("positive/po.txt")) is None


def test_a_page_read_visually_is_named_in_the_preview_not_dropped():
    from app.tasks.parse_and_extract import _VISUAL_PART_PLACEHOLDER, build_preview

    answer = parse_client.ParseAnswer(
        "ok",
        file_type=FileTypeName.EML.value,
        parts=[{"type": "text", "text": "Order body"}, {"type": "image", "source": {}}],
    )

    preview = build_preview(answer)

    assert preview is not None
    assert _VISUAL_PART_PLACEHOLDER.encode() in preview.content


def _drive_task_over(monkeypatch, filename: str, content: bytes, *, save_derived):
    import contextlib
    from uuid import uuid4

    from docflow_core.extraction import ExtractionResult

    import app.tasks.parse_and_extract as mod

    session = _FakeSession(
        {"storage_path": f"tenants/x/uploads/y{filename[-5:]}", "original_filename": filename}
    )

    @contextlib.contextmanager
    def fake_tenant_session(tenant_id):
        yield session

    failed = ExtractionResult(
        ok=False, model_id="m", prompt_hash="h", schema_version="s", raw_response={}
    )
    monkeypatch.setattr(mod, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(mod, "read_file", lambda tenant_id, path: content)
    monkeypatch.setattr(mod, "save_derived", save_derived)
    monkeypatch.setattr(mod.anthropic, "Anthropic", lambda api_key=None, **kwargs: object())
    monkeypatch.setattr(mod, "extract_document", lambda client, blocks, **kwargs: failed)
    _no_examples(monkeypatch, mod)

    tenant_id = uuid4()
    mod.parse_and_extract(str(tenant_id), str(uuid4()))
    return session, tenant_id


def test_the_task_stores_a_preview_under_the_tenant_even_when_extraction_fails(monkeypatch):
    """
    The gap this closes: previews were built only by the demo seed script, so
    a real Word upload showed "can't display". The preview is written before
    the model call, so a reviewer can see a document whose extraction failed.
    """
    saved: list[tuple] = []

    def fake_save(tenant_id, document_id, kind, data, content_type=None):
        saved.append((tenant_id, kind, data, content_type))
        return f"tenants/{tenant_id}/derived/{document_id}/{kind}"

    session, tenant_id = _drive_task_over(monkeypatch, "po.docx", DOCX, save_derived=fake_save)

    previews_saved = [entry for entry in saved if entry[1].startswith("preview")]
    assert len(previews_saved) == 1 and previews_saved[0][0] == tenant_id
    updates = [params for sql, params in session.statements if "preview_storage_path" in sql]
    assert updates == [
        {
            "id": updates[0]["id"],
            "path": f"tenants/{tenant_id}/derived/{updates[0]['id']}/preview",
            "media_type": "text/plain; charset=utf-8",
            "kind": "extracted_text",
        }
    ]


def test_a_preview_failure_never_touches_the_document(monkeypatch):
    def broken_save(tenant_id, document_id, kind, data, content_type=None):
        from docflow_core.storage import StorageUnavailableError

        raise StorageUnavailableError("fake outage")

    session, _ = _drive_task_over(monkeypatch, "po.docx", DOCX, save_derived=broken_save)

    assert not [sql for sql, _ in session.statements if "preview_storage_path" in sql]
    # The run still reached the model call and recorded its outcome.
    assert [params for sql, params in session.statements if params.get("model_id") == "m"]


# ── Section 7.13: the text an example needs, and the runs it costs (D-141/142) ──


def test_the_text_sent_to_the_model_is_kept_for_a_text_document(monkeypatch):
    saved: list[tuple] = []

    def fake_save(tenant_id, document_id, kind, data, content_type=None):
        saved.append((tenant_id, kind, data, document_id))
        return f"tenants/{tenant_id}/derived/{document_id}/{kind}"

    session, tenant_id = _drive_task_over(monkeypatch, "po.docx", DOCX, save_derived=fake_save)

    kept = [entry for entry in saved if entry[1] == "extracted_text"]
    assert len(kept) == 1 and kept[0][0] == tenant_id
    assert b"CF-1001" in kept[0][2]
    updates = [params for sql, params in session.statements if "extracted_text_path" in sql]
    assert updates and updates[0]["path"] == f"tenants/{tenant_id}/derived/{kept[0][3]}/extracted_text"


def test_a_document_read_visually_keeps_no_text_and_can_never_be_an_example(monkeypatch):
    """An example is never a file or an image (Section 7.13, Section 10)."""
    saved: list[tuple] = []

    def fake_save(tenant_id, document_id, kind, data, content_type=None):
        saved.append((tenant_id, kind, data, document_id))
        return f"tenants/{tenant_id}/derived/{document_id}/{kind}"

    session, _ = _drive_task_over(monkeypatch, "fax.tif", TIFF, save_derived=fake_save)

    assert not [entry for entry in saved if entry[1] == "extracted_text"]
    assert not [sql for sql, _ in session.statements if "extracted_text_path" in sql]


def test_a_failed_extraction_is_still_recorded_as_a_model_run(monkeypatch):
    """The daily cost breaker reads extraction_runs (Section 7.9); a call that
    failed still happened and must be there."""
    session, _ = _drive_task_over(
        monkeypatch, "po.docx", fixture_bytes("positive/po.docx"), save_derived=_fixed_derived_key
    )

    runs = [params for sql, params in session.statements if "INSERT INTO extraction_runs" in sql]
    assert len(runs) == 1
    assert runs[0]["run_kind"] == "extraction" and runs[0]["succeeded"] is False


def test_a_successful_extraction_records_its_run_and_points_the_document_at_it(monkeypatch):
    session = _drive_successful_task(monkeypatch, buyer_id=None, matcher=lambda *a, **k: _NoMatches())

    runs = [params for sql, params in session.statements if "INSERT INTO extraction_runs" in sql]
    assert len(runs) == 1 and runs[0]["succeeded"] is True and runs[0]["examples_used"] == "[]"
    updates = [
        params
        for sql, params in session.statements
        if "current_extraction_run_id" in sql and sql.lstrip().upper().startswith("UPDATE")
    ]
    assert updates and updates[0]["run_id"] == runs[0]["id"]


def test_the_routing_read_is_its_own_run_and_its_cost_is_part_of_the_document(monkeypatch):
    from docflow_core.example_prompting import ExamplePlan
    from docflow_core.extraction import RoutingResult

    import app.tasks.parse_and_extract as mod

    routing = RoutingResult(
        ok=True, model_id="routing-model", prompt_hash="r", schema_version="routing",
        raw_response={}, input_tokens=900, output_tokens=40, est_cost_usd=Decimal("0.0011"),
    )
    captured = {}

    def fake_extract(client, content, **kwargs):
        captured.update(kwargs)
        from docflow_core.extraction import ExtractionResult

        return ExtractionResult(
            ok=False, model_id="m", prompt_hash="h", schema_version="s", raw_response={},
            est_cost_usd=Decimal("0.0100"),
        )

    session, _ = _drive_task_over(
        monkeypatch, "po.docx", fixture_bytes("positive/po.docx"), save_derived=_fixed_derived_key
    )
    # Re-drive with a routing plan in place.
    monkeypatch.setattr(mod.example_prompting, "plan", lambda *a, **k: ExamplePlan(routing=routing))
    monkeypatch.setattr(mod, "extract_document", fake_extract)
    session.statements.clear()
    from uuid import uuid4

    mod.parse_and_extract(str(uuid4()), str(uuid4()))

    runs = [params for sql, params in session.statements if "INSERT INTO extraction_runs" in sql]
    # The routing read is recorded by example_prompting.plan the moment it
    # returns (D-163; test_example_prompting.py) -- stubbed out here -- so the
    # worker writes only the extraction run. The document's cost still sums both.
    assert [r["run_kind"] for r in runs] == ["extraction"]
    assert captured["examples"] == []
    costs = [params["v_est_cost_usd"] for params in _to(session, "failed")]
    assert costs == ["0.0111"]


def test_a_planning_failure_means_no_examples_never_a_lost_document(monkeypatch):
    from uuid import uuid4

    import app.tasks.parse_and_extract as mod

    def broken_plan(*args, **kwargs):
        raise RuntimeError("database away")

    monkeypatch.setattr(mod.example_prompting, "plan", broken_plan)
    plan = mod._plan_examples(object(), uuid4(), uuid4(), None, [])
    assert plan.examples == [] and plan.outcome == "planning_failed"


class _NoMatches:
    lines_considered = 0
    lines_matched = 0
    rules_applied: dict = {}


# ── Stage 3b: reading the original from Storage (Q3, Q5) ──────────────────


def _drive_read(monkeypatch, *, read, sha256=None):
    import contextlib
    from uuid import uuid4

    import app.tasks.parse_and_extract as mod

    session = _FakeSession(
        {
            "storage_path": "tenants/x/uploads/po.txt",
            "original_filename": "po.txt",
            "content_sha256": sha256,
        }
    )

    @contextlib.contextmanager
    def fake_tenant_session(tenant_id):
        yield session

    alerts: list[dict] = []
    failure_alerts: list[dict] = []
    monkeypatch.setattr(mod, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(mod, "read_file", read)
    monkeypatch.setattr(
        mod.founder_alerts, "raise_storage_unavailable", lambda session, **kw: alerts.append(kw)
    )
    monkeypatch.setattr(
        mod.founder_alerts, "raise_for_failure", lambda session, **kw: failure_alerts.append(kw)
    )

    def no_model(*args, **kwargs):
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(mod, "extract_document", no_model)
    tenant_id = uuid4()
    mod.parse_and_extract(str(tenant_id), str(uuid4()))
    return session, alerts, failure_alerts


def test_a_storage_outage_gives_the_attempt_back_and_never_fails_the_document(monkeypatch):
    """An outage longer than MAX_PROCESSING_ATTEMPTS x the stuck timeout used
    to end in DOC-022. Now each failed read puts its attempt back, so however
    many times the sweep retries during an outage, the document never fails."""
    from docflow_core.storage import StorageUnavailableError

    def down(tenant_id, path):
        raise StorageUnavailableError("fake outage")

    for _ in range(3):
        session, alerts, failure_alerts = _drive_read(monkeypatch, read=down)
        assert not _to(session, "failed")
        released = [sql for sql, _ in session.statements if "processing_attempts - 1" in sql]
        assert len(released) == 1
        assert alerts and alerts[0]["where"] == "worker"
        assert not failure_alerts


def test_a_missing_original_fails_loudly_once_instead_of_waiting(monkeypatch):
    from docflow_core.storage import StorageObjectMissingError

    def gone(tenant_id, path):
        raise StorageObjectMissingError("not found")

    session, alerts, failure_alerts = _drive_read(monkeypatch, read=gone)
    failures = _to(session, "failed")
    assert failures and failures[-1]["v_failure_code"] == "DOC-026"
    assert not [sql for sql, _ in session.statements if "processing_attempts - 1" in sql]
    assert failure_alerts and failure_alerts[0]["error_code"] == "DOC-026"
    assert not alerts


def test_a_refused_path_fails_cleanly_with_doc_026_and_no_retry(monkeypatch):
    """A path the prefix rules refuse would be refused again on every retry,
    so it ends now in DOC-026 -- not a crash, not a wait (founder, 2026-09-30)."""
    from docflow_core.storage import UnsafeStoragePathError

    import app.tasks.parse_and_extract as mod

    reported: list[dict] = []
    monkeypatch.setattr(
        mod.founder_alerts, "report_refused_storage_path", lambda exc, **kw: reported.append(kw)
    )

    def refused(tenant_id, path):
        raise UnsafeStoragePathError("not under this tenant's prefix")

    session, alerts, failure_alerts = _drive_read(monkeypatch, read=refused)
    failures = _to(session, "failed")
    assert failures and failures[-1]["v_raw_json"] == {"error_code": "DOC-026", "detail": "path_refused"}
    assert not [sql for sql, _ in session.statements if "processing_attempts - 1" in sql]
    assert failure_alerts and failure_alerts[0]["error_code"] == "DOC-026"
    assert reported and reported[0]["where"] == "worker"
    assert not alerts


def test_bytes_that_dont_match_the_received_file_are_never_parsed(monkeypatch):
    import hashlib

    received = b"PURCHASE ORDER\nPO Number: BCH-2291\n"
    session, _, failure_alerts = _drive_read(
        monkeypatch,
        read=lambda tenant_id, path: b"PURCHASE ORDER\nPO Number: SOMETHING-ELSE\n",
        sha256=hashlib.sha256(received).hexdigest(),
    )
    failures = _to(session, "failed")
    assert failures and failures[-1]["v_failure_code"] == "DOC-026"
    assert failures[-1]["v_raw_json"] == {"error_code": "DOC-026", "detail": "hash_mismatch"}
    assert failure_alerts and failure_alerts[0]["error_code"] == "DOC-026"


def test_the_worker_reads_with_its_documents_tenant(monkeypatch):
    """Item 3: the read passes the tenant, so the storage layer can refuse a
    path under any other tenant before contacting Storage."""
    import hashlib

    seen: list = []
    content = b"PURCHASE ORDER\nPO Number: BCH-2291\n"

    def read(tenant_id, path):
        seen.append((tenant_id, path))
        return content

    try:
        _drive_read(monkeypatch, read=read, sha256=hashlib.sha256(content).hexdigest())
    except AssertionError:
        pass  # it got as far as the model, which this test doesn't need
    assert seen and seen[0][1] == "tenants/x/uploads/po.txt"
    from uuid import UUID

    assert isinstance(seen[0][0], UUID)


# ── Stage 3c, item 6: what the task does with each answer (test F1) ──────────


def _drive_with_answer(monkeypatch, outcome):
    """Run the task with the parse service's answer replaced by `outcome`:
    a ParseAnswer to return, or an exception to raise."""
    import contextlib
    from uuid import uuid4

    import app.tasks.parse_and_extract as mod

    session = _FakeSession({"storage_path": "tenants/x/uploads/y.pdf", "original_filename": "po.pdf"})
    alerts: list[tuple[str, dict]] = []

    @contextlib.contextmanager
    def fake_tenant_session(tenant_id):
        yield session

    def fake_parse(content, filename):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(mod, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(mod, "read_file", lambda tenant_id, path: b"%PDF-1.4 fake")
    monkeypatch.setattr(mod.parse_client, "parse_document", fake_parse)
    for name in ("raise_for_failure", "raise_parse_alert", "raise_parse_service_unavailable"):
        monkeypatch.setattr(
            mod.founder_alerts, name, lambda session, _n=name, **kw: alerts.append((_n, kw)) or True
        )
    sent: list[tuple] = []
    monkeypatch.setattr(
        mod.celery_app, "send_task", lambda name, args, queue: sent.append((name, args, queue))
    )
    tenant_id, document_id = uuid4(), uuid4()
    mod.parse_and_extract(str(tenant_id), str(document_id))
    return session, alerts, sent


def test_never_got_in_waits_without_using_a_try(monkeypatch):
    session, alerts, sent = _drive_with_answer(monkeypatch, parse_client.ParseUnavailable("no_connection"))
    assert not _to(session, "failed")
    gave_back = [sql for sql, _ in session.statements if "processing_attempts - 1" in sql]
    assert gave_back, "the try was not given back"
    assert [name for name, _ in alerts] == ["raise_parse_service_unavailable"]
    assert alerts[0][1]["reason"] == "no_connection" and alerts[0][1]["where"] == "worker"
    assert not sent


def test_a_limit_fails_doc_029_with_its_alert(monkeypatch):
    session, alerts, _ = _drive_with_answer(monkeypatch, parse_client.ParseAnswer("stopped", cause="memory"))
    failures = _to(session, "failed")
    assert failures[-1]["v_failure_code"] == "DOC-029"
    # DOC-029's wording promises the customer nothing, so it isn't in
    # FAILURE_ALERTS; the founder's parse alert is raised explicitly, once per
    # tenant per day (not per cause).
    parse_alerts = [kw for name, kw in alerts if name == "raise_parse_alert"]
    assert [(kw["error_code"], kw["cause"], kw["by_cause"]) for kw in parse_alerts] == [
        ("DOC-029", "stopped:memory", False)
    ]


def test_a_rejection_keeps_its_catalog_code(monkeypatch):
    session, alerts, _ = _drive_with_answer(monkeypatch, parse_client.ParseAnswer("rejected", code="DOC-016"))
    assert _to(session, "failed")[-1]["v_failure_code"] == "DOC-016"


@pytest.mark.parametrize(
    "outcome, cause",
    [
        (parse_client.ParseAnswer("crashed", cause="signal_11"), "crashed:signal_11"),
        (parse_client.ParseAnswerInvalid("bad_part_type"), "invalid:bad_part_type"),
    ],
)
def test_a_crash_or_a_refused_answer_fails_doc_005_and_tells_the_founder(monkeypatch, outcome, cause):
    session, alerts, _ = _drive_with_answer(monkeypatch, outcome)
    assert _to(session, "failed")[-1]["v_failure_code"] == "DOC-005"
    assert [(name, kw["error_code"], kw["cause"], kw["by_cause"]) for name, kw in alerts] == [
        ("raise_parse_alert", "DOC-005", cause, True)
    ]


def test_a_lost_try_is_recorded_and_retried_at_once(monkeypatch):
    import app.tasks.parse_and_extract as mod

    released: list = []
    monkeypatch.setattr(mod.document_status, "record_parse_lost", lambda session, did: (1, [], [1]))
    monkeypatch.setattr(
        mod.document_status, "release_claim", lambda session, did: released.append(did) or True
    )
    session, alerts, sent = _drive_with_answer(monkeypatch, parse_client.ParseLost("ReadTimeout"))
    assert not _to(session, "failed")
    assert released and not alerts
    assert [(name, queue) for name, _args, queue in sent] == [("docflow.parse_and_extract", "interactive")]


def test_a_second_lost_try_fails_doc_022_naming_the_lost_parse(monkeypatch):
    import app.tasks.parse_and_extract as mod

    monkeypatch.setattr(mod.document_status, "record_parse_lost", lambda session, did: (2, [], [1, 2]))
    session, alerts, sent = _drive_with_answer(monkeypatch, parse_client.ParseLost("ReadTimeout"))
    assert _to(session, "failed")[-1]["v_failure_code"] == "DOC-022"
    assert not sent
    (name, kw), = alerts
    assert name == "raise_for_failure" and kw["error_code"] == "DOC-022" and kw["cause"] == "timeout"
    assert kw["detail"]["cause_detail"] == "parse_lost"
    assert kw["detail"]["parse_lost_attempts"] == [1, 2]


# ── Stage 3d: what the task does with each provider answer ──────────────────


def _drive_with_provider(monkeypatch, request, *, extraction_error=None, routing_error=None):
    """Run the task over a real fixture with the model's answer replaced: an
    extraction that failed with `extraction_error` (a ProviderError), and a
    routing call that failed with `routing_error`. Returns the fake session
    and the stubbed dispatcher/provider calls (conftest)."""
    from decimal import Decimal  # noqa: F401 -- kept local like the tests above
    from uuid import uuid4

    from docflow_core.example_prompting import ExamplePlan
    from docflow_core.extraction import ExtractionResult, RoutingResult

    import app.tasks.parse_and_extract as mod

    routing = None
    if routing_error is not None:
        routing = RoutingResult(
            ok=False, model_id="r", prompt_hash="r", schema_version="r", raw_response={},
            error_code="DOC-008", provider_error=routing_error,
        )
    failed = ExtractionResult(
        ok=False, model_id="m", prompt_hash="h", schema_version="s", raw_response={},
        error_code="DOC-008", provider_error=extraction_error,
    )
    session, _ = _drive_task_over(
        monkeypatch, "po.docx", fixture_bytes("positive/po.docx"), save_derived=_fixed_derived_key
    )
    session.statements.clear()
    request.node.dispatch_calls.clear()
    monkeypatch.setattr(mod.example_prompting, "plan", lambda *a, **k: ExamplePlan(routing=routing))
    monkeypatch.setattr(mod, "extract_document", lambda client, blocks, **kwargs: failed)
    # The first wait: nothing waited yet (the fake session can't answer the
    # read; test_dispatch_db.py runs the real one).
    monkeypatch.setattr(mod.document_status, "waited_seconds", lambda session, did, cause: None)
    mod.parse_and_extract(str(uuid4()), str(uuid4()))
    return session, request.node.dispatch_calls


def test_a_provider_wait_goes_back_to_pending_and_never_fails(monkeypatch, request):
    from docflow_core.provider_errors import ProviderError

    overloaded = ProviderError("wait", "overloaded", "http_529")
    session, calls = _drive_with_provider(monkeypatch, request, extraction_error=overloaded)
    assert not _to(session, "failed")
    waits = [params for sql, params in session.statements if "SET status = 'pending'" in sql]
    assert len(waits) == 1
    assert (waits[0]["cause"], waits[0]["error_label"], waits[0]["retry_after"]) == (
        "model_provider", "http_529", 60
    )
    # The failure counts towards "down"; the run ends with a dispatch pass.
    assert [c[0] for c in calls] == ["failure", "dispatch"]
    assert calls[0][1] is overloaded


def test_a_request_the_document_made_impossible_still_fails_at_once(monkeypatch, request):
    from docflow_core.provider_errors import ProviderError

    bad = ProviderError("fail", None, "http_400")
    session, calls = _drive_with_provider(monkeypatch, request, extraction_error=bad)
    assert _to(session, "failed")[-1]["v_failure_code"] == "DOC-008"
    assert not [sql for sql, _ in session.statements if "SET status = 'pending'" in sql]
    assert [c[0] for c in calls] == ["dispatch"]


def test_our_own_configuration_waits_too(monkeypatch, request):
    """Founder, 2026-10-01: a wrong key never fails a customer's order."""
    from docflow_core.provider_errors import ProviderError

    key = ProviderError("wait", "our_configuration", "http_401")
    session, calls = _drive_with_provider(monkeypatch, request, extraction_error=key)
    assert not _to(session, "failed")
    assert [c[0] for c in calls] == ["failure", "dispatch"]


def test_a_routing_model_refusal_alerts_and_extraction_goes_ahead(monkeypatch, request):
    """Founder: alert, don't hold -- the document is read without examples."""
    from docflow_core.provider_errors import ProviderError

    retired = ProviderError("wait", "our_configuration", "http_404")
    _session, calls = _drive_with_provider(monkeypatch, request, routing_error=retired)
    assert ("routing_failure", retired) in calls
    assert calls[-1] == ("dispatch",)


def test_every_document_task_ends_with_a_dispatch_pass_even_when_it_raises(monkeypatch, request):
    from uuid import uuid4

    import app.tasks.parse_and_extract as mod

    def boom(tid, did):
        raise RuntimeError("anything")

    monkeypatch.setattr(mod, "_parse_and_extract", boom)
    request.node.dispatch_calls.clear()
    with pytest.raises(RuntimeError):
        mod.parse_and_extract(str(uuid4()), str(uuid4()))
    assert request.node.dispatch_calls == [("dispatch",)]
