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
}

# Failure codes whose catalog text promises the reader that DocFlow has been
# alerted, and the alert that keeps the promise (D-145). The catalog is what a
# customer reads, so a promise there with no alert behind it is a false
# statement to them. `tests/test_founder_alerts.py` fails the build when a
# catalog entry makes the promise and is neither here nor alerted elsewhere.
FAILURE_ALERTS: dict[str, str] = {
    "DOC-008": "document_failed",
    "DOC-009": "document_failed",
    "DOC-017": "document_failed",
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
) -> bool:
    """
    Raise an alert. Returns False when an open alert with the same dedupe key
    already exists (nothing is written). Works in a tenant session for that
    tenant (0011's `tenant_raise` policy) or a platform session.
    """
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
                    (:id, :type, :severity, :tenant_id, CAST(:payload AS jsonb), :dedupe_key,
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
) -> bool:
    """
    Raise the alert a failure's catalog message promises, if it promises one
    (D-145). Returns False when the code promises nothing, or when an open
    alert for the same tenant, code and day already exists: a flood of broken
    files is one alert, not a thousand.

    The payload is the code and ids only (Section 7.10: it is emailed).
    """
    alert_type = FAILURE_ALERTS.get(error_code or "")
    if alert_type is None:
        return False
    entry = get_error(error_code or "")
    day = datetime.now(timezone.utc).date().isoformat()
    payload: dict[str, Any] = {"error_code": entry.code}
    if document_id is not None:
        payload["first_document_id"] = str(document_id)
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
            dedupe_key=f"{alert_type}:{tenant_id}:{entry.code}:{day}",
        )
    except Exception:
        savepoint.rollback()
        logger.exception(
            "founder_alert_not_raised tenant_id=%s error_code=%s", tenant_id, entry.code
        )
        return False
    savepoint.commit()
    return raised
