"""
Founder alerts (CLAUDE.md Section 7.9 / 7.15.3; DECISIONS.md D-104).

"One alert, one row, two channels. Every alert condition writes a
founder_alerts row first; the email is sent from that row, and the Console's
attention panel reads the same row. Never emit an alert that exists only in
an email or only on a screen."

`raise_alert` is the only writer. It writes the email-outbox row and the
alert row together, in one savepoint, and the alert names its email -- so
the two channels cannot disagree. A `dedupe_key` collapses a condition that
keeps firing (a document stuck for three hours) into one open alert; once
the founder acknowledges it, the next occurrence raises a new one.

Payloads carry ids, counts and codes only -- never document values or
customer data (Section 7.10), because the payload is emailed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docflow_core import email_outbox
from docflow_core.config import get_settings
from docflow_core.errors import get_error

logger = logging.getLogger(__name__)

# Every alert type the system raises, with the title the founder reads.
# Section 7.15.3 lists the MVP set; each is added here by the slice that
# starts raising it, so this table is also the inventory of what is wired.
ALERT_TYPES: dict[str, str] = {
    "export_integrity_failure": "An export failed its integrity check",
    "first_week_checkin": "First-week check-in due",
    "scheduled_job_failed": "A scheduled job failed after retries",
    "stripe_subscription_past_due": "A tenant's Stripe subscription is past due",
    "tenant_entered_pending_deletion": "A tenant entered the pending-deletion window",
    "tenant_ready_to_delete": "A tenant's deletion window has elapsed",
    "stripe_cancel_failed": "Cancelling a tenant's Stripe subscription failed",
    "exports_not_finishing": "A tenant's exports keep stopping before they finish",
    "reactivation_invoices_to_review": (
        "A reactivated tenant's old subscription has invoices to collect, void or refund"
    ),
    # Card billing (founder, 2026-09-29; migration 0031's record_stripe_card_event)
    "setup_fee_already_paid": "A tenant paid its setup fee a second time",
    "setup_fee_amount_mismatch": "A setup-fee payment didn't match the tenant's fee",
    # Slice 5.7 (Section 7.16)
    "allowance_reached": "A tenant used its whole monthly allowance",
    "abuse_ceiling_tripped": "A tenant hit the abuse ceiling; new documents are held",
    "cost_breaker_tripped": "A tenant hit the daily AI-cost ceiling; new documents are held",
    "quarantine_ttl_elapsed": "Held documents are past their retention period",
    # D-145: failures whose catalog message tells the reader "DocFlow has been
    # alerted". Until these existed, nothing did.
    "document_failed": "A document couldn't be read",
    "unsafe_file_refused": "A file was refused as unsafe",
    "unverified_sender_held": "Mail from a sender that failed authentication is held",
    # Phase 5.5 Stage 1b (Section 7.9, D-158).
    "document_stuck": "A document was stuck in processing",
    "pipeline_step_failed": "An automatic step didn't finish on a document",
    # Phase 5.5 Stage 2c (D-171): the inbound webhook refused a request.
    "intake_webhook_refused": "The inbound email webhook refused a request",
    # D-176: a Stripe event stamped beyond the clock tolerance; not applied.
    "stripe_event_future_dated": "A Stripe event was stamped in the future and was not applied",
    # D-177: the Console accepts password-only sessions until this is fixed.
    "console_mfa_enforcement_off": "Console MFA enforcement is off (CONSOLE_MFA_ENFORCED=false)",
    # Stage 3b (Q3): Storage couldn't be reached. Once an hour for the whole
    # platform, not per tenant: one outage hits every tenant at once.
    "storage_unavailable": "File storage couldn't be reached",
    # Stage 3b (founder, 2026-09-30): a stored path named another tenant's
    # folder and was refused before any read (Section 7.5).
    "storage_path_cross_tenant": "A stored file path named another tenant's folder and was refused",
    # Stage 3c (founder, Q4): the parse service couldn't be reached, was busy
    # past the worker's patience, or refused the worker's token. Once an hour
    # for the whole platform, like storage_unavailable.
    "parse_service_unavailable": "The parse service couldn't be reached",
    # Stage 3c (founder, 2026-10-01, departure #1): a parse job killed by the
    # seccomp filter (SIGSYS). Every one alerts, never rate-limited: the
    # filter kills only a call from a foreign architecture, which no parser
    # makes by accident.
    "parse_seccomp_kill": "A parse job was killed by the seccomp filter",
}

# Why a seccomp kill's alert has no syscall number (founder: say so when it
# can't be had). The filter answers every listed call with EPERM; only a call
# from a foreign architecture (x32 or 32-bit) gets KILL_PROCESS, which ends
# the process without telling anyone which call it was -- the parent sees
# only SIGSYS, and the number goes to the kernel's audit log on the machine,
# which the parse service can't read.
SECCOMP_KILL_CAUSE = "crashed:signal_31"
SECCOMP_SYSCALL_UNAVAILABLE = (
    "not available: the filter kills a call from a foreign architecture (x32 or 32-bit) without "
    "reporting which call; only the kernel's audit log on the parse machine has it"
)

# Failure codes whose catalog text promises the reader that DocFlow has been
# alerted, and the alert that keeps the promise (D-145). The catalog is what a
# customer reads, so a promise there with no alert behind it is a false
# statement to them. `tests/test_founder_alerts.py` fails the build when a
# catalog entry makes the promise and is neither here nor alerted elsewhere.
FAILURE_ALERTS: dict[str, str] = {
    "DOC-008": "document_failed",
    "DOC-009": "document_failed",
    "DOC-017": "document_failed",
    "DOC-020": "document_failed",
    "DOC-021": "document_failed",
    "DOC-022": "document_stuck",
    # Stage 3b: the stored original is missing or its hash doesn't match.
    "DOC-026": "document_failed",
    "DOC-015": "unsafe_file_refused",
    "INT-004": "unverified_sender_held",
}

SEVERITIES = ("info", "warning", "high", "critical")


def raise_alert(
    session: Session,
    *,
    alert_type: str,
    severity: str,
    tenant_id: UUID | None,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    dedupe_per_utc_day: bool = False,
    dedupe_per_utc_hour: bool = False,
) -> bool:
    """
    Raise an alert. Returns False when an open alert with the same dedupe key
    already exists (nothing is written). Works in a tenant session for that
    tenant (0011's `tenant_raise` policy) or a platform session.

    `dedupe_per_utc_day` makes it one alert per condition per day: the INSERT
    appends ":YYYY-MM-DD" to the key, the UTC date by the database's clock --
    the clock that stamps the rows the key dedupes, so the day can't disagree
    with them near midnight (D-170 #7). `dedupe_per_utc_hour` does the same
    per UTC hour (":YYYY-MM-DDTHH").
    """
    if dedupe_per_utc_day and dedupe_per_utc_hour:
        raise ValueError("choose one dedupe window")
    if alert_type not in ALERT_TYPES:
        raise ValueError(f"unknown alert type {alert_type!r}")
    if severity not in SEVERITIES:
        raise ValueError(f"unknown severity {severity!r}")
    payload = payload or {}
    alert_id = uuid4()
    settings = get_settings()

    savepoint = session.begin_nested()
    outbox_id = None
    if settings.founder_alert_email:
        outbox_id = email_outbox.enqueue(
            session,
            tenant_id=tenant_id,
            to_address=settings.founder_alert_email,
            template="founder_alert",
            params={
                "severity": severity,
                "title": ALERT_TYPES[alert_type],
                "tenant": str(tenant_id) if tenant_id else "(none)",
                "raised_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "alert_id": str(alert_id),
                "details": "\n".join(f"  {k}: {v}" for k, v in sorted(payload.items()))
                or "  (none)",
                "console_link": f"{settings.app_base_url}/admin",
            },
            related_type="founder_alert",
            related_id=alert_id,
        )
    # A plain INSERT, deliberately not `ON CONFLICT DO NOTHING`: Postgres
    # checks an ON CONFLICT insert against the table's SELECT policies too,
    # and a tenant session has none here (it may raise, never read) -- so the
    # tenant-session path would always be refused. The partial unique index
    # on open dedupe keys does the deduplicating instead.
    try:
        session.execute(
            text(
                """
                INSERT INTO founder_alerts
                    (id, type, severity, tenant_id, payload, dedupe_key, email_outbox_id)
                VALUES
                    (:id, :type, :severity, :tenant_id, CAST(:payload AS jsonb),
                     CASE WHEN :per_day
                          THEN CAST(:dedupe_key AS text) || ':' || (now() AT TIME ZONE 'UTC')::date::text
                          WHEN :per_hour
                          THEN CAST(:dedupe_key AS text) || ':'
                               || to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24')
                          ELSE CAST(:dedupe_key AS text) END,
                     :email_outbox_id)
                """
            ),
            {
                "id": str(alert_id),
                "type": alert_type,
                "severity": severity,
                "tenant_id": str(tenant_id) if tenant_id else None,
                "payload": json.dumps(payload, sort_keys=True),
                "dedupe_key": dedupe_key,
                "per_day": dedupe_per_utc_day,
                "per_hour": dedupe_per_utc_hour,
                "email_outbox_id": str(outbox_id) if outbox_id else None,
            },
        )
    except IntegrityError as exc:
        savepoint.rollback()  # discards the email written with it
        if _is_open_duplicate(exc):
            return False
        raise
    savepoint.commit()
    return True


_OPEN_DEDUPE_INDEX = "idx_founder_alerts_open_dedupe"


def _is_open_duplicate(exc: IntegrityError) -> bool:
    """True only for the dedupe index -- any other integrity failure is a bug
    and must surface, not be mistaken for 'already open'."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == _OPEN_DEDUPE_INDEX


def raise_for_failure(
    session: Session,
    *,
    tenant_id: UUID,
    error_code: str | None,
    document_id: UUID | None = None,
    cause: str | None = None,
    detail: dict[str, Any] | None = None,
) -> bool:
    """
    Raise the alert a failure's catalog message promises, if it promises one
    (D-145). Returns False when the code promises nothing, or when an open
    alert for the same tenant, code (and cause, when given) and day already
    exists: a flood of broken files is one alert, not a thousand.

    `cause` splits one code's alerts by why it happened -- DOC-022's
    `timeout` against `worker_stopped` (Stage 3a) -- so the first cause of
    the day can't hide the other. `detail` adds numbers to the payload.

    The payload is codes, ids and numbers only (Section 7.10: it is emailed).
    """
    alert_type = FAILURE_ALERTS.get(error_code or "")
    if alert_type is None:
        return False
    entry = get_error(error_code or "")
    payload: dict[str, Any] = {"error_code": entry.code}
    if document_id is not None:
        payload["first_document_id"] = str(document_id)
    if cause is not None:
        payload["cause"] = cause
    if detail:
        payload.update(detail)
    # Its own savepoint: the caller is recording the failure itself (a
    # refusal, a hold), and an alert that can't be written must never undo
    # that record. It is logged instead, with the code and ids only.
    savepoint = session.begin_nested()
    try:
        raised = raise_alert(
            session,
            alert_type=alert_type,
            severity=entry.severity,
            tenant_id=tenant_id,
            payload=payload,
            dedupe_key=f"{alert_type}:{tenant_id}:{entry.code}" + (f":{cause}" if cause else ""),
            dedupe_per_utc_day=True,
        )
    except Exception:
        savepoint.rollback()
        logger.exception(
            "founder_alert_not_raised tenant_id=%s error_code=%s", tenant_id, entry.code
        )
        return False
    savepoint.commit()
    return raised


def raise_storage_unavailable(session: Session, *, tenant_id: UUID, where: str) -> bool:
    """
    Stage 3b (Q3): Storage couldn't be reached. At most one alert per UTC
    hour for the whole platform, with no new migration: the open-alert dedupe
    index (0011) is unique across every tenant and applies whatever RLS lets
    a session see, so each tenant session raises the alert for its own tenant
    under the same hourly key, the first in the hour writes the row, and the
    rest write nothing. The alert therefore names the first tenant to hit it,
    not a count; every failure is still logged with its tenant.

    `where` is a fixed label for the call site (upload, email_intake, worker,
    export_download), never anything from a document. Like raise_for_failure
    it has its own savepoint and never raises: an alert that can't be written
    must never undo what the caller is recording.
    """
    logger.error("storage_unavailable tenant_id=%s where=%s", tenant_id, where)
    savepoint = session.begin_nested()
    try:
        raised = raise_alert(
            session,
            alert_type="storage_unavailable",
            severity="high",
            tenant_id=tenant_id,
            payload={"first_seen_at": where},
            dedupe_key="storage_unavailable",
            dedupe_per_utc_hour=True,
        )
    except Exception:
        savepoint.rollback()
        logger.exception("founder_alert_not_raised tenant_id=%s alert=storage_unavailable", tenant_id)
        return False
    savepoint.commit()
    return raised


def report_refused_storage_path(
    exc: Exception, *, tenant_id: UUID, where: str, ref_id: UUID | str | None
) -> None:
    """
    Stage 3b (founder, 2026-09-30): a stored path the prefix rules refused.
    Nothing was read; this only records it.

    * A malformed path (seed data, a bug): logged as `storage_path_refused`.
    * A well-formed path under another tenant's folder
      (`CrossTenantStoragePathError`): logged as `storage_path_cross_tenant`
      and raised as a critical alert, one open alert per record, because
      Section 7.5's isolation rule caught something.

    `where` is a fixed call-site label and `ref_id` the order, export or
    import id -- never the path or anything from a document (Section 7.10).
    Opens its own tenant session and never raises: the caller is about to
    answer with its own error.
    """
    from docflow_core.storage import CrossTenantStoragePathError

    if not isinstance(exc, CrossTenantStoragePathError):
        logger.error("storage_path_refused tenant_id=%s where=%s ref_id=%s", tenant_id, where, ref_id)
        return
    logger.error(
        "storage_path_cross_tenant tenant_id=%s named_tenant_id=%s where=%s ref_id=%s",
        tenant_id,
        exc.named_tenant_id,
        where,
        ref_id,
    )
    from docflow_core.db import tenant_session

    try:
        with tenant_session(tenant_id) as session:
            raise_alert(
                session,
                alert_type="storage_path_cross_tenant",
                severity="critical",
                tenant_id=tenant_id,
                payload={
                    "where": where,
                    "ref_id": str(ref_id) if ref_id else None,
                    "named_tenant_id": exc.named_tenant_id,
                },
                dedupe_key=f"storage_path_cross_tenant:{tenant_id}:{ref_id}",
            )
    except Exception:  # noqa: BLE001 -- logged above and here; the caller's error still goes out
        logger.exception("founder_alert_not_raised tenant_id=%s alert=storage_path_cross_tenant", tenant_id)


def raise_seccomp_kill(
    session: Session, *, tenant_id: UUID, where: str, ref_id: UUID | None, error_code: str
) -> bool:
    """
    A parse job killed by the seccomp filter (founder, 2026-10-01). Its own
    alert type, raised for every kill: the dedupe key names the document or
    import, and there is no daily or hourly window. Its own savepoint; never
    raises.
    """
    savepoint = session.begin_nested()
    try:
        raised = raise_alert(
            session,
            alert_type="parse_seccomp_kill",
            severity="high",
            tenant_id=tenant_id,
            payload={
                "error_code": error_code,
                "cause": "seccomp_kill",
                "signal": "SIGSYS (31)",
                "syscall": None,
                "syscall_unavailable": SECCOMP_SYSCALL_UNAVAILABLE,
                "where": where,
                **({"ref_id": str(ref_id)} if ref_id else {}),
            },
            dedupe_key=f"parse_seccomp_kill:{where}:{ref_id or uuid4()}",
        )
    except Exception:
        savepoint.rollback()
        logger.exception("founder_alert_not_raised tenant_id=%s alert=parse_seccomp_kill", tenant_id)
        return False
    savepoint.commit()
    logger.error("parse_seccomp_kill tenant_id=%s where=%s ref_id=%s", tenant_id, where, ref_id)
    return raised


def alert_seccomp_kill(tenant_id: UUID, *, where: str, ref_id: UUID | None, error_code: str) -> None:
    """raise_seccomp_kill in its own tenant session. Never raises."""
    try:
        from docflow_core.db import tenant_session

        with tenant_session(tenant_id) as session:
            raise_seccomp_kill(
                session, tenant_id=tenant_id, where=where, ref_id=ref_id, error_code=error_code
            )
    except Exception:
        logger.exception("founder_alert_not_raised tenant_id=%s alert=parse_seccomp_kill", tenant_id)


def raise_parse_service_unavailable(session: Session, *, tenant_id: UUID, where: str, reason: str) -> bool:
    """
    Stage 3c (founder, Q4): the parse service couldn't be reached -- the
    request never got in, so the document waits and no try is used. At most
    one alert per UTC hour for the whole platform, by the same mechanism as
    raise_storage_unavailable (one hourly key, unique across tenants; the
    first tenant in the hour writes the row).

    `where` is a fixed call-site label; `reason` is the client's fixed label
    (no_connection, http_503, unauthorized, http_NNN), never
    response text. Its own savepoint; never raises.
    """
    logger.error("parse_service_unavailable tenant_id=%s where=%s reason=%s", tenant_id, where, reason)
    savepoint = session.begin_nested()
    try:
        raised = raise_alert(
            session,
            alert_type="parse_service_unavailable",
            severity="high",
            tenant_id=tenant_id,
            payload={"first_seen_at": where, "reason": reason},
            dedupe_key="parse_service_unavailable",
            dedupe_per_utc_hour=True,
        )
    except Exception:
        savepoint.rollback()
        logger.exception("founder_alert_not_raised tenant_id=%s alert=parse_service_unavailable", tenant_id)
        return False
    savepoint.commit()
    return raised


def raise_parse_alert(
    session: Session,
    *,
    tenant_id: UUID,
    document_id: UUID | None,
    error_code: str,
    cause: str,
    by_cause: bool,
) -> bool:
    """
    Stage 3c (founder, Q3): a parse job that stopped at a limit (DOC-029), or
    that crashed some other way or answered something the worker refused
    (DOC-005). Either may be a file built to attack a parser, so the founder
    hears about it even though the catalog text (founder-approved) promises
    the customer no alert -- which is why these are not in FAILURE_ALERTS.

    One `document_failed` alert per tenant per code per UTC day (DOC-029,
    `by_cause=False`), or per tenant per code per cause per day (DOC-005,
    whose causes are distinct faults). `cause` is a fixed label
    (stopped:memory, crashed:signal_11, invalid:bad_part...). Its own
    savepoint; never raises.
    """
    entry = get_error(error_code)
    if cause == SECCOMP_KILL_CAUSE:
        return raise_seccomp_kill(
            session, tenant_id=tenant_id, where="worker", ref_id=document_id, error_code=entry.code
        )
    savepoint = session.begin_nested()
    try:
        raised = raise_alert(
            session,
            alert_type="document_failed",
            severity="high",
            tenant_id=tenant_id,
            payload={
                "error_code": entry.code,
                "cause": cause,
                **({"first_document_id": str(document_id)} if document_id else {}),
            },
            dedupe_key=f"document_failed:{tenant_id}:{entry.code}:parse" + (f":{cause}" if by_cause else ""),
            dedupe_per_utc_day=True,
        )
    except Exception:
        savepoint.rollback()
        logger.exception("founder_alert_not_raised tenant_id=%s error_code=%s", tenant_id, entry.code)
        return False
    savepoint.commit()
    return raised


def alert_parse_service_unavailable(tenant_id: UUID, *, where: str, reason: str) -> None:
    """raise_parse_service_unavailable in its own tenant session. Never raises."""
    from docflow_core.db import tenant_session

    try:
        with tenant_session(tenant_id) as session:
            raise_parse_service_unavailable(session, tenant_id=tenant_id, where=where, reason=reason)
    except Exception:  # noqa: BLE001 -- logged; the caller's error still goes out
        logger.exception("founder_alert_not_raised tenant_id=%s alert=parse_service_unavailable", tenant_id)


def alert_storage_unavailable(tenant_id: UUID, *, where: str) -> None:
    """raise_storage_unavailable in its own tenant session, for a caller whose
    own transaction has just rolled back because Storage failed. Never
    raises: the caller is about to return its own catalog error."""
    from docflow_core.db import tenant_session

    try:
        with tenant_session(tenant_id) as session:
            raise_storage_unavailable(session, tenant_id=tenant_id, where=where)
    except Exception:  # noqa: BLE001 -- logged; the caller's error still goes out
        logger.exception("founder_alert_not_raised tenant_id=%s alert=storage_unavailable", tenant_id)
