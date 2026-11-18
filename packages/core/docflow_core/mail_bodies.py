"""
An email's own text, from the webhook's `pending` to its decision (B1;
docs/designs/b1.md 2.2; D-246).

The labelling task (`apps/worker/app/tasks/label_mail_body.py`) is a thin
caller of this module, so what it decides is testable without a queue:

* `choose_read_text` -- which part is read when an email carries both a text
  and an HTML part (the founder's answer 1: the text part, unless it has
  less than half the HTML part's visible characters).
* `call_and_decide` -- the budget cut, the token count, the claim, one call,
  `decide_body`. The claim is what stops a second paid call.
* `claim_label_call`, `record_label_outcome`, `settle_body` -- the database
  steps, each under a lock on the email's `raw_emails` row.

Concurrency (founder, 2026-11-18, item a). Celery acknowledges late, so a
task can be delivered twice, and the stuck sweep may act on a body a slow
task still holds:

1. The claim, just before the call, locks the row and inserts the `started`
   run row (D-163) only while the text is still `pending` and no label call
   was ever started for it. A second delivery finds the started row and
   makes no call. The claim is made outside `label_mail_body`'s call hook on
   purpose: that hook can never stop a call (`extraction._announce`).
2. The final transaction locks the row, writes the outcome row with
   `ON CONFLICT (started_run_id) DO NOTHING` (one outcome per start, 0034),
   then moves the text out of `pending` with a guarded UPDATE. Only if that
   UPDATE touched the row does it write the document or the ignored-mail
   row. Whichever of the task and the sweep comes second does nothing.

`body_state` after a decision is `labelled`: "the label step is finished".
The label columns are null when there was no label answer (a failed call, a
hash mismatch, unreadable text, the stuck sweep); each such path logs one
line with the email's id and the reason (founder, 2026-11-18, answer 1).

The text is never logged (Section 7.10): ids, sizes, the label and the
confidence only.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import allowance, model_runs
from docflow_core.duplicates import find_content_duplicate_at_ingest
from docflow_core.extraction import ROUTING_MODEL, _defang, count_request_tokens
from docflow_core.mail_labels import (
    MAIL_LABEL_RUN_KIND,
    MAIL_LABEL_SYSTEM_PROMPT,
    BodyDecision,
    MailLabel,
    MailLabelResult,
    cut_for_label,
    decide_body,
    label_content,
    label_mail_body,
    needs_ignored_mail_row,
)

logger = logging.getLogger(__name__)

# The ignored-mail code for a labelled email that made no document.
NO_ORDER_FOUND_CODE = "INT-012"

# The filename a text document carries, by the part it was made from. The
# same names the webhook stores the parts under (email_intake.BODY_FILENAMES).
TEXT_DOCUMENT_FILENAMES = {"text": "E-mail text.txt", "html": "E-mail text.html"}

# Why a text became a document with no label answer (answer 1's log line).
NO_LABEL_CALL_FAILED = "call_failed"
NO_LABEL_HASH_MISMATCH = "hash_mismatch"
NO_LABEL_TEXT_UNREADABLE = "text_unreadable"
NO_LABEL_STUCK_SWEEP = "stuck_sweep"


# ── Which part is read ──────────────────────────────────────────────────────


def visible_characters(value: str) -> int:
    """Characters that show: everything but whitespace."""
    return sum(1 for ch in value if not ch.isspace())


def choose_read_text(text_part: str | None, html_part: str | None) -> tuple[str, str] | None:
    """
    The part DocFlow reads, as `(kind, text)`, from the parse service's text
    of each part (None: the part is absent or couldn't be read). The text
    part, unless it has less than half the HTML part's visible characters
    -- a stub ("view this in an HTML reader") beside a full HTML order would
    otherwise be labelled "not an order" (b1.md question 1). A part that
    couldn't be read loses to one that could. None when neither could.
    """
    if text_part is None and html_part is None:
        return None
    if html_part is None:
        return "text", text_part or ""
    if text_part is None:
        return "html", html_part
    if 2 * visible_characters(text_part) < visible_characters(html_part):
        return "html", html_part
    return "text", text_part


# ── The call ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LabelOutcome:
    """What `call_and_decide` did. `claimed` false: another delivery already
    started a label call for this text, so none was made here."""

    claimed: bool
    started_run_id: UUID | None = None
    result: MailLabelResult | None = None
    decision: BodyDecision | None = None
    text_was_cut: bool = False
    sent_characters: int = 0


Claim = Callable[[int | None], UUID | None]


def call_and_decide(client: Any, full_text: str, *, has_attachment: bool, claim: Claim) -> LabelOutcome:
    """
    Rewrite the sender's fence tags as data, cut the whole text (header lines
    included) at MAIL_LABEL_TEXT_CHAR_BUDGET, count the request's tokens,
    claim the call, make it once, and decide. The rewrite comes first, so a
    tag the cut would split is already `[/document]` and leaves no part-tag
    at the end of what is sent (founder, 2026-11-18).
    `claim(counted)` commits the started row and returns its id, or None when
    the text was claimed already -- then no call is made.

    A text past the budget is sent cut and becomes a document whatever the
    label says (`decide_body`'s `text_cut`): its order lines may be past the
    cut. The caller stores the uncut text; extraction reads that.
    """
    sent, was_cut = cut_for_label(_defang(full_text))
    counted = count_request_tokens(
        client,
        model=ROUTING_MODEL,
        system=MAIL_LABEL_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": label_content(sent)}],
    )
    started_run_id = claim(counted)
    if started_run_id is None:
        return LabelOutcome(claimed=False)
    result = label_mail_body(client, sent)
    decision = decide_body(result.label, text_was_cut=was_cut, has_attachment=has_attachment)
    return LabelOutcome(
        claimed=True,
        started_run_id=started_run_id,
        result=result,
        decision=decision,
        text_was_cut=was_cut,
        sent_characters=len(sent),
    )


# ── The email's row ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EmailText:
    """What the labelling task reads from an email's `raw_emails` row."""

    raw_email_id: UUID
    body_state: str
    storage_path: str | None
    kind: str | None
    sha256: str | None
    html_path: str | None
    subject: str | None
    sender_email: str | None
    date_header: str | None
    message_id: str | None
    has_attachment_document: bool


def load_email_text(session: Session, raw_email_id: UUID, *, lock: bool = False) -> EmailText | None:
    """The email's text columns, None when there is no such email."""
    row = session.execute(
        text(
            f"""
            SELECT r.id, r.body_state, r.body_storage_path, r.body_kind, r.body_sha256,
                   r.body_html_path, r.subject, r.sender_email, r.date_header, r.message_id,
                   EXISTS (SELECT 1 FROM documents d
                            WHERE d.raw_email_id = r.id AND d.email_part = 'attachment'
                              AND d.deleted_at IS NULL) AS has_attachment_document
            FROM raw_emails r WHERE r.id = :id
            {"FOR UPDATE OF r" if lock else ""}
            """
        ),
        {"id": str(raw_email_id)},
    ).mappings().first()
    if row is None:
        return None
    return EmailText(
        raw_email_id=UUID(str(row["id"])),
        body_state=row["body_state"],
        storage_path=row["body_storage_path"],
        kind=row["body_kind"],
        sha256=row["body_sha256"],
        html_path=row["body_html_path"],
        subject=row["subject"],
        sender_email=row["sender_email"],
        date_header=row["date_header"],
        message_id=row["message_id"],
        has_attachment_document=bool(row["has_attachment_document"]),
    )


def label_call_started(session: Session, raw_email_id: UUID) -> bool:
    """Whether a label call was ever started for this email's text."""
    return (
        session.execute(
            text(
                "SELECT 1 FROM extraction_runs WHERE raw_email_id = :id "
                "AND run_kind = :kind AND run_state = 'started' LIMIT 1"
            ),
            {"id": str(raw_email_id), "kind": MAIL_LABEL_RUN_KIND},
        ).first()
        is not None
    )


def claim_label_call(
    session: Session, tenant_id: UUID, raw_email_id: UUID, *, counted_input_tokens: int | None
) -> UUID | None:
    """
    Step 1 of the concurrency rule: under the row's lock, the started row
    (D-163) for one label call -- only while the text is `pending` and no
    call was started before. Returns its id, or None (claimed already, or
    decided). The caller commits this before the call.
    """
    email = load_email_text(session, raw_email_id, lock=True)
    if email is None or email.body_state != "pending" or label_call_started(session, raw_email_id):
        return None
    run_id = uuid4()
    session.execute(
        text(
            """
            INSERT INTO extraction_runs
                (id, tenant_id, document_id, raw_email_id, run_kind, model_id, run_state, succeeded,
                 counted_input_tokens, created_at)
            VALUES (:id, :tenant_id, NULL, :raw_email_id, :run_kind, :model_id, 'started', NULL,
                    :counted, clock_timestamp())
            """
        ),
        {
            "id": str(run_id),
            "tenant_id": str(tenant_id),
            "raw_email_id": str(raw_email_id),
            "run_kind": MAIL_LABEL_RUN_KIND,
            "model_id": ROUTING_MODEL,
            "counted": counted_input_tokens,
        },
    )
    return run_id


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def record_label_outcome(
    session: Session,
    tenant_id: UUID,
    raw_email_id: UUID,
    result: MailLabelResult,
    *,
    started_run_id: UUID,
) -> bool:
    """
    The label call's outcome row: tokens, cost, the answer, pointing at its
    started row. Never a `document_id`: a label run belongs to the email,
    and a document's own cost is its extraction's. One outcome per start
    (0034's unique index): if the stuck sweep already closed this run as
    lost, nothing is written and False is returned, so a call is never
    counted twice.
    """
    inserted = session.execute(
        text(
            """
            INSERT INTO extraction_runs
                (id, tenant_id, document_id, raw_email_id, run_kind, model_id, prompt_hash,
                 schema_version, raw_response, input_tokens, output_tokens, example_input_tokens,
                 est_cost_usd, latency_ms, succeeded, error_code, examples_used, started_run_id,
                 created_at)
            VALUES
                (:id, :tenant_id, NULL, :raw_email_id, :run_kind, :model_id, :prompt_hash,
                 :schema_version, CAST(:raw_response AS jsonb), :input_tokens, :output_tokens, NULL,
                 :est_cost_usd, :latency_ms, :succeeded, :error_code, '[]'::jsonb, :started_run_id,
                 clock_timestamp())
            ON CONFLICT (started_run_id) WHERE started_run_id IS NOT NULL DO NOTHING
            RETURNING id
            """
        ),
        {
            "id": str(uuid4()),
            "tenant_id": str(tenant_id),
            "raw_email_id": str(raw_email_id),
            "run_kind": MAIL_LABEL_RUN_KIND,
            "model_id": result.model_id,
            "prompt_hash": result.prompt_hash,
            "schema_version": result.schema_version,
            "raw_response": json.dumps(result.raw_response),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "est_cost_usd": _money(result.est_cost_usd),
            "latency_ms": result.latency_ms,
            "succeeded": result.ok,
            "error_code": result.error_code,
            "started_run_id": str(started_run_id),
        },
    ).first()
    return inserted is not None


# ── The decision, written ───────────────────────────────────────────────────


@dataclass(frozen=True)
class DocumentPart:
    """The stored part a text document is made from: its Storage object (the
    one the email's row points at -- shared, never copied), its kind, and the
    hash its bytes must have when the parse step reads them."""

    storage_path: str
    kind: str
    sha256: str


@dataclass(frozen=True)
class Settled:
    """What `settle_body` wrote. `settled` false: the text had already left
    `pending` (the stuck sweep, or another delivery), so nothing was."""

    settled: bool
    document_id: UUID | None = None
    ignored_mail_row: bool = False


def settle_body(
    session: Session,
    tenant_id: UUID,
    raw_email_id: UUID,
    *,
    decision: BodyDecision,
    label: MailLabel | None,
    part: DocumentPart | None,
    text_path: str | None,
    no_label_reason: str | None = None,
) -> Settled:
    """
    Step 2 of the concurrency rule, in the caller's transaction (the one that
    also writes the outcome row): lock the row, move the text from `pending`
    to `labelled` with its label (null with no answer), and only then write
    the decision.

    * A document: one `documents` row (`email_part = 'body'`, `pending`, the
      label's `injection_suspected` carried so it is forced to review), the
      same duplicate check as an attachment, the allowance thresholds. The
      insert is `ON CONFLICT ... DO NOTHING` against 0041's one-per-email
      index, then the row is read. The email's outcome becomes `processed`
      when it was `received` or `rejected`.
    * No document: an INT-012 ignored-mail row carrying the label when the
      email made no document, or the label is change or cancellation (D-230
      item 5). A `received` email becomes `rejected`.

    The caller nudges the dispatcher after the commit when a document was made.
    """
    email = load_email_text(session, raw_email_id, lock=True)
    if email is None:
        return Settled(settled=False)
    moved = session.execute(
        text(
            """
            UPDATE raw_emails
               SET body_state = 'labelled',
                   body_label = :label,
                   body_label_confidence = CAST(:label_confidence AS numeric),
                   body_contains_order_lines = :contains_order_lines,
                   body_order_lines_confidence = CAST(:order_lines_confidence AS numeric),
                   body_injection_suspected = :injection_suspected,
                   body_text_path = :text_path,
                   body_labelled_at = now()
             WHERE id = :id AND tenant_id = :tenant_id AND body_state = 'pending'
            RETURNING id
            """
        ),
        {
            "id": str(raw_email_id),
            "tenant_id": str(tenant_id),
            "label": label.label if label else None,
            "label_confidence": str(label.label_confidence) if label else None,
            "contains_order_lines": label.contains_order_lines if label else None,
            "order_lines_confidence": str(label.order_lines_confidence) if label else None,
            "injection_suspected": label.injection_suspected if label else None,
            "text_path": text_path,
        },
    ).first()
    if moved is None:
        logger.info("mail_body_already_decided raw_email_id=%s state=%s", raw_email_id, email.body_state)
        return Settled(settled=False)

    if label is None:
        logger.info(
            "mail_body_document_without_label raw_email_id=%s reason=%s",
            raw_email_id,
            no_label_reason or decision.reason,
        )
    else:
        logger.info(
            "mail_body_labelled raw_email_id=%s label=%s label_confidence=%s "
            "order_lines_confidence=%s decision=%s reason=%s",
            raw_email_id,
            label.label,
            label.label_confidence,
            label.order_lines_confidence,
            "document" if decision.make_document else "no_document",
            decision.reason,
        )

    if decision.make_document:
        if part is None:  # a document is always made from a stored part
            raise ValueError("a text document needs its stored part")
        document_id = _insert_text_document(
            session, tenant_id, email, part, injection_suspected=decision.injection_suspected
        )
        # The label call's cost is on the text document from the start
        # (answer 3); the outcome row is already written in this transaction.
        model_runs.set_document_cost(session, document_id)
        session.execute(
            text(
                "UPDATE raw_emails SET outcome = 'processed' "
                "WHERE id = :id AND outcome IN ('received', 'rejected')"
            ),
            {"id": str(raw_email_id)},
        )
        allowance.record_thresholds(session, tenant_id)
        return Settled(settled=True, document_id=document_id)

    row_written = needs_ignored_mail_row(label, email_made_a_document=email.has_attachment_document)
    if row_written:
        session.execute(
            text(
                """
                INSERT INTO intake_rejections
                    (id, tenant_id, source, original_filename, detected_type, error_code,
                     sender_email, subject, message_id, raw_email_id, mail_label, created_at)
                VALUES
                    (:id, :tenant_id, 'email', NULL, NULL, :code,
                     :sender_email, :subject, :message_id, :raw_email_id, :mail_label, now())
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": str(tenant_id),
                "code": NO_ORDER_FOUND_CODE,
                "sender_email": email.sender_email,
                "subject": email.subject,
                "message_id": email.message_id,
                "raw_email_id": str(raw_email_id),
                "mail_label": label.label if label else None,
            },
        )
    session.execute(
        text("UPDATE raw_emails SET outcome = 'rejected' WHERE id = :id AND outcome = 'received'"),
        {"id": str(raw_email_id)},
    )
    return Settled(settled=True, ignored_mail_row=row_written)


def _insert_text_document(
    session: Session,
    tenant_id: UUID,
    email: EmailText,
    part: DocumentPart,
    *,
    injection_suspected: bool,
) -> UUID:
    existing = find_content_duplicate_at_ingest(session, tenant_id, part.sha256)
    session.execute(
        text(
            """
            INSERT INTO documents
                (id, tenant_id, original_filename, storage_path, source, status, content_sha256,
                 sender_email, message_id, is_possible_duplicate, duplicate_of_document_id,
                 injection_suspected, raw_email_id, email_part, created_at)
            VALUES
                (:id, :tenant_id, :original_filename, :storage_path, 'email', 'pending', :content_sha256,
                 :sender_email, :message_id, :is_possible_duplicate, :duplicate_of_document_id,
                 :injection_suspected, :raw_email_id, 'body', now())
            ON CONFLICT (raw_email_id) WHERE email_part = 'body' DO NOTHING
            """
        ),
        {
            "id": str(uuid4()),
            "tenant_id": str(tenant_id),
            "original_filename": TEXT_DOCUMENT_FILENAMES[part.kind],
            "storage_path": part.storage_path,
            "content_sha256": part.sha256,
            "sender_email": email.sender_email,
            "message_id": email.message_id,
            "is_possible_duplicate": existing is not None,
            "duplicate_of_document_id": str(existing) if existing else None,
            "injection_suspected": injection_suspected,
            "raw_email_id": str(email.raw_email_id),
        },
    )
    # One text document per email (0041): an earlier one stands, never a second.
    document_id = session.execute(
        text("SELECT id FROM documents WHERE raw_email_id = :id AND email_part = 'body'"),
        {"id": str(email.raw_email_id)},
    ).scalar_one()
    return UUID(str(document_id))
