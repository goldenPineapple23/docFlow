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
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docflow_core import email_outbox
from docflow_core.config import get_settings

# Every alert type the system raises, with the title the founder reads.
# Section 7.15.3 lists the MVP set; each is added here by the slice that
# starts raising it, so this table is also the inventory of what is wired.
ALERT_TYPES: dict[str, str] = {
    "export_integrity_failure": "An export failed its integrity check",
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
