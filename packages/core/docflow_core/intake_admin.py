"""
Intake-address administration (CLAUDE.md Section 7.16.3; D-126): rotating a
tenant's intake address, the opt-in strict sender allowlist, and the periodic
housekeeping (retiring expired grace addresses, surfacing held documents that
have outlived their retention period).

All of it runs inside one tenant's own session. Rotation and the allowlist are
Console actions by the founder; the routes write the `admin_actions` row.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import email_outbox, founder_alerts, quarantine
from docflow_core.config import get_settings
from docflow_core.constants import (
    QUARANTINE_TTL_DAYS,
    ROTATED_ADDRESS_GRACE_DAYS,
    constants_in_effect,
)
from docflow_core.lifecycle import _lifecycle_event
from docflow_core.quarantine import QuarantineError

_ADDRESS_OR_DOMAIN = re.compile(r"^(?:[\w.+-]+@)?[a-z0-9-]+(?:\.[a-z0-9-]+)+$")


def rotate_address(session: Session, tenant_id: UUID, *, actor_user_id: UUID) -> dict[str, Any]:
    """Issue a new intake address. The old one keeps replying "this address has
    changed" for ROTATED_ADDRESS_GRACE_DAYS, then is retired (7.16.3). Logged as a
    lifecycle event; the owner is emailed the new address."""
    old = (
        session.execute(
            text("SELECT id FROM intake_addresses WHERE tenant_id = :t AND status = 'active' FOR UPDATE"),
            {"t": str(tenant_id)},
        )
        .mappings()
        .first()
    )
    if old is None:
        raise QuarantineError("QUA-004")

    # Demote first: only one address per tenant may be active at a time.
    grace_ends_at = session.execute(
        text(
            "UPDATE intake_addresses SET status = 'grace', "
            "grace_ends_at = now() + make_interval(days => :days) "
            "WHERE id = :id RETURNING grace_ends_at"
        ),
        {"id": str(old["id"]), "days": ROTATED_ADDRESS_GRACE_DAYS},
    ).scalar_one()

    token = secrets.token_hex(16)  # 128 bits: unguessable (7.2)
    address = f"orders+{token}@{get_settings().intake_email_domain}"
    new_id = session.execute(
        text(
            "INSERT INTO intake_addresses (tenant_id, token, address, status, created_at) "
            "VALUES (:t, :token, :address, 'active', now()) RETURNING id"
        ),
        {"t": str(tenant_id), "token": token, "address": address},
    ).scalar_one()

    _lifecycle_event(
        session,
        tenant_id,
        "intake_address_rotated",
        actor_user_id=actor_user_id,
        # Ids only: the token is a credential and never goes in a log.
        payload={"old_address_id": str(old["id"]), "new_address_id": str(new_id)},
        constants=constants_in_effect("ROTATED_ADDRESS_GRACE_DAYS"),
    )
    _email_owners_new_address(session, tenant_id, address, grace_ends_at)
    return {"address": address, "grace_ends_at": grace_ends_at}


def _email_owners_new_address(
    session: Session, tenant_id: UUID, address: str, grace_ends_at: datetime
) -> None:
    owners = (
        session.execute(
            text("SELECT email FROM users WHERE tenant_id = :t AND role = 'owner' AND deleted_at IS NULL"),
            {"t": str(tenant_id)},
        )
        .scalars()
        .all()
    )
    name = session.execute(text("SELECT name FROM tenants WHERE id = :t"), {"t": str(tenant_id)}).scalar_one()
    for owner in owners:
        email_outbox.enqueue(
            session,
            tenant_id=tenant_id,
            to_address=owner,
            template="intake_address_rotated",
            params={
                "tenant_name": name,
                "new_address": address,
                "grace_ends": grace_ends_at.date().isoformat(),
            },
            related_type="intake_address",
        )


def set_sender_allowlist(
    session: Session,
    tenant_id: UUID,
    *,
    strict: bool,
    entries: list[str],
    actor_user_id: UUID,
) -> dict[str, Any]:
    """Turn the strict sender allowlist on or off and set its entries. Opt-in
    only -- it is never the default, because a new buyer's first PO from an
    unknown address is the whole point of the product (7.16.3). While on, mail
    from anyone else is HELD (never rejected) for the tenant to release."""
    cleaned: list[str] = []
    for entry in entries:
        value = entry.strip().lower()
        if not value:
            continue
        if not _ADDRESS_OR_DOMAIN.match(value):
            raise QuarantineError("QUA-005", {"entry": value[:80]})
        if value not in cleaned:
            cleaned.append(value)
    if strict and not cleaned:
        raise QuarantineError("QUA-005")
    session.execute(
        text(
            "UPDATE tenants SET strict_sender_mode = :strict, "
            "sender_allowlist = string_to_array(:entries, ',') WHERE id = :t"
        ),
        # A Python list reaches Postgres as JSON here, not an array; entries
        # are validated to contain no comma, so a comma-joined string is safe.
        {"t": str(tenant_id), "strict": strict, "entries": ",".join(cleaned)},
    )
    _lifecycle_event(
        session,
        tenant_id,
        "sender_allowlist_changed",
        actor_user_id=actor_user_id,
        payload={"strict_sender_mode": strict, "entry_count": len(cleaned)},
    )
    return {"strict_sender_mode": strict, "sender_allowlist": cleaned}


def get_sender_settings(session: Session, tenant_id: UUID) -> dict[str, Any]:
    row = (
        session.execute(
            text("SELECT strict_sender_mode, sender_allowlist FROM tenants WHERE id = :t"),
            {"t": str(tenant_id)},
        )
        .mappings()
        .one()
    )
    return {
        "strict_sender_mode": row["strict_sender_mode"],
        "sender_allowlist": list(row["sender_allowlist"] or []),
    }


def housekeeping(session: Session, tenant_id: UUID) -> dict[str, int]:
    """Run periodically per tenant. Retires grace addresses whose period has
    ended, and raises one (deduplicated) founder alert when held documents are
    past QUARANTINE_TTL_DAYS. Never deletes anything."""
    retired = (
        session.execute(
            text(
                "UPDATE intake_addresses SET status = 'retired', retired_at = now() "
                "WHERE tenant_id = :t AND status = 'grace' AND grace_ends_at < now() RETURNING id"
            ),
            {"t": str(tenant_id)},
        )
        .scalars()
        .all()
    )
    expired = quarantine.expired_count(session, tenant_id)
    if expired:
        founder_alerts.raise_alert(
            session,
            alert_type="quarantine_ttl_elapsed",
            severity="warning",
            tenant_id=tenant_id,
            payload={"held_documents": expired, "retention_days": QUARANTINE_TTL_DAYS},
            dedupe_key=f"quarantine_ttl:{tenant_id}",
        )
    return {"retired_addresses": len(retired), "expired_held": expired}
