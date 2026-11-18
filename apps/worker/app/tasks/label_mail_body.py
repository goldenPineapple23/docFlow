"""
The labelling task (B1; docs/designs/b1.md 2.2; D-246): one email's text,
from `pending` to a decision. The webhook sends it once, after its commit,
for every text that was not held (`email_intake.LABEL_MAIL_BODY_TASK`).

One attempt and no retries of its own, and every failure ends in "make it a
document" (D-230 item 4): a text DocFlow can't read or can't label is put in
front of a person, never dropped. The decision rules and the database steps
are `docflow_core.mail_bodies`; this module only reads, asks and writes in
order:

1. A quick look, with no lock: nothing to do unless the text is `pending`
   and no label call was ever started for it (a second delivery stops here).
2. Read the stored part from Storage and check its hash. A mismatch makes a
   document with no parse and no call, carrying the recorded hash, so the
   parse step refuses the changed bytes as DOC-026, visibly (item b).
3. The parse service turns each part into text; the text is never opened
   here (Section 7.11). With both parts, `choose_read_text` picks one.
4. The `Subject` / `From` / `Date` lines and the text are stored whole: that
   is what the reviewer is shown and what extraction reads.
5. `mail_bodies.call_and_decide`: the budget cut, the claim (the started row,
   committed before the call), one call, the decision.
6. One transaction: the outcome row, then the guarded move out of `pending`
   and the document or the ignored-mail row (`mail_bodies.settle_body`).
7. A document made: a dispatch pass now, as after every document task.

On the worker's document queue: its one slot runs a document or a label,
never both. During a backlog the stuck sweep may make a waiting text a
document before its label runs (founder, 2026-11-18: accepted for the demo
half). The task's own time limit (MAIL_LABEL_TASK_TIME_LIMIT_SECONDS) bounds
it from the moment it starts; the sweep measures a text's age from its
started row, or from the email's arrival before any claim.
"""

from __future__ import annotations

import hashlib
import logging
from uuid import UUID

import anthropic
from docflow_core import faults, founder_alerts, mail_bodies, model_provider, parse_client
from docflow_core.config import get_settings
from docflow_core.constants import MAIL_LABEL_TASK_TIME_LIMIT_SECONDS
from docflow_core.db import tenant_session
from docflow_core.mail_bodies import DocumentPart
from docflow_core.mail_labels import MailLabelResult, decide_body
from docflow_core.storage import (
    StorageObjectMissingError,
    StorageUnavailableError,
    UnsafeStoragePathError,
    read_file,
    save_file,
)

from app.celery_app import celery_app
from app.tasks import parse_and_extract

logger = logging.getLogger(__name__)

# The stored text as read (header lines and the chosen part): a write-once
# object of its own, named for what it is.
READ_TEXT_FILENAME = "Email text read.txt"

# The SDK's own retries inside the one label call; the task's time limit is
# sized with them (constants.MAIL_LABEL_TASK_TIME_LIMIT_SECONDS).
LABEL_CLIENT_MAX_RETRIES = 2

_PARSE_ERRORS = (parse_client.ParseUnavailable, parse_client.ParseLost, parse_client.ParseAnswerInvalid)


@celery_app.task(name="docflow.label_mail_body", time_limit=MAIL_LABEL_TASK_TIME_LIMIT_SECONDS)
def label_mail_body(tenant_id: str, raw_email_id: str) -> None:
    label_email_text(UUID(tenant_id), UUID(raw_email_id))


def label_email_text(tid: UUID, rid: UUID) -> None:
    with tenant_session(tid) as session:
        email = mail_bodies.load_email_text(session, rid)
        if email is None or email.body_state != "pending":
            logger.info(
                "mail_label_not_pending raw_email_id=%s state=%s", rid, email.body_state if email else None
            )
            return
        if mail_bodies.label_call_started(session, rid):
            # An earlier delivery made the call or is making it; if it died,
            # the stuck sweep makes the document. Never a second call.
            logger.info("mail_label_already_started raw_email_id=%s", rid)
            return
    if email.storage_path is None or email.kind is None or email.sha256 is None:
        logger.error("mail_label_no_stored_text raw_email_id=%s", rid)
        return
    stored = DocumentPart(storage_path=email.storage_path, kind=email.kind, sha256=email.sha256)

    content = _read_stored(tid, rid, email.storage_path)
    if content is None:
        _settle_without_call(tid, rid, stored, mail_bodies.NO_LABEL_TEXT_UNREADABLE)
        return
    if hashlib.sha256(content).hexdigest() != email.sha256:
        logger.error("mail_label_stored_text_hash_mismatch raw_email_id=%s", rid)
        _settle_without_call(tid, rid, stored, mail_bodies.NO_LABEL_HASH_MISMATCH)
        return

    main_text = _parse_text(content, email.kind, rid)
    html_text: str | None = None
    html_part: DocumentPart | None = None
    if email.html_path is not None:
        html_content = _read_stored(tid, rid, email.html_path)
        if html_content is not None:
            html_text = _parse_text(html_content, "html", rid)
            html_part = DocumentPart(
                storage_path=email.html_path, kind="html", sha256=hashlib.sha256(html_content).hexdigest()
            )
    if email.kind == "text":
        chosen = mail_bodies.choose_read_text(main_text, html_text)
    else:
        chosen = mail_bodies.choose_read_text(None, main_text)
    if chosen is None:
        _settle_without_call(tid, rid, stored, mail_bodies.NO_LABEL_TEXT_UNREADABLE)
        return
    chosen_kind, body_text = chosen
    part = html_part if (chosen_kind == "html" and email.kind == "text" and html_part) else stored

    read_text = mail_bodies.full_text(email, body_text)
    try:
        text_path = save_file(tid, READ_TEXT_FILENAME, read_text.encode("utf-8"))
    except (StorageUnavailableError, UnsafeStoragePathError) as exc:
        logger.error("mail_label_read_text_not_stored raw_email_id=%s error_type=%s", rid, type(exc).__name__)
        _settle_without_call(tid, rid, part, mail_bodies.NO_LABEL_TEXT_UNREADABLE)
        return

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=LABEL_CLIENT_MAX_RETRIES)
    outcome = mail_bodies.call_and_decide(
        client, read_text, has_attachment=email.has_attachment_document, claim=_claim(tid, rid)
    )
    if not outcome.claimed:
        logger.info("mail_label_claimed_elsewhere raw_email_id=%s", rid)
        return
    assert outcome.result is not None and outcome.decision is not None and outcome.started_run_id
    result = outcome.result
    if result.provider_error is not None and result.provider_error.is_our_configuration:
        # The routing model refused us: the existing alert (b1.md section 5).
        model_provider.alert_routing_failure(result.provider_error)

    try:
        with tenant_session(tid) as session:
            mail_bodies.load_email_text(session, rid, lock=True)
            if not mail_bodies.record_label_outcome(
                session, tid, rid, result, started_run_id=outcome.started_run_id
            ):
                logger.info("mail_label_outcome_already_closed raw_email_id=%s", rid)
            settled = mail_bodies.settle_body(
                session,
                tid,
                rid,
                decision=outcome.decision,
                label=result.label,
                part=part,
                text_path=text_path,
                no_label_reason=mail_bodies.NO_LABEL_CALL_FAILED if result.label is None else None,
            )
    except Exception:
        # That transaction held the paid call's outcome row too: write it again
        # on its own, so the call stays on the cost record (as an extraction's
        # does, D-163). The text stays `pending`; the stuck sweep decides it.
        _record_outcome_alone(tid, rid, result, outcome.started_run_id)
        raise
    if settled.document_id is not None:
        parse_and_extract.dispatch_after_task()


def _record_outcome_alone(tid: UUID, rid: UUID, result: MailLabelResult, started_run_id: UUID) -> None:
    """Never raises: the task's own failure is what goes on."""
    try:
        with tenant_session(tid) as session:
            mail_bodies.record_label_outcome(session, tid, rid, result, started_run_id=started_run_id)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        # Logged with ids only. No founder alert yet: `cost_record_lost` is
        # keyed on a document, and a label run has none (asked, 2026-11-18).
        logger.error(
            "mail_label_run_not_recorded raw_email_id=%s %s", rid, faults.for_log(exc)
        )


def _read_stored(tid: UUID, rid: UUID, path: str) -> bytes | None:
    """One stored part's bytes, or None when Storage couldn't give them. A
    path the prefix rules refuse is reported (Stage 3b) and nothing is read."""
    try:
        return read_file(tid, path)
    except UnsafeStoragePathError as exc:
        founder_alerts.report_refused_storage_path(exc, tenant_id=tid, where="mail_label", ref_id=rid)
        return None
    except (StorageUnavailableError, StorageObjectMissingError) as exc:
        logger.error("mail_label_text_not_read raw_email_id=%s error_type=%s", rid, type(exc).__name__)
        return None


def _claim(tid: UUID, rid: UUID) -> mail_bodies.Claim:
    def claim(counted: int | None) -> UUID | None:
        with tenant_session(tid) as session:
            return mail_bodies.claim_label_call(session, tid, rid, counted_input_tokens=counted)

    return claim


def _parse_text(content: bytes, kind: str, rid: UUID) -> str | None:
    """The parse service's text for one stored part, or None when it couldn't
    give one (never got in, got lost, refused, stopped, crashed)."""
    try:
        answer = parse_client.parse_document(content, mail_bodies.TEXT_DOCUMENT_FILENAMES[kind])
    except _PARSE_ERRORS as exc:
        logger.error("mail_label_parse_failed raw_email_id=%s error_type=%s", rid, type(exc).__name__)
        return None
    if answer.outcome != "ok":
        logger.error("mail_label_parse_outcome raw_email_id=%s outcome=%s", rid, answer.outcome)
        return None
    return mail_bodies.parsed_text(answer.parts)


def _settle_without_call(tid: UUID, rid: UUID, part: DocumentPart, reason: str) -> None:
    """A text that can't be labelled becomes a document with no call (D-230
    item 4), `labelled` with no label. Nothing is claimed, so no run row."""
    with tenant_session(tid) as session:
        settled = mail_bodies.settle_body(
            session,
            tid,
            rid,
            decision=decide_body(None, text_readable=False),
            label=None,
            part=part,
            text_path=None,
            no_label_reason=reason,
        )
    if settled.document_id is not None:
        parse_and_extract.dispatch_after_task()

