"""
The people on an account: the Team page (slice 5.8d; DECISIONS.md D-132).

Section 3: "every subsequent user is invited by a tenant owner/admin". The
founder chose the smallest version that does that (option A, 2026-09-24): the
account's admin lists the team, invites a reviewer, resends an invite, and
removes someone. No role changes, no second admin, nothing about billing.

Everything runs in the tenant's own session, so it can only ever see and change
this tenant's users (Section 7.5). The rules that keep an account manageable are
enforced here, not in the page:

  * only reviewers are invited, and only reviewers (or a viewer, should one
    exist) can be removed -- the account's admin stays (TEAM-007);
  * nobody removes themselves (TEAM-006);
  * removing is `is_active = false`, never a delete: the person's approvals and
    edits stay in the account's history under their name, and sign-in refuses
    them from the next request (`app.deps`, AUTH-004). Inviting the same address
    again restores them.

Every invite and removal is a `tenant_lifecycle_events` row, which the Activity
page reads (`tenant_home`), naming who did it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docflow_core import email_outbox, external_services, invites

# Roles the Team page may remove. The admin roles are never removable here.
REMOVABLE_ROLES = ("reviewer", "viewer")
INVITED_ROLE = "reviewer"

# A deliberately plain check: one @, something on both sides, a dot in the
# domain, no spaces. Whether the mailbox exists is the invite's job to find out.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAIL_LENGTH = 254


class TeamError(Exception):
    """A refusal with its catalog code (Section 7.16.5)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Member:
    user_id: UUID
    email: str
    full_name: str | None
    role: str
    invite_sent_at: datetime | None
    first_signed_in_at: datetime | None
    is_you: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_id": str(self.user_id),
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "invite_sent_at": self.invite_sent_at.isoformat() if self.invite_sent_at else None,
            "signed_in": self.first_signed_in_at is not None,
            "is_you": self.is_you,
            "can_remove": self.role in REMOVABLE_ROLES and not self.is_you,
            "can_resend": self.first_signed_in_at is None and not self.is_you,
        }


def normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if len(email) > MAX_EMAIL_LENGTH or not _EMAIL.match(email):
        raise TeamError("TEAM-001")
    return email


def members(session: Session, tenant_id: UUID, *, me: UUID) -> list[Member]:
    """Everyone with access now. Removed people are not listed; their history
    is on the Activity page."""
    rows = session.execute(
        text(
            """
            SELECT id, email, full_name, role, invite_sent_at, first_signed_in_at
            FROM users
            WHERE tenant_id = :t AND is_active AND deleted_at IS NULL
            ORDER BY CASE WHEN role IN ('owner', 'admin') THEN 0 ELSE 1 END, created_at, email
            """
        ),
        {"t": str(tenant_id)},
    ).mappings().all()
    return [
        Member(
            user_id=UUID(str(r["id"])),
            email=r["email"],
            full_name=r["full_name"],
            role=r["role"],
            invite_sent_at=r["invite_sent_at"],
            first_signed_in_at=r["first_signed_in_at"],
            is_you=str(r["id"]) == str(me),
        )
        for r in rows
    ]


def _tenant_name(session: Session, tenant_id: UUID) -> str:
    return str(
        session.execute(text("SELECT name FROM tenants WHERE id = :t"), {"t": str(tenant_id)}).scalar_one()
    ).strip()


def _event(session: Session, tenant_id: UUID, event_type: str, actor: UUID, user_id: UUID) -> None:
    session.execute(
        text(
            """
            INSERT INTO tenant_lifecycle_events
                (id, tenant_id, event_type, actor_user_id, payload, created_at)
            VALUES (:id, :t, :type, :actor, CAST(:payload AS jsonb), clock_timestamp())
            """
        ),
        {
            "id": str(uuid4()),
            "t": str(tenant_id),
            "type": event_type,
            "actor": str(actor),
            "payload": json.dumps({"user_id": str(user_id)}),
        },
    )


def _send(session: Session, tenant_id: UUID, *, actor: UUID, user: dict[str, Any]) -> UUID:
    """The invite itself, through the one path every invite takes. A failure
    anywhere rolls back to the savepoint, so a half-made invite never stays."""
    inviter = session.execute(
        text("SELECT coalesce(full_name, email) FROM users WHERE id = :u"), {"u": str(actor)}
    ).scalar_one()
    savepoint = session.begin_nested()
    try:
        outbox_id = invites.issue(
            session,
            tenant_id=tenant_id,
            user_id=user["id"],
            email=user["email"],
            existing_auth_user_id=user["auth_user_id"],
            template="team_invite",
            params={"tenant_name": _tenant_name(session, tenant_id), "inviter": inviter},
        )
    except external_services.ExternalServiceError:
        savepoint.rollback()
        raise TeamError("TEAM-004") from None
    except (invites.InviteLinkedElsewhere, IntegrityError):
        # This address's sign-in already belongs to someone else -- another
        # account's user, or DocFlow's own operator. `users.auth_user_id` is
        # unique, so the database refuses it even though this tenant's session
        # cannot see who holds it -- and neither can the admin (Section 7.5).
        savepoint.rollback()
        raise TeamError("TEAM-003") from None
    savepoint.commit()
    return outbox_id


def invite(session: Session, tenant_id: UUID, *, actor: UUID, raw_email: str) -> dict[str, Any]:
    """Invite a reviewer. An address that was removed earlier is restored rather
    than duplicated; one that is already on the team is refused (TEAM-002)."""
    email = normalize_email(raw_email)
    existing = session.execute(
        text(
            "SELECT id, email, role, is_active, deleted_at, auth_user_id FROM users "
            "WHERE tenant_id = :t AND lower(email) = :e"
        ),
        {"t": str(tenant_id), "e": email},
    ).mappings().first()

    restored = False
    if existing is not None:
        if existing["is_active"] and existing["deleted_at"] is None:
            raise TeamError("TEAM-002")
        user = dict(existing)
        session.execute(
            text(
                "UPDATE users SET is_active = true, deleted_at = NULL, role = :role, updated_at = now() "
                "WHERE id = :u"
            ),
            {"u": str(user["id"]), "role": INVITED_ROLE},
        )
        restored = True
    else:
        user_id = uuid4()
        # The savepoint in _send covers the link; the row itself is created in
        # the caller's transaction, so a refused invite leaves no user behind.
        session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, role, is_active) "
                "VALUES (:id, :t, :e, :role, true)"
            ),
            {"id": str(user_id), "t": str(tenant_id), "e": email, "role": INVITED_ROLE},
        )
        user = {"id": user_id, "email": email, "auth_user_id": None}

    outbox_id = _send(session, tenant_id, actor=actor, user=user)
    _event(session, tenant_id, "user_invited", actor, UUID(str(user["id"])))
    return {
        "user_id": str(user["id"]),
        "email": email,
        "restored": restored,
        "email_outbox_id": str(outbox_id),
        "held": not email_outbox.provider_configured(),
    }


def _member_row(session: Session, tenant_id: UUID, user_id: UUID) -> dict[str, Any]:
    row = session.execute(
        text(
            "SELECT id, email, role, auth_user_id, first_signed_in_at FROM users "
            "WHERE id = :u AND tenant_id = :t AND is_active AND deleted_at IS NULL"
        ),
        {"u": str(user_id), "t": str(tenant_id)},
    ).mappings().first()
    if row is None:
        raise TeamError("TEAM-008")
    return dict(row)


def resend(session: Session, tenant_id: UUID, *, actor: UUID, user_id: UUID) -> dict[str, Any]:
    user = _member_row(session, tenant_id, user_id)
    if user["first_signed_in_at"] is not None:
        raise TeamError("TEAM-005")
    outbox_id = _send(session, tenant_id, actor=actor, user=user)
    _event(session, tenant_id, "user_invited", actor, user_id)
    return {
        "user_id": str(user_id),
        "email": user["email"],
        "email_outbox_id": str(outbox_id),
        "held": not email_outbox.provider_configured(),
    }


def remove(session: Session, tenant_id: UUID, *, actor: UUID, user_id: UUID) -> dict[str, Any]:
    user = _member_row(session, tenant_id, user_id)
    if str(user_id) == str(actor):
        raise TeamError("TEAM-006")
    if user["role"] not in REMOVABLE_ROLES:
        raise TeamError("TEAM-007")
    session.execute(
        text("UPDATE users SET is_active = false, updated_at = now() WHERE id = :u"),
        {"u": str(user_id)},
    )
    _event(session, tenant_id, "user_removed", actor, user_id)
    return {"user_id": str(user_id), "email": user["email"]}


def mark_signed_in(session: Session, user_id: UUID) -> None:
    """Stamp the first sign-in, once. Called from GET /auth/me, which every
    signed-in screen calls, so it needs no separate "accepted" step."""
    session.execute(
        text("UPDATE users SET first_signed_in_at = now() WHERE id = :u AND first_signed_in_at IS NULL"),
        {"u": str(user_id)},
    )
