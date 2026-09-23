"""
Email intake pipeline (CLAUDE.md Section 7.2, 7.8, 7.16.3, 7.16.4). The
webhook route (apps/api/app/routers/email_intake.py) is a thin adapter: it
turns an HTTP request into a call into this module and translates the
result to an HTTP response. All parsing and abuse-defense *decision* logic
lives here so it's independently unit-testable and so the route never
re-implements a rule inline, per this slice's own instructions.

Provider choice (see DECISIONS.md): Postmark's inbound webhook. Its
payload shape is isolated behind `parse_postmark_payload` -- everything
below that boundary (`ParsedEmail`, `AuthResult`, `evaluate_email`, the DB
helpers, `process_inbound_email`) is provider-agnostic, so swapping
providers later is a change to one function, not to the abuse-defense logic.

Every document this pipeline creates has `source = 'email'`. Accepted
attachments go through the exact same validate -> store -> create
`documents` row -> enqueue `docflow.parse_and_extract` flow as
apps/api/app/routers/documents.py's upload endpoint (Section 7.11: "one
module defines the allowlist... the upload endpoint, email intake, and the
Console's staging upload all read from it").

tenant_id is never trusted from the payload -- it is resolved exclusively
from the URL's intake token via `resolve_tenant_by_token` (see
docflow_core.db.token_lookup_session), before any other processing happens
(CLAUDE.md Section 7.5 / Section 10).
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from celery import Celery
from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import file_types
from docflow_core.config import get_settings

# Named constants (Section 7.15.4) are defined once, in docflow_core.constants.
from docflow_core.constants import MAX_ATTACHMENTS_PER_EMAIL, UNKNOWN_SENDER_HOURLY_LIMIT
from docflow_core.db import tenant_session, token_lookup_session
from docflow_core.duplicates import find_content_duplicate_at_ingest
from docflow_core.storage import save_file

# A lightweight Celery producer, separate from apps/api/app/celery_client.py
# and apps/worker/app/celery_app.py's instances but pointed at the same
# broker and (via an explicit `queue=` on every send_task call) the same
# named queues -- mirrors apps/api/app/celery_client.py's own shape. A
# module-level object so tests can monkeypatch
# `docflow_core.email_intake.celery_client` exactly as
# apps/api/tests/test_documents_upload.py already does for the upload router.
_settings = get_settings()
celery_client = Celery("docflow_email_intake", broker=_settings.redis_url, backend=_settings.redis_url)


class MalformedPayloadError(Exception):
    """The inbound webhook body doesn't have the shape this pipeline needs."""


@dataclass(frozen=True)
class ParsedAttachment:
    filename: str
    content: bytes
    content_type: str


@dataclass(frozen=True)
class ParsedEmail:
    sender_email: str
    sender_name: str | None
    subject: str | None
    message_id: str | None
    headers: list[dict]
    attachments: list[ParsedAttachment] = field(default_factory=list)


@dataclass(frozen=True)
class AuthResult:
    spf: str | None
    dkim: str | None
    dmarc: str | None


@dataclass(frozen=True)
class EmailDecision:
    """Pure decision output of `evaluate_email` -- no DB, no I/O."""

    reject_no_attachment: bool
    quarantine_reason: str | None


@dataclass(frozen=True)
class AttachmentOutcome:
    filename: str
    accepted: bool
    document_id: UUID | None
    error_code: str | None
    possible_duplicate_of: UUID | None


@dataclass(frozen=True)
class ProcessResult:
    outcome: str  # "processed" | "quarantined" | "rejected" | "duplicate"
    raw_email_id: UUID | None
    attachments: list[AttachmentOutcome]


# ── 1. Postmark payload parsing boundary ───────────────────────────────────

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _extract_email_from_display(value: str) -> str:
    match = _EMAIL_RE.search(value or "")
    return match.group(0).lower() if match else ""


def parse_postmark_payload(payload: dict) -> ParsedEmail:
    """
    Parses Postmark's inbound-webhook JSON shape into the provider-agnostic
    `ParsedEmail`. Raises `MalformedPayloadError` for anything that doesn't
    have the minimum shape this pipeline needs -- the route turns that into
    a 400, never a 500 or a silent partial parse.
    """
    if not isinstance(payload, dict):
        raise MalformedPayloadError("Payload must be a JSON object.")

    from_full = payload.get("FromFull") or {}
    if not isinstance(from_full, dict):
        from_full = {}
    sender_email = str(from_full.get("Email") or "").strip().lower()
    if not sender_email:
        sender_email = _extract_email_from_display(str(payload.get("From") or ""))
    if not sender_email:
        raise MalformedPayloadError("Payload has no resolvable sender address (From/FromFull.Email).")

    headers_raw = payload.get("Headers")
    if headers_raw is None:
        headers_raw = []
    if not isinstance(headers_raw, list):
        raise MalformedPayloadError("Headers must be a list.")

    attachments_raw = payload.get("Attachments")
    if attachments_raw is None:
        attachments_raw = []
    if not isinstance(attachments_raw, list):
        raise MalformedPayloadError("Attachments must be a list.")

    attachments: list[ParsedAttachment] = []
    for entry in attachments_raw:
        if not isinstance(entry, dict):
            raise MalformedPayloadError("Each attachment entry must be a JSON object.")
        name = str(entry.get("Name") or "attachment")
        content_b64 = entry.get("Content") or ""
        try:
            content = base64.b64decode(content_b64, validate=True)
        except Exception as exc:
            raise MalformedPayloadError(f"Attachment {name!r} has invalid base64 content.") from exc
        attachments.append(
            ParsedAttachment(filename=name, content=content, content_type=str(entry.get("ContentType") or ""))
        )

    return ParsedEmail(
        sender_email=sender_email,
        sender_name=from_full.get("Name"),
        subject=payload.get("Subject"),
        message_id=payload.get("MessageID"),
        headers=headers_raw,
        attachments=attachments,
    )


def sender_domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


# ── 4. SPF/DKIM/DMARC ───────────────────────────────────────────────────────

_AUTH_RESULTS_HEADER_NAME = "authentication-results"
_SPF_RE = re.compile(r"\bspf=(\w+)", re.IGNORECASE)
_DKIM_RE = re.compile(r"\bdkim=(\w+)", re.IGNORECASE)
_DMARC_RE = re.compile(r"\bdmarc=(\w+)", re.IGNORECASE)


def _first_match(pattern: re.Pattern, value: str) -> str | None:
    match = pattern.search(value)
    return match.group(1).lower() if match else None


def parse_authentication_results(headers: list[dict]) -> AuthResult:
    """
    Tolerant parse of the `Authentication-Results` header (CLAUDE.md Section
    7.16.3): "extract the three verdict keywords via regex, don't attempt a
    full RFC 8601 parser." Concatenates every Authentication-Results header
    present (a message can legitimately carry more than one, e.g. added by
    different hops) so a verdict anywhere in any of them is found.
    """
    combined = " ".join(
        str(h.get("Value") or "")
        for h in headers
        if str(h.get("Name") or "").strip().lower() == _AUTH_RESULTS_HEADER_NAME
    )
    return AuthResult(
        spf=_first_match(_SPF_RE, combined),
        dkim=_first_match(_DKIM_RE, combined),
        dmarc=_first_match(_DMARC_RE, combined),
    )


def should_quarantine_for_auth(auth: AuthResult) -> bool:
    """
    CLAUDE.md Section 7.16.3: "A DMARC fail, or an SPF fail specifically
    from a domain that has a DMARC policy... -> quarantined." This pipeline
    has no independent DNS lookup of the sending domain's real DMARC policy
    (see DECISIONS.md for the exact heuristic): the presence of a `dmarc=`
    verdict at all in Authentication-Results is treated as evidence the
    domain has a DMARC policy the receiving mail server evaluated. If no
    `dmarc=` verdict is present, an SPF fail alone does NOT trigger
    quarantine, matching the "none/neutral results are processed normally"
    spirit for domains DocFlow has no DMARC signal for.
    """
    if auth.dmarc == "fail":
        return True
    if auth.spf == "fail" and auth.dmarc is not None:
        return True
    return False


# ── 5. Unknown-sender velocity (DB-touching; provider-agnostic) ────────────


def is_known_sender(session: Session, tenant_id: UUID, sender_email: str) -> bool:
    """
    CLAUDE.md Section 7.16.3: "A sender is known if its address or domain
    matches an existing buyer or a prior approved document for this
    tenant." No buyer/customer records exist yet (Phase 2) -- see
    DECISIONS.md for this slice's scoping to "has a prior non-quarantined
    document for this tenant" only.
    """
    row = session.execute(
        text(
            "SELECT 1 FROM documents WHERE tenant_id = :tenant_id AND sender_email = :sender_email "
            "AND status != 'quarantined' LIMIT 1"
        ),
        {"tenant_id": str(tenant_id), "sender_email": sender_email},
    ).first()
    return row is not None


def count_unknown_sender_documents_last_hour(session: Session, tenant_id: UUID) -> int:
    """
    Counts documents created in the trailing rolling hour, for this tenant,
    whose sender had no earlier non-quarantined document (i.e. was unknown
    at the moment that document was created). See DECISIONS.md for why this
    exact self-join shape was chosen over a simpler "count all email
    documents in the last hour" query.
    """
    row = session.execute(
        text(
            """
            SELECT count(*) AS c
            FROM documents d
            WHERE d.tenant_id = :tenant_id
              AND d.source = 'email'
              AND d.sender_email IS NOT NULL
              AND d.created_at > now() - interval '1 hour'
              AND NOT EXISTS (
                  SELECT 1 FROM documents d2
                  WHERE d2.tenant_id = d.tenant_id
                    AND d2.sender_email = d.sender_email
                    AND d2.status != 'quarantined'
                    AND d2.created_at < d.created_at
              )
            """
        ),
        {"tenant_id": str(tenant_id)},
    ).mappings().first()
    return int(row["c"]) if row else 0


def evaluate_email(
    num_attachments: int,
    auth: AuthResult,
    sender_known: bool,
    unknown_sender_count_last_hour: int,
) -> EmailDecision:
    """
    Pure decision function -- no DB access -- so the abuse-defense ordering
    from CLAUDE.md Section 7.16.3 (attachment cap, then auth, then unknown-
    sender velocity) is independently unit-testable without a database.
    """
    if num_attachments == 0:
        return EmailDecision(reject_no_attachment=True, quarantine_reason=None)
    if num_attachments > MAX_ATTACHMENTS_PER_EMAIL:
        return EmailDecision(reject_no_attachment=False, quarantine_reason="attachment_cap")
    if should_quarantine_for_auth(auth):
        return EmailDecision(reject_no_attachment=False, quarantine_reason="auth_fail")
    if not sender_known and unknown_sender_count_last_hour >= UNKNOWN_SENDER_HOURLY_LIMIT:
        return EmailDecision(reject_no_attachment=False, quarantine_reason="unknown_sender_velocity")
    return EmailDecision(reject_no_attachment=False, quarantine_reason=None)


# ── Tenant resolution from the intake token ─────────────────────────────────


def resolve_tenant_by_token(token: str) -> tuple[UUID, str] | None:
    """
    Returns (tenant_id, intake_addresses.status) for this token, or None if
    no such token exists. Per CLAUDE.md Section 7.15.1's "unreachable, not
    just hidden" principle (an intake token is exactly as sensitive as a
    platform-admin route from an outside prober's perspective): the caller
    must respond identically -- a generic 404 -- whether the token never
    existed or exists but isn't active, never revealing which.
    """
    with token_lookup_session(token) as session:
        row = session.execute(
            text("SELECT tenant_id, status FROM intake_addresses WHERE token = :token"),
            {"token": token},
        ).mappings().first()
    if row is None:
        return None
    return UUID(str(row["tenant_id"])), row["status"]


# ── Raw-email forensic record ───────────────────────────────────────────────


def _insert_raw_email(
    session: Session,
    tenant_id: UUID,
    parsed: ParsedEmail,
    auth: AuthResult,
    sender_domain: str,
    *,
    outcome: str,
    attachment_count: int,
) -> UUID:
    raw_email_id = uuid4()
    session.execute(
        text(
            """
            INSERT INTO raw_emails
                (id, tenant_id, message_id, sender_email, sender_domain, subject,
                 spf_result, dkim_result, dmarc_result, raw_headers, attachment_count,
                 outcome, created_at)
            VALUES
                (:id, :tenant_id, :message_id, :sender_email, :sender_domain, :subject,
                 :spf_result, :dkim_result, :dmarc_result, :raw_headers, :attachment_count,
                 :outcome, now())
            """
        ),
        {
            "id": str(raw_email_id),
            "tenant_id": str(tenant_id),
            "message_id": parsed.message_id,
            "sender_email": parsed.sender_email,
            "sender_domain": sender_domain,
            "subject": parsed.subject,
            "spf_result": auth.spf,
            "dkim_result": auth.dkim,
            "dmarc_result": auth.dmarc,
            "raw_headers": parsed.headers,
            "attachment_count": attachment_count,
            "outcome": outcome,
        },
    )
    return raw_email_id


def _insert_intake_rejection(
    session: Session,
    tenant_id: UUID,
    parsed: ParsedEmail,
    *,
    original_filename: str | None,
    detected_type: str | None,
    error_code: str,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO intake_rejections
                (id, tenant_id, source, original_filename, detected_type, error_code,
                 sender_email, subject, message_id, created_at)
            VALUES
                (:id, :tenant_id, 'email', :original_filename, :detected_type, :error_code,
                 :sender_email, :subject, :message_id, now())
            """
        ),
        {
            "id": str(uuid4()),
            "tenant_id": str(tenant_id),
            "original_filename": original_filename,
            "detected_type": detected_type,
            "error_code": error_code,
            "sender_email": parsed.sender_email,
            "subject": parsed.subject,
            "message_id": parsed.message_id,
        },
    )


def _reply_not_active(session: Session, tenant_id: UUID, tenant_name: str, sender: str | None) -> None:
    """The not-yet-active auto-reply. See `_reply_intake_blocked`."""
    _reply_intake_blocked(
        session, tenant_id, tenant_name, sender, error_code="INT-005", template="intake_not_active"
    )


def _reply_suspended(session: Session, tenant_id: UUID, tenant_name: str, sender: str | None) -> None:
    """The account-no-longer-active auto-reply (Section 7.14: 'the inbound
    email auto-replies with a clear "this account is no longer active"
    message'). See `_reply_intake_blocked`."""
    _reply_intake_blocked(
        session, tenant_id, tenant_name, sender, error_code="INT-006", template="intake_suspended"
    )


def _reply_intake_blocked(
    session: Session,
    tenant_id: UUID,
    tenant_name: str,
    sender: str | None,
    *,
    error_code: str,
    template: str,
) -> None:
    """Auto-reply for mail this tenant's address cannot accept right now, at
    most once a day per sender, so a mail loop or a busy buyer never turns
    this address into a spam cannon."""
    if not sender:
        return
    from docflow_core import email_outbox

    earlier = session.execute(
        text(
            "SELECT count(*) FROM intake_rejections WHERE error_code = :code "
            "AND lower(sender_email) = lower(:sender) AND created_at > now() - interval '1 day'"
        ),
        {"code": error_code, "sender": sender},
    ).scalar_one()
    if earlier > 1:  # the rejection just written is one of them
        return
    email_outbox.enqueue(
        session,
        tenant_id=tenant_id,
        to_address=sender,
        template=template,
        params={"tenant_name": tenant_name},
        related_type="intake_rejection",
    )


def _insert_quarantined_document(
    session: Session,
    tenant_id: UUID,
    parsed: ParsedEmail,
    attachment: ParsedAttachment,
    *,
    quarantine_reason: str,
) -> UUID:
    storage_path = save_file(tenant_id, attachment.filename, attachment.content)
    content_sha256 = hashlib.sha256(attachment.content).hexdigest()
    document_id = uuid4()
    session.execute(
        text(
            """
            INSERT INTO documents
                (id, tenant_id, original_filename, storage_path, source, status,
                 content_sha256, sender_email, message_id, quarantine_reason, quarantined_at,
                 created_at)
            VALUES
                (:id, :tenant_id, :original_filename, :storage_path, 'email', 'quarantined',
                 :content_sha256, :sender_email, :message_id, :quarantine_reason, now(),
                 now())
            """
        ),
        {
            "id": str(document_id),
            "tenant_id": str(tenant_id),
            "original_filename": attachment.filename,
            "storage_path": storage_path,
            "content_sha256": content_sha256,
            "sender_email": parsed.sender_email,
            "message_id": parsed.message_id,
            "quarantine_reason": quarantine_reason,
        },
    )
    return document_id


def _accept_attachment(
    session: Session,
    tenant_id: UUID,
    parsed: ParsedEmail,
    attachment: ParsedAttachment,
) -> AttachmentOutcome:
    """
    Runs one attachment through the exact same validate -> store -> create
    `documents` row flow as apps/api/app/routers/documents.py's upload
    endpoint. Does not enqueue -- callers enqueue only after the enclosing
    transaction commits (see `process_inbound_email`).
    """
    validation = file_types.validate_upload(attachment.content, attachment.filename)
    if not validation.ok:
        _insert_intake_rejection(
            session,
            tenant_id,
            parsed,
            original_filename=attachment.filename,
            detected_type=validation.file_type.name.value if validation.file_type else None,
            error_code=validation.error_code,
        )
        return AttachmentOutcome(
            filename=attachment.filename,
            accepted=False,
            document_id=None,
            error_code=validation.error_code,
            possible_duplicate_of=None,
        )

    content_sha256 = hashlib.sha256(attachment.content).hexdigest()
    # CLAUDE.md Section 7.8, through the same one function the upload endpoint
    # calls, so the two intake paths cannot disagree about what a duplicate
    # is. The link is stored on the new row, not merely returned (D-076).
    existing = find_content_duplicate_at_ingest(session, tenant_id, content_sha256)

    storage_path = save_file(tenant_id, attachment.filename, attachment.content)
    document_id = uuid4()
    session.execute(
        text(
            """
            INSERT INTO documents
                (id, tenant_id, original_filename, storage_path, source, status,
                 content_sha256, sender_email, message_id, is_possible_duplicate,
                 duplicate_of_document_id, created_at)
            VALUES
                (:id, :tenant_id, :original_filename, :storage_path, 'email', 'pending',
                 :content_sha256, :sender_email, :message_id, :is_possible_duplicate,
                 :duplicate_of_document_id, now())
            """
        ),
        {
            "id": str(document_id),
            "tenant_id": str(tenant_id),
            "original_filename": attachment.filename,
            "storage_path": storage_path,
            "content_sha256": content_sha256,
            "sender_email": parsed.sender_email,
            "message_id": parsed.message_id,
            "is_possible_duplicate": existing is not None,
            "duplicate_of_document_id": str(existing) if existing else None,
        },
    )
    return AttachmentOutcome(
        filename=attachment.filename,
        accepted=True,
        document_id=document_id,
        error_code=None,
        possible_duplicate_of=existing,
    )


def process_inbound_email(tenant_id: UUID, parsed: ParsedEmail) -> ProcessResult:
    """
    The full CLAUDE.md Section 7.16.3 pipeline for one inbound email, given
    an already-resolved `tenant_id`. Opens its own `tenant_session()` so
    every write in this function is scoped by RLS (CLAUDE.md Section 7.5),
    exactly as `apps/api/app/routers/documents.py` does for uploads.

    Message-ID dedupe runs first, before any other check, so a mail
    provider's retried webhook delivery never produces a second
    `raw_emails` row, a second quarantine decision, or a second enqueue --
    see DECISIONS.md for why this differs from the numbered order in the
    slice's own spec (which lists dedupe after the abuse-defense checks).
    """
    auth = parse_authentication_results(parsed.headers)
    sender_domain = sender_domain_of(parsed.sender_email)

    pending_enqueues: list[UUID] = []
    result_outcome: str
    raw_email_id: UUID
    attachment_outcomes: list[AttachmentOutcome] = []

    with tenant_session(tenant_id) as session:
        if parsed.message_id:
            duplicate = session.execute(
                text("SELECT id FROM raw_emails WHERE tenant_id = :tenant_id AND message_id = :message_id"),
                {"tenant_id": str(tenant_id), "message_id": parsed.message_id},
            ).mappings().first()
            if duplicate is not None:
                return ProcessResult(
                    outcome="duplicate", raw_email_id=UUID(str(duplicate["id"])), attachments=[]
                )

        # Section 7.15.2 Step 2: the intake address "exists from this moment but
        # is not live: until go-live (Step 9) it auto-replies 'this address is
        # not yet active' and processes nothing" (D-114). Logged, never read.
        # Section 7.14: a suspended/pending-deletion tenant's address is
        # blocked the same way, but with its own "no longer active" wording
        # (D-123) -- intake_address_active is the one flag both states set
        # false, so the tenant's current lifecycle status picks the reply.
        tenant = session.execute(
            text("SELECT name, status, intake_address_active FROM tenants WHERE id = :id"),
            {"id": str(tenant_id)},
        ).mappings().first()
        if tenant is not None and not tenant["intake_address_active"]:
            suspended = tenant["status"] in ("suspended", "pending_deletion")
            error_code = "INT-006" if suspended else "INT-005"
            _insert_intake_rejection(
                session, tenant_id, parsed, original_filename=None, detected_type=None, error_code=error_code
            )
            raw_email_id = _insert_raw_email(
                session, tenant_id, parsed, auth, sender_domain,
                outcome="rejected", attachment_count=len(parsed.attachments),
            )
            if suspended:
                _reply_suspended(session, tenant_id, tenant["name"], parsed.sender_email)
            else:
                _reply_not_active(session, tenant_id, tenant["name"], parsed.sender_email)
            return ProcessResult(outcome="rejected", raw_email_id=raw_email_id, attachments=[])

        num_attachments = len(parsed.attachments)

        if num_attachments == 0:
            _insert_intake_rejection(
                session, tenant_id, parsed, original_filename=None, detected_type=None, error_code="INT-001"
            )
            raw_email_id = _insert_raw_email(
                session, tenant_id, parsed, auth, sender_domain, outcome="rejected", attachment_count=0
            )
            return ProcessResult(outcome="rejected", raw_email_id=raw_email_id, attachments=[])

        sender_known = is_known_sender(session, tenant_id, parsed.sender_email)
        unknown_sender_count = (
            0 if sender_known else count_unknown_sender_documents_last_hour(session, tenant_id)
        )
        decision = evaluate_email(num_attachments, auth, sender_known, unknown_sender_count)

        if decision.quarantine_reason:
            for attachment in parsed.attachments:
                document_id = _insert_quarantined_document(
                    session, tenant_id, parsed, attachment, quarantine_reason=decision.quarantine_reason
                )
                attachment_outcomes.append(
                    AttachmentOutcome(
                        filename=attachment.filename,
                        accepted=False,
                        document_id=document_id,
                        error_code=None,
                        possible_duplicate_of=None,
                    )
                )
            result_outcome = "quarantined"
        else:
            any_accepted = False
            for attachment in parsed.attachments:
                outcome = _accept_attachment(session, tenant_id, parsed, attachment)
                attachment_outcomes.append(outcome)
                if outcome.accepted:
                    any_accepted = True
                    pending_enqueues.append(outcome.document_id)
            result_outcome = "processed" if any_accepted else "rejected"

        raw_email_id = _insert_raw_email(
            session,
            tenant_id,
            parsed,
            auth,
            sender_domain,
            outcome=result_outcome,
            attachment_count=num_attachments,
        )

    # Enqueued only once the transaction above has committed (tenant_session's
    # __exit__), so a task never races a document row that isn't visible yet
    # -- mirrors apps/api/app/routers/documents.py's upload endpoint exactly.
    for document_id in pending_enqueues:
        celery_client.send_task(
            "docflow.parse_and_extract", args=[str(tenant_id), str(document_id)], queue="interactive"
        )

    return ProcessResult(outcome=result_outcome, raw_email_id=raw_email_id, attachments=attachment_outcomes)
