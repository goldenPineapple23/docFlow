"""
The extraction task (CLAUDE.md Section 7.11: "parsing never runs in the web
process"). Since Stage 3c this task never opens the file either: it reads
the original from Storage, checks its hash, and sends the bytes to the parse
service (`docflow_core.parse_client`), where every file is opened in a
sandboxed job of its own -- its own namespaces, cgroups, seccomp filter and
unprivileged user, one process per file. What comes back (text parts, page
or image parts, an image preview) is checked by the client, then sent to the
model here. The API (apps/api/app/routers/documents.py) only validates bytes
and enqueues this task.

`tenant_id` is a required argument, not derived from anything inside this
task: the enqueuing caller (the upload endpoint) already resolved it from
the authenticated session per CLAUDE.md Section 7.5, and passes it through
so this task can open a correctly-scoped `tenant_session()` from the start --
there is no query in this module that runs before a tenant context is set.

Every DB write goes through `docflow_core.db.tenant_session()` -- never a
raw connection, never `admin_data_access` (Section 7.15.1's import boundary
doesn't apply here; this module is not `/admin/*` and must never import it).
"""

from __future__ import annotations

import hashlib
import logging
import time
from decimal import Decimal
from uuid import UUID, uuid4

import anthropic
from docflow_core import (
    dispatch,
    document_status,
    example_prompting,
    faults,
    field_schema,
    file_types,
    founder_alerts,
    model_provider,
    model_runs,
    parse_client,
    previews,
    retry_rules,
    review_digest,
)
from docflow_core.buyers import identify_and_link_buyer
from docflow_core.config import get_settings
from docflow_core.constants import (
    DISPATCH_QUEUE,
    DOCUMENT_TASK_TIME_LIMIT_SECONDS,
    PARSE_SERVICE_WAIT_RETRY_MINUTES,
    PROVIDER_MAX_WAIT_HOURS,
    STORAGE_WAIT_RETRY_MINUTES,
)
from docflow_core.db import tenant_session
from docflow_core.duplicates import detect_document_relationships
from docflow_core.example_prompting import ExamplePlan
from docflow_core.extraction import (
    EXTRACTION_DEADLINE_SECONDS,
    ExtractionResult,
    build_text_content,
    extract_document,
    wrap_document_content,
)
from docflow_core.field_schema import FieldSchema
from docflow_core.matching import bump_times_applied, match_document_lines
from docflow_core.numbers import plain_or_none
from docflow_core.storage import (
    StorageObjectMissingError,
    StorageUnavailableError,
    UnsafeStoragePathError,
    read_file,
    save_derived,
)
from docflow_core.validation import validate_document
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.celery_app import celery_app

logger = logging.getLogger(__name__)

def _envelope(parts: list[dict]) -> list[dict]:
    """
    One <document> envelope for the whole document, however many parts the
    parse service returned (CLAUDE.md Section 7.2: document content is
    always delimited, and it is data, never instructions).
    """
    if len(parts) == 1 and parts[0]["type"] == "text":
        return build_text_content(parts[0]["text"])
    return wrap_document_content(parts)


# Stands in, in a text preview, for a page the model read visually (a scanned
# PDF or an image attached to an email). Deliberately generic: an artifact's
# label can carry an attachment filename, which is untrusted (Section 7.11).
_VISUAL_PART_PLACEHOLDER = (
    "[An attached page or image was read visually and cannot be shown as text here. "
    "Download the original to see it.]"
)


def build_preview(answer: parse_client.ParseAnswer) -> previews.Preview | None:
    """
    A viewable rendering for a format no browser displays (DECISIONS.md D-092),
    or None when the browser can show the original as it is.

    Image-like formats (TIFF, HEIC) get the image the parse service made from
    the original. For every other format the preview is the text the service
    returned and this task sent to the model -- one extraction, two uses, so
    the reviewer sees exactly what DocFlow read, tables included.
    """
    name = file_types.FileTypeName(answer.file_type)
    if not previews.needs_preview(name):
        return None
    if previews.is_image_like(name):
        image = answer.image_preview
        if image is None:
            return None
        return previews.Preview(content=image["content"], media_type=image["media_type"], kind=image["kind"])
    chunks = [part["text"] if part["type"] == "text" else _VISUAL_PART_PLACEHOLDER for part in answer.parts]
    return previews.text_preview("\n\n".join(chunks))


def _store_preview(tenant_id: UUID, document_id: UUID, answer: parse_client.ParseAnswer) -> None:
    """
    Best effort, by design: a preview is a convenience, and a document that
    cannot be previewed still reviews fine against its extracted values --
    the viewer says so. A failure here never touches the document's status.
    """
    try:
        preview = build_preview(answer)
        if preview is None:
            return
        # Fixed key per document (Stage 3b item 4): a retry overwrites it, so
        # it never leaves an orphan. The media type is stored on the row and
        # set on the object.
        preview_path = save_derived(
            tenant_id, document_id, "preview", preview.content, content_type=preview.media_type
        )
        with tenant_session(tenant_id) as session:
            session.execute(
                text(
                    """
                    UPDATE documents
                    SET preview_storage_path = :path, preview_media_type = :media_type,
                        preview_kind = :kind
                    WHERE id = :id
                    """
                ),
                {
                    "id": str(document_id),
                    "path": preview_path,
                    "media_type": preview.media_type,
                    "kind": preview.kind,
                },
            )
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.error("preview_failed document_id=%s error_type=%s", document_id, type(exc).__name__)


def _store_extracted_text(tenant_id: UUID, document_id: UUID, parts: list[dict]) -> None:
    """
    Keep the text this task sent the model, so the order can later be shown
    to the model as a past example (Section 7.13: "the same extracted text
    the parser produced"). Only when every part is text: a document read
    visually has no text of its own and can never be an example -- an example
    is never a file or an image. Best effort, like the preview: a document
    whose text couldn't be kept reviews and exports exactly the same.
    """
    if not parts or any(part["type"] != "text" for part in parts):
        return
    body = "\n\n".join(part["text"] for part in parts).strip()
    if not body:
        return
    try:
        path = save_derived(
            tenant_id,
            document_id,
            "extracted_text",
            body.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
        )
        with tenant_session(tenant_id) as session:
            session.execute(
                text("UPDATE documents SET extracted_text_path = :path WHERE id = :id"),
                {"id": str(document_id), "path": path},
            )
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.error(
            "extracted_text_store_failed document_id=%s error_type=%s", document_id, type(exc).__name__
        )


def _plan_examples(
    client: anthropic.Anthropic,
    tenant_id: UUID,
    document_id: UUID,
    sender_email: str | None,
    parts: list[dict],
) -> ExamplePlan:
    """
    Approved-example prompting (Section 7.13). Any failure here means "no
    examples", which is exactly how a tenant with the feature off is read --
    the feature must never cost a document its extraction.
    """
    try:
        plan = example_prompting.plan(
            client,
            tenant_id,
            document_id,
            sender_email=sender_email,
            parts=parts,
            session_factory=tenant_session,
        )
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.error("example_planning_failed document_id=%s error_type=%s", document_id, type(exc).__name__)
        return ExamplePlan(outcome="planning_failed")
    # IDs and outcomes only (Section 7.10).
    logger.info(
        "example_plan document_id=%s outcome=%s identified_by=%s examples=%d routing=%s",
        document_id,
        plan.outcome,
        plan.identified_by,
        len(plan.examples),
        plan.routing is not None,
    )
    return plan


class _StartedRuns:
    """`extract_document`'s on_call_start hook (D-163): commits the started
    run row, in its own transaction, just before the paid call, and keeps its
    id for the outcome row."""

    def __init__(self, tenant_id: UUID, document_id: UUID):
        self.tenant_id = tenant_id
        self.document_id = document_id
        self.extraction: UUID | None = None

    def __call__(self, run_kind: str, model_id: str, counted: int | None) -> None:
        try:
            with tenant_session(self.tenant_id) as session:
                run_id = model_runs.record_started(
                    session, self.tenant_id, self.document_id, run_kind=run_kind, model_id=model_id,
                    counted_input_tokens=counted,
                )
        except Exception as exc:
            # The call goes ahead without its started row (extraction's
            # `_announce` logs this and carries on). If the worker is killed
            # mid-call, that call's cost is on no record: the founder is told
            # now (Q11; D-203).
            _alert_lost_cost_record(self.tenant_id, self.document_id, faults.describe(exc), which="started")
            raise
        if run_kind == "extraction":
            self.extraction = run_id


def _record_runs(
    session: Session,
    tenant_id: UUID,
    document_id: UUID,
    plan: ExamplePlan,
    result: ExtractionResult,
    started_run_id: UUID | None = None,
) -> UUID:
    """The extraction call's outcome row (D-142), pointing at its started row
    (D-163). The routing read, when there was one, is already on record:
    `example_prompting.plan` writes it the moment it returns, so a failure
    after it can't lose it. The document's own cost is then set from the
    whole record, in the same transaction, so every path that records an
    outcome -- saved, failed, waiting on the provider -- keeps every paid
    call on it."""
    run_id = model_runs.record_extraction(
        session, tenant_id, document_id, result, started_run_id=started_run_id
    )
    model_runs.set_document_cost(session, document_id)
    return run_id


def _record_unsaved_extraction(
    tenant_id: UUID,
    document_id: UUID,
    plan: ExamplePlan,
    result: ExtractionResult,
    started_run_id: UUID | None = None,
) -> None:
    """A paid answer whose save failed (DOC-021): that transaction held the
    run record too, so it is written again on its own -- every paid call is
    on the cost record, succeeded or failed (founder, 2026-09-26; D-163).
    Logged, never raised: the document must still end `failed`."""
    try:
        with tenant_session(tenant_id) as session:
            _record_runs(session, tenant_id, document_id, plan, result, started_run_id)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        fault = faults.describe(exc)
        logger.error(
            "extraction_run_not_recorded document_id=%s error_type=%s location=%s",
            document_id,
            fault.exception_type,
            fault.location,
        )
        # A paid call that is on no cost record is a data loss (Section 7.9),
        # so the founder is told (Q11; D-203).
        _alert_lost_cost_record(tenant_id, document_id, fault, which="outcome")


def _alert_lost_cost_record(tenant_id: UUID, document_id: UUID, fault: faults.Fault, *, which: str) -> None:
    """The founder alert for a run row that could not be written: the row
    before the call (`started`) or the one with its outcome (`outcome`).
    Raised once per state, and the document carries the state's key. Its own
    transaction; never raises: the document must still reach its outcome."""
    try:
        with tenant_session(tenant_id) as session:
            key = faults.state_key(
                session, alert_type="cost_record_lost", tenant_id=tenant_id, step=which, fault=fault
            )
            faults.note_on_document(session, document_id, key)
            founder_alerts.raise_fault_alert(
                session,
                alert_type="cost_record_lost",
                severity="high",
                tenant_id=tenant_id,
                document_id=document_id,
                step=which,
                fault=fault,
                state_key=key,
            )
    except Exception as exc:  # noqa: BLE001 -- logged; the document goes on
        logger.error(
            "founder_alert_not_raised document_id=%s alert=cost_record_lost %s",
            document_id,
            faults.for_log(exc),
        )


def _mark_failed(tenant_id: UUID, document_id: UUID, *, raw_response: dict | None = None) -> None:
    """A failure before the model was asked (an unsafe or unconvertible file).
    Its code lives in raw_json, as since D-145, and in failure_code."""
    code = (raw_response or {}).get("error_code")
    with tenant_session(tenant_id) as session:
        document_status.transition(
            session,
            document_id,
            from_statuses=["processing"],
            to="failed",
            values={"raw_json": raw_response, "failure_code": code, "processed_at": document_status.NOW},
        )
    # A code whose catalog message says "DocFlow has been alerted" (a failed
    # conversion, DOC-017) raises that alert (D-145).
    _alert_failure(tenant_id, document_id, code)


def _fail_after_extraction(
    tenant_id: UUID,
    document_id: UUID,
    code: str,
    *,
    step: str,
    fault: faults.Fault,
    raw_response: dict | None = None,
) -> None:
    """A failure after the model answered (saving it, or checking it: DOC-021).
    The answer already on the document stays; `raw_response` is written only
    when saving it was what failed. Then the promised alert (D-145).

    `step` and `fault` say which step of ours failed and where (D-203): the
    state's key goes on the document in this transaction, and the alert is
    raised once for that state, with the exception's type and our code
    location."""
    values: dict = {"failure_code": code, "processed_at": document_status.NOW}
    if raw_response is not None:
        values["raw_json"] = raw_response
    with tenant_session(tenant_id) as session:
        key = faults.state_key(
            session, alert_type="document_failed", tenant_id=tenant_id, step=step, fault=fault
        )
        faults.note_on_document(session, document_id, key)
        document_status.transition(
            session, document_id, from_statuses=["processing"], to="failed", values=values
        )
    _alert_failure(tenant_id, document_id, code, step=step, fault=fault, state_key=key)


# Stage 3c: a parse job stopped at a limit (memory, CPU, wall clock, answer
# size) -- founder-approved wording, Q3.
STOPPED_CODE = "DOC-029"
# A parse job that failed on our side (D-203, PR 1b-i; founder, 2026-10-16):
# an exception no parser raises on a malformed file (`error`), a job that
# crashed some other way, or an answer that failed the checks. Never blamed
# on the customer's file; the founder gets the keyed alert.
PARSE_OURS_CODE = "DOC-030"
PARSE_STEP = "parsing"
# Stage 3d: waited PROVIDER_MAX_WAIT_HOURS on the model provider (the stuck
# sweep uses the same code for a document that reached it while held).
PROVIDER_WAIT_LIMIT_CODE = "DOC-024"


def _fail_parse_ours(
    tenant_id: UUID, document_id: UUID, *, fault: faults.Fault, release_tag: str, detail: str
) -> None:
    """
    DOC-030 (D-203, PR 1b-i): the parse job failed on our side. The state's
    key -- with the parse app's release, where the fault is -- goes on the
    document in the transaction that fails it; the alert is raised once per
    state, in its own transaction, so one that can't be written never undoes
    the failed status.

    `fault` is the exception's type and our code location for an `error`
    answer; for a crash, the cause (signal or exit status) stands in the
    location's place (founder, 2026-10-16).
    """
    with tenant_session(tenant_id) as session:
        key = faults.state_key(
            session,
            alert_type="document_failed",
            tenant_id=tenant_id,
            step=PARSE_STEP,
            fault=fault,
            release_tag=release_tag,
        )
        faults.note_on_document(session, document_id, key)
        document_status.transition(
            session,
            document_id,
            from_statuses=["processing"],
            to="failed",
            values={
                "raw_json": {"error_code": PARSE_OURS_CODE, "detail": f"parse service: {detail}"},
                "failure_code": PARSE_OURS_CODE,
                "processed_at": document_status.NOW,
            },
        )
    try:
        with tenant_session(tenant_id) as session:
            founder_alerts.raise_fault_alert(
                session,
                alert_type="document_failed",
                severity="high",
                tenant_id=tenant_id,
                document_id=document_id,
                step=PARSE_STEP,
                fault=fault,
                state_key=key,
                error_code=PARSE_OURS_CODE,
                release_tag=release_tag,
            )
    except Exception as exc:  # noqa: BLE001 -- an alert that can't be written never undoes the failure
        logger.error(
            "founder_alert_not_raised document_id=%s error_code=%s %s",
            document_id,
            PARSE_OURS_CODE,
            faults.for_log(exc),
        )


def _crash_fault(kind: str, cause: str, module: str) -> faults.Fault:
    """A crash has no exception of ours to name: its cause stands in the
    code location's place in the state key (founder, 2026-10-16)."""
    return faults.Fault(exception_type=kind, module=module, function=cause, line=0)


def _error_fault(answer: parse_client.ParseAnswer) -> faults.Fault:
    module, function, line = (answer.location or "unknown:unknown:0").rsplit(":", 2)
    return faults.Fault(
        exception_type=answer.error_type or "unknown", module=module, function=function, line=int(line)
    )


def _parse_alert(tenant_id: UUID, document_id: UUID, cause: str) -> None:
    """The founder alert for a parse job stopped at a limit (DOC-029, once per
    tenant per day). A crashed job or a bad answer is DOC-030 with the keyed
    alert since PR 1b-i (`_fail_parse_ours`). Its own transaction, after the
    failed status, so an alert that can't be written never undoes it."""
    try:
        with tenant_session(tenant_id) as session:
            founder_alerts.raise_parse_alert(
                session, tenant_id=tenant_id, document_id=document_id, cause=cause
            )
    except Exception as exc:
        logger.error(
            "founder_alert_not_raised document_id=%s error_code=%s %s",
            document_id, STOPPED_CODE, faults.for_log(exc),
        )


def _after_lost_parse(tenant_id: UUID, document_id: UUID, reason: str) -> None:
    """
    Item 6a: record the lost try and apply the sweep's own `decide()` at once,
    so a lost file is retried or failed in seconds, not after the 30-minute
    sweep. One decision, one place: the sweep calls the same function.
    """
    retry = False
    with tenant_session(tenant_id) as session:
        state = document_status.record_parse_lost(session, document_id)
        if state is None:
            return  # the document left `processing` under us
        attempts, timeouts, lost = state
        action, cause = retry_rules.decide(attempts, timeouts, parse_lost_attempts=lost)
        if action == "retry":
            retry = document_status.release_claim(session, document_id)
        else:
            moved = document_status.transition(
                session,
                document_id,
                from_statuses=["processing"],
                to="failed",
                values={"failure_code": retry_rules.STUCK_CODE, "processed_at": document_status.NOW},
            )
            if moved:
                founder_alerts.raise_for_failure(
                    session,
                    tenant_id=tenant_id,
                    error_code=retry_rules.STUCK_CODE,
                    document_id=document_id,
                    cause=cause,
                    detail=retry_rules.failure_detail(attempts, timeouts, lost, last_lost_reason=reason),
                )
    if retry:
        # After the commit: the next job must see the released claim.
        celery_app.send_task(
            "docflow.parse_and_extract", args=[str(tenant_id), str(document_id)], queue="interactive"
        )


def _wait_for_provider(
    tid: UUID, did: UUID, plan: ExamplePlan, result: ExtractionResult, started_run_id: UUID | None
) -> None:
    """
    Stage 3d: the model provider didn't answer (5xx, overloaded, rate
    limited, the network) or refused us for a reason on our side (a wrong
    key, no credit, a retired model -- founder, 2026-10-01). Never the
    customer's fault, so the document waits instead of failing:

    - the failed call stays on the cost record (D-163), as before;
    - `processing -> pending` with the next retry 1, 2, 4, 8 then every 15
      minutes after the first wait (a 429's Retry-After is a floor);
    - after PROVIDER_MAX_WAIT_HOURS of waiting it is failed with DOC-024,
      which blames the provider, not the file, and alerts the founder;
    - the failure counts towards marking the provider down
      (model_provider.record_failure), which holds the dispatcher.
    """
    error = result.provider_error
    assert error is not None
    failed_at_limit = False
    with tenant_session(tid) as session:
        _record_runs(session, tid, did, plan, result, started_run_id)
        waited = document_status.waited_seconds(session, did, "model_provider")
        if waited is not None and waited >= PROVIDER_MAX_WAIT_HOURS * 3600:
            failed_at_limit = document_status.transition(
                session,
                did,
                from_statuses=["processing"],
                to="failed",
                values={
                    "model_id": result.model_id,
                    "prompt_hash": result.prompt_hash,
                    "schema_version": result.schema_version,
                    "raw_json": result.raw_response,
                    "failure_code": PROVIDER_WAIT_LIMIT_CODE,
                    "processed_at": document_status.NOW,
                    "last_wait_error": error.label,
                    "retry_at": None,
                },
            )
        else:
            document_status.to_waiting(
                session,
                did,
                cause="model_provider",
                retry_after_seconds=model_provider.retry_delay_seconds(waited, error.retry_after_seconds),
                error_label=error.label,
            )
    # Labels and ids only (Section 7.10).
    logger.error(
        "provider_wait document_id=%s cause=%s label=%s waited_seconds=%s failed_at_limit=%s",
        did,
        error.cause,
        error.label,
        waited,
        failed_at_limit,
    )
    if failed_at_limit:
        _alert_failure(tid, did, PROVIDER_WAIT_LIMIT_CODE)
    model_provider.record_failure(error)


def _alert_failure(
    tenant_id: UUID,
    document_id: UUID,
    error_code: str | None,
    *,
    step: str | None = None,
    fault: faults.Fault | None = None,
    state_key: str | None = None,
) -> None:
    """
    Raise the founder alert a failure's catalog message promises (D-145).

    Its own transaction, after the document is already marked failed: an
    alert that can't be written must never roll back the record of what
    happened to the document. It is logged instead, with IDs only.

    With a fault (a step of ours failed), the alert carries the exception's
    type and our code location and is raised once per state (D-203).
    """
    fault_detail = {} if fault is None else {"step": step, "fault": fault, "state_key": state_key}
    try:
        with tenant_session(tenant_id) as session:
            founder_alerts.raise_for_failure(
                session, tenant_id=tenant_id, error_code=error_code, document_id=document_id, **fault_detail
            )
    except Exception as exc:
        logger.error(
            "founder_alert_not_raised document_id=%s error_code=%s %s",
            document_id,
            error_code,
            faults.for_log(exc),
        )


_PROVENANCE_HEADER_FIELDS = (
    "po_number",
    "order_date",
    "requested_delivery_date",
    "buyer_name",
    "buyer_contact_email",
    "ship_to_address",
    "payment_terms",
    "order_total",
    "currency",
    "notes",
)

_PROVENANCE_LINE_FIELDS = ("sku", "description", "quantity", "unit", "unit_price", "line_total")


def _extracted_provenance(values: dict, fields: tuple[str, ...]) -> dict:
    """
    Per-field provenance (Section 9 / Section 7.1: "Provenance for every
    value: extracted, edited-by-human, or mapped"). Every value this task
    writes came straight from the model, so every present field is
    `extracted`; a field the model returned as null has no value and
    therefore no provenance. `learned_rule:<id>` and
    `human_edit:<review_action_id>` are written by the matching and review
    slices that produce them.
    """
    return {name: "extracted" for name in fields if values.get(name) is not None}


def _overall_confidence(header_confidence: dict, schema: FieldSchema) -> Decimal:
    """
    CLAUDE.md Section 7.1: "Overall document confidence is the minimum of
    required-field confidences, not an average." Required means required for
    THIS tenant (D-120): the fields its current field schema marks required.
    """
    return schema.overall_confidence(header_confidence)


@celery_app.task(
    name="docflow.parse_and_extract",
    time_limit=DOCUMENT_TASK_TIME_LIMIT_SECONDS,
    Request="app.timeouts:DocumentTaskRequest",
)
def parse_and_extract(tenant_id: str, document_id: str) -> None:
    """
    Read one document and put it in front of a reviewer -- once, and only
    when it has been fully checked (review findings H1, H3; D-158).

    * **Claim first.** The document moves to `processing` by compare-and-set
      (`document_status.claim_for_processing`). A redelivered or duplicated
      job, or one for a document that has since been approved, finds nothing
      to claim and does nothing -- the queue acknowledges late, so a worker
      restart redelivers, and an approved order must never be knocked back.
    * **The model's answer is saved while still `processing`**, in one
      transaction with the header, the lines and the run record. If the
      worker dies after that, the next claim resumes from the saved answer
      and never pays for a second extraction.
    * **`needs_review` only with the checks.** Buyer identification, matching
      and duplicate detection each run in their own transaction (a failure
      there must never cost the paid-for extraction, D-058) and a step that
      fails is recorded, not just logged. Validation then runs, and its
      warnings -- including VAL-016 for any step that didn't finish -- are
      committed in the SAME transaction as the move to `needs_review`. If
      validation itself fails, the document is `failed` with DOC-021 and the
      founder is alerted: never in review with zero warnings.
    * **Then a dispatch pass** (Stage 3d, Q4): this process has just freed a
      slot, so the next waiting document goes now, not at the next beat.
    * **Never on the dispatch queue** (founder, 2026-10-01): that queue's one
      process must never hold a document. A document task that arrives there
      claims nothing and is put on `interactive`, where it belongs.
    """
    if (parse_and_extract.request.delivery_info or {}).get("routing_key") == DISPATCH_QUEUE:
        logger.error("document_task_on_dispatch_queue document_id=%s resent=interactive", document_id)
        send_document(UUID(tenant_id), UUID(document_id))
        return
    try:
        _parse_and_extract(UUID(tenant_id), UUID(document_id))
    finally:
        dispatch_after_task()


def dispatch_after_task() -> None:
    """The dispatch pass at the end of every document task. Never raises: the
    beat backstop runs another within DISPATCH_INTERVAL_SECONDS."""
    try:
        dispatch.run_pass(send_document)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.error("dispatch_after_task_failed error_type=%s", type(exc).__name__)


def send_document(tenant_id: UUID, document_id: UUID) -> None:
    """How the dispatcher puts a document task on the queue: always the
    interactive queue -- every choice between tenants and lanes has already
    been made, so the queue only ever holds what the worker can take next."""
    celery_app.send_task(
        "docflow.parse_and_extract", args=[str(tenant_id), str(document_id)], queue="interactive"
    )


def _parse_and_extract(tid: UUID, did: UUID) -> None:

    with tenant_session(tid) as session:
        row = session.execute(
            text(
                "SELECT storage_path, original_filename, status, sender_email, "
                "current_extraction_run_id, content_sha256 FROM documents WHERE id = :id"
            ),
            {"id": str(did)},
        ).mappings().first()
        if row is None:
            logger.error("parse_and_extract_missing_document document_id=%s", did)
            return
        # A test-batch file (`staged`) waits for the founder's "Run
        # extraction" (D-112), a held one (`quarantined`) for its release,
        # and anything already processed is done: none of them is claimable,
        # so none of them reaches the model from here.
        if not document_status.claim_for_processing(session, did):
            logger.info(
                "parse_and_extract_not_claimed document_id=%s status=%s", did, row["status"]
            )
            return
        # D-163: a paid call an earlier, dead attempt started and never
        # finished goes on the cost record now, as a lower bound.
        model_runs.close_lost_runs(session, tid, did)
        # One budget for the whole read, counted from the claim (D-163): the
        # stuck-document sweep may take a claim over after
        # STUCK_PROCESSING_TIMEOUT_MIN, and this read must be over by then.
        read_deadline = time.monotonic() + EXTRACTION_DEADLINE_SECONDS
        storage_path = row["storage_path"]
        original_filename = row["original_filename"]
        sender_email = row.get("sender_email")
        already_extracted = row.get("current_extraction_run_id") is not None
        expected_sha256 = row.get("content_sha256")

    if already_extracted:
        # An earlier attempt saved the model's answer and then stopped (a
        # restart, a crash, a timeout). Resume from it: no second model call.
        logger.info("parse_and_extract_resuming document_id=%s", did)
        _finish(tid, did)
        return

    # Stage 3b (Q3, Q5). Outside every broad `except`, so a Storage failure
    # can never be relabelled DOC-005.
    try:
        content = read_file(tid, storage_path)
    except StorageUnavailableError:
        # An outage: the document waits it out without using an attempt
        # (Stage 3d: a real `processing -> pending` wait, retried by the
        # dispatcher every STORAGE_WAIT_RETRY_MINUTES, no maximum).
        logger.error("storage_read_failed document_id=%s", did)
        with tenant_session(tid) as session:
            document_status.to_waiting(
                session,
                did,
                cause="storage",
                retry_after_seconds=STORAGE_WAIT_RETRY_MINUTES * 60,
                error_label="storage_unavailable",
            )
            founder_alerts.raise_storage_unavailable(session, tenant_id=tid, where="worker")
        return
    except StorageObjectMissingError:
        # Not an outage: waiting would retry it forever. A data fault, loudly.
        logger.error("stored_original_missing document_id=%s", did)
        _mark_failed(tid, did, raw_response={"error_code": "DOC-026", "detail": "missing"})
        return
    except UnsafeStoragePathError as exc:
        # The row's path breaks the prefix rules, so nothing is read. A retry
        # would refuse it again: a clean DOC-026 now (founder, 2026-09-30).
        founder_alerts.report_refused_storage_path(exc, tenant_id=tid, where="worker", ref_id=did)
        _mark_failed(tid, did, raw_response={"error_code": "DOC-026", "detail": "path_refused"})
        return
    if expected_sha256 and hashlib.sha256(content).hexdigest() != expected_sha256:
        # The bytes aren't the file that was received: never read the wrong one.
        logger.error("stored_original_hash_mismatch document_id=%s", did)
        _mark_failed(tid, did, raw_response={"error_code": "DOC-026", "detail": "hash_mismatch"})
        return

    # Stage 3c: the file is opened only by the parse service, in a sandboxed
    # job of its own; it re-validates the bytes there before anything else.
    # Outside every broad `except`, so a service failure can never be
    # relabelled DOC-005.
    try:
        answer = parse_client.parse_document(content, original_filename)
    except parse_client.ParseUnavailable as exc:
        # Never got in: not the file's fault. The document waits, no try used
        # (founder, Q6: only "never got in" waits; "got in, never came out"
        # below still uses up tries, so a poisoned file can't wait forever).
        logger.error("parse_service_unavailable document_id=%s reason=%s", did, exc.reason)
        with tenant_session(tid) as session:
            document_status.to_waiting(
                session,
                did,
                cause="parse_service",
                retry_after_seconds=PARSE_SERVICE_WAIT_RETRY_MINUTES * 60,
                error_label=f"parse_service:{exc.reason}",
            )
            founder_alerts.raise_parse_service_unavailable(
                session, tenant_id=tid, where="worker", reason=exc.reason
            )
        return
    except parse_client.ParseLost as exc:
        # Got in, never came out: a timeout-class try (item 6a), decided now.
        logger.error("parse_lost document_id=%s reason=%s", did, exc.reason)
        _after_lost_parse(tid, did, exc.reason)
        return
    except parse_client.ParseAnswerInvalid as exc:
        logger.error("parse_answer_invalid document_id=%s reason=%s", did, exc.reason)
        _fail_parse_ours(
            tid,
            did,
            fault=_crash_fault("invalid_answer", exc.reason, "docflow_core.parse_client"),
            release_tag=parse_client.UNKNOWN_RELEASE,
            detail=f"invalid:{exc.reason}",
        )
        return

    if answer.outcome == "rejected":
        logger.error("parse_and_extract_rejected document_id=%s error_code=%s", did, answer.code)
        _mark_failed(
            tid, did, raw_response={"error_code": answer.code, "detail": "rejected by the parse service"}
        )
        return
    if answer.outcome == "stopped":
        logger.error("parse_stopped document_id=%s cause=%s", did, answer.cause)
        _mark_failed(
            tid, did, raw_response={"error_code": STOPPED_CODE, "detail": f"stopped: {answer.cause}"}
        )
        _parse_alert(tid, did, f"stopped:{answer.cause}")
        return
    if answer.outcome == "crashed":
        logger.error("parse_crashed document_id=%s cause=%s release=%s", did, answer.cause, answer.release)
        cause = answer.cause or "unknown"
        _fail_parse_ours(
            tid,
            did,
            fault=_crash_fault("crashed", cause, "parse_service.job"),
            release_tag=answer.release,
            detail=f"crashed:{cause}",
        )
        if f"crashed:{cause}" == founder_alerts.SECCOMP_KILL_CAUSE:
            # Every seccomp kill keeps its own alert too (founder, 2026-10-01).
            founder_alerts.alert_seccomp_kill(tid, where="worker", ref_id=did, error_code=PARSE_OURS_CODE)
        return
    if answer.outcome == "error":
        # Ours, not the file's (D-203, PR 1b-i): IDs, the type and our code
        # location only (7.10).
        logger.error(
            "parse_error document_id=%s tenant_id=%s error_type=%s location=%s release=%s",
            did, tid, answer.error_type, answer.location, answer.release,
        )
        _fail_parse_ours(tid, did, fault=_error_fault(answer), release_tag=answer.release, detail="error")
        return

    parts = answer.parts
    content_blocks = _envelope(parts)

    # Before the model call, so the reviewer can see the original even if
    # extraction fails. Built from what the parse service returned; the API
    # only serves what this wrote.
    _store_preview(tid, did, answer)
    _store_extracted_text(tid, did, parts)

    settings = get_settings()
    # Two retries is the SDK's default, stated here because the read budget
    # above is sized with it in mind.
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=2)
    plan = _plan_examples(client, tid, did, sender_email, parts)
    routing_error = plan.routing.provider_error if plan.routing is not None else None
    if routing_error is not None and routing_error.is_our_configuration:
        # The routing model refused us (founder: alert, don't hold). This
        # document goes on without examples, as any failed routing call does.
        model_provider.alert_routing_failure(routing_error)
    started = _StartedRuns(tid, did)
    result = extract_document(
        client, content_blocks, examples=plan.examples, deadline=read_deadline, on_call_start=started
    )

    if result.provider_error is None:
        # The provider answered (whatever the answer): it is up. Marks it up
        # again after an outage, which releases everything waiting on it.
        model_provider.record_success()
    elif result.provider_error.is_wait:
        _wait_for_provider(tid, did, plan, result, started.extraction)
        return

    if not result.ok:
        with tenant_session(tid) as session:
            _record_runs(session, tid, did, plan, result, started.extraction)
            document_status.transition(
                session,
                did,
                from_statuses=["processing"],
                to="failed",
                values={
                    "model_id": result.model_id,
                    "prompt_hash": result.prompt_hash,
                    "schema_version": result.schema_version,
                    "raw_json": result.raw_response,
                    "failure_code": result.error_code or "DOC-008",
                    "processed_at": document_status.NOW,
                    **document_status.WAIT_CLEARED,
                },
            )
        # DOC-008 / DOC-009 / DOC-020 tell the reader DocFlow has been alerted (D-145).
        _alert_failure(tid, did, result.error_code or "DOC-008")
        return

    try:
        _save_extraction(tid, did, plan, result, started.extraction)
    except Exception as exc:  # noqa: BLE001 -- the document must not be stranded (M3)
        # Nothing of the answer was written (one transaction). Keep the paid
        # answer on the failed document, with a code, and tell the founder.
        fault = faults.describe(exc)
        logger.error("extraction_save_failed document_id=%s %s", did, faults.for_log(exc))
        _record_unsaved_extraction(tid, did, plan, result, started.extraction)
        _fail_after_extraction(
            tid, did, "DOC-021", step="saving", fault=fault, raw_response=result.raw_response
        )
        return

    _finish(tid, did)


def _save_extraction(
    tid: UUID, did: UUID, plan: ExamplePlan, result: ExtractionResult, started_run_id: UUID | None = None
) -> None:
    """The model's answer, the header, the lines and the run record, in one
    transaction. The status stays `processing`: nothing is reviewable yet."""
    header = result.header
    with tenant_session(tid) as session:
        # The tenant's field schema decides which fields count towards the
        # document's confidence and which are checked (D-120). Recorded on the
        # document, so a later change never makes its numbers unexplainable.
        schema = field_schema.current(session, tid)
        overall_confidence = _overall_confidence(result.header_confidence, schema)
        run_id = _record_runs(session, tid, did, plan, result, started_run_id)
        session.execute(
            text(
                """
                UPDATE documents
                SET model_id = :model_id, prompt_hash = :prompt_hash,
                    schema_version = :schema_version, input_tokens = :input_tokens,
                    output_tokens = :output_tokens,
                    injection_suspected = :injection_suspected,
                    overall_confidence = :overall_confidence, raw_json = :raw_json,
                    field_schema_version = :field_schema_version,
                    current_extraction_run_id = :run_id
                WHERE id = :id AND status = 'processing'
                """
            ),
            {
                "id": str(did),
                "model_id": result.model_id,
                "prompt_hash": result.prompt_hash,
                "schema_version": result.schema_version,
                "field_schema_version": schema.version or None,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "run_id": str(run_id),
                "injection_suspected": result.injection_suspected,
                "overall_confidence": str(overall_confidence),
                "raw_json": result.raw_response,
            },
        )

        session.execute(
            text(
                """
                INSERT INTO document_headers
                    (document_id, tenant_id, po_number, order_date, requested_delivery_date,
                     buyer_name, buyer_contact_email, ship_to_address, payment_terms,
                     order_total, currency, notes, header_confidence, currency_inferred,
                     field_provenance)
                VALUES
                    (:document_id, :tenant_id, :po_number, :order_date, :requested_delivery_date,
                     :buyer_name, :buyer_contact_email, :ship_to_address, :payment_terms,
                     :order_total, :currency, :notes, :header_confidence, :currency_inferred,
                     :field_provenance)
                """
            ),
            {
                "document_id": str(did),
                "tenant_id": str(tid),
                "po_number": header["po_number"],
                "order_date": header["order_date"],
                "requested_delivery_date": header["requested_delivery_date"],
                "buyer_name": header["buyer_name"],
                "buyer_contact_email": header["buyer_contact_email"],
                "ship_to_address": header["ship_to_address"],
                "payment_terms": header["payment_terms"],
                "order_total": plain_or_none(header["order_total"]),
                "currency": header["currency"],
                "notes": header["notes"],
                "header_confidence": result.header_confidence,
                "currency_inferred": result.currency_inferred,
                "field_provenance": _extracted_provenance(header, _PROVENANCE_HEADER_FIELDS),
            },
        )

        for line in result.lines:
            session.execute(
                text(
                    """
                    INSERT INTO document_lines
                        (id, document_id, tenant_id, line_number, sku, description,
                         quantity, unit, unit_price, line_total, confidence, field_provenance)
                    VALUES
                        (:id, :document_id, :tenant_id, :line_number, :sku, :description,
                         :quantity, :unit, :unit_price, :line_total, :confidence, :field_provenance)
                    """
                ),
                {
                    "id": str(uuid4()),
                    "document_id": str(did),
                    "tenant_id": str(tid),
                    "line_number": line["line_number"],
                    "sku": line["sku"],
                    "description": line["description"],
                    "quantity": plain_or_none(line["quantity"]),
                    "unit": line["unit"],
                    "unit_price": plain_or_none(line["unit_price"]),
                    "line_total": plain_or_none(line["line_total"]),
                    "confidence": str(line["confidence"]) if line["confidence"] is not None else None,
                    "field_provenance": _extracted_provenance(line, _PROVENANCE_LINE_FIELDS),
                },
            )


def _finish(tid: UUID, did: UUID) -> None:
    """Post-processing, then validation and the move to `needs_review` in one
    transaction. Runs after a fresh extraction and when resuming one."""
    with tenant_session(tid) as session:
        header = session.execute(
            text(
                "SELECT buyer_name, buyer_contact_email FROM document_headers WHERE document_id = :id"
            ),
            {"id": str(did)},
        ).mappings().first()
        field_schema_version = session.execute(
            text("SELECT field_schema_version FROM documents WHERE id = :id"), {"id": str(did)}
        ).scalar_one_or_none()
        # The same schema version the confidence was computed with, so one
        # document is never half-checked under two versions (D-120).
        schema = field_schema.at_version(session, tid, field_schema_version)

    issues: list[str] = []
    # Where each step that didn't finish failed: the exception's type and our
    # code location, never its message (D-203).
    step_faults: dict[str, faults.Fault] = {}
    # Learned rules that fired, added to `times_applied` once, in the move to
    # review below -- so a resumed document is never counted twice (Stage 3a).
    rules_applied: dict[UUID, int] = {}

    # Buyer identification runs first, because Section 7.6's learned mappings
    # are scoped to a buyer. Own transaction (D-058): a failure here must never
    # roll back the paid-for extraction -- but it is recorded and shown now
    # (VAL-016), not only logged.
    buyer_id: UUID | None = None
    try:
        with tenant_session(tid) as session:
            identification = identify_and_link_buyer(
                session,
                tid,
                did,
                buyer_name=header.get("buyer_name") if header else None,
                buyer_contact_email=header.get("buyer_contact_email") if header else None,
            )
            buyer_id = identification.buyer_id
            if identification.rule_id is not None:
                rules_applied[identification.rule_id] = rules_applied.get(identification.rule_id, 0) + 1
    except Exception as exc:  # noqa: BLE001 -- recorded as a pipeline issue
        # No exception message: a database error's text can carry bound
        # parameters, which here are customer data (Section 7.10).
        step_faults["buyer_identification"] = fault = faults.describe(exc)
        logger.error("buyer_identification_failed document_id=%s %s", did, faults.for_log(exc))
        issues.append("buyer_identification")

    # Matching. `buyer_id` None is not a failure: matching then uses the
    # tenant-wide rules only, never another buyer's.
    try:
        with tenant_session(tid) as session:
            summary = match_document_lines(session, tid, did, buyer_id=buyer_id)
        for rule_id, count in summary.rules_applied.items():
            rules_applied[rule_id] = rules_applied.get(rule_id, 0) + count
        logger.info(
            "matching_complete document_id=%s considered=%d matched=%d",
            did,
            summary.lines_considered,
            summary.lines_matched,
        )
    except Exception as exc:  # noqa: BLE001 -- recorded as a pipeline issue
        step_faults["matching"] = fault = faults.describe(exc)
        logger.error("matching_failed document_id=%s %s", did, faults.for_log(exc))
        issues.append("matching")

    # Duplicate / change-order detection (Section 7.8). Here as well as at
    # ingest because the PO number the change-order case needs only exists
    # after extraction; re-running the content-hash half is idempotent.
    try:
        with tenant_session(tid) as session:
            relationships = detect_document_relationships(session, tid, did)
        logger.info(
            "duplicate_detection_complete document_id=%s duplicate=%s change_order=%s",
            did,
            relationships.is_possible_duplicate,
            relationships.is_possible_change_order,
        )
    except Exception as exc:  # noqa: BLE001 -- recorded as a pipeline issue
        step_faults["duplicate_detection"] = fault = faults.describe(exc)
        logger.error("duplicate_detection_failed document_id=%s %s", did, faults.for_log(exc))
        issues.append("duplicate_detection")

    # Validation and the move into review: one transaction. Validation reports
    # on everything above -- values, matches, duplicate flags and any step
    # that didn't finish -- and changes no value (Section 7.7).
    try:
        with tenant_session(tid) as session:
            session.execute(
                text("UPDATE documents SET pipeline_issues = :issues WHERE id = :id"),
                {"id": str(did), "issues": issues},
            )
            # Each failed step's state key goes on the document (D-203), so
            # the Console can count the documents a state has hit.
            step_keys: dict[str, str] = {}
            for step in issues:
                step_keys[step] = faults.state_key(
                    session,
                    alert_type="pipeline_step_failed",
                    tenant_id=tid,
                    step=step,
                    fault=step_faults[step],
                )
                faults.note_on_document(session, did, step_keys[step])
            checked = validate_document(session, tid, did, schema.rules())
            moved = document_status.transition(
                session,
                did,
                from_statuses=["processing"],
                to="needs_review",
                values={
                    "processed_at": document_status.NOW,
                    "failure_code": document_status.NULL,
                    **document_status.WAIT_CLEARED,
                },
            )
            if not moved:
                # Someone else moved it while we worked (the stuck sweep gave
                # up on it, say). Their decision stands; undo ours.
                raise _AlreadyMovedOn()
            # One alert per step that failed, once per state: its exception
            # type and our code location, in this release, for this tenant.
            # A second document hit by the same state raises nothing and
            # carries the same key (D-203; it was one alert per document).
            for step in issues:
                founder_alerts.raise_fault_alert(
                    session,
                    alert_type="pipeline_step_failed",
                    severity="high",
                    tenant_id=tid,
                    document_id=did,
                    step=step,
                    fault=step_faults[step],
                    state_key=step_keys[step],
                )
            # Exactly once per document: this transaction is the only one
            # that moves it to review (compare-and-set above).
            bump_times_applied(session, rules_applied)
            # The "needs review" digest (slice 5.8c, D-131), in the same
            # transaction so a worker killed straight after the commit can't
            # leave the order out of the digest -- behind a savepoint, so a
            # notification that fails can never cost the document its checks.
            digest = session.begin_nested()
            try:
                review_digest.note_needs_review(session, tid, did)
                digest.commit()
            except Exception as exc:  # noqa: BLE001 -- a notification, not the document
                digest.rollback()
                logger.error("review_digest_failed document_id=%s error_type=%s", did, type(exc).__name__)
        # Counts only -- never a value (Section 7.10).
        logger.info(
            "validation_complete document_id=%s warnings=%d created=%d resolved=%d issues=%d",
            did,
            checked.evaluated,
            checked.created,
            checked.resolved,
            len(issues),
        )
    except _AlreadyMovedOn:
        logger.info("parse_and_extract_moved_on document_id=%s", did)
        return
    except Exception as exc:  # noqa: BLE001 -- never silent: failed + DOC-021 + alert
        fault = faults.describe(exc)
        logger.error("validation_failed document_id=%s %s", did, faults.for_log(exc))
        _fail_after_extraction(tid, did, "DOC-021", step="validation", fault=fault)
        return


class _AlreadyMovedOn(Exception):
    """The document left `processing` under us; roll back and leave it be."""
