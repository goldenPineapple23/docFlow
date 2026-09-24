"""
Issuing an invite: one path for every invite DocFlow sends (D-105, D-132).

The founder invites a tenant's first user from the Console (Section 7.15.2
Step 3); the tenant's admin invites everyone after that from the Team page
(Section 3, slice 5.8d). Both need the same three things, so both call this:

  * a one-time "set your password" link from Supabase's admin API, WITHOUT
    Supabase sending its own email (`external_services.generate_invite_link`);
  * the local `users` row linked to that Supabase sign-in (`auth_user_id`,
    D-012), so the first sign-in lands in the right tenant with the right role;
  * the email written to DocFlow's own outbox from a template in the repo.

The caller owns the transaction and the audit rows, which differ: the Console
writes `admin_actions`, the Team page writes the account's own activity.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core import email_outbox, external_services
from docflow_core.config import get_settings


class InviteLinkedElsewhere(Exception):
    """The user row already points at a different Supabase sign-in than the one
    this email address has. Re-linking could hand the account to the wrong
    person, so nothing is changed."""


def issue(
    session: Session,
    *,
    tenant_id: UUID,
    user_id: UUID,
    email: str,
    existing_auth_user_id: UUID | str | None,
    template: str,
    params: dict[str, Any],
) -> UUID:
    """Create the link, link the user row to it, and queue the email. Returns
    the outbox row's id. Raises `external_services.ExternalServiceError` if
    Supabase cannot be reached (nothing is written), `InviteLinkedElsewhere` as
    above, and lets the database refuse an address whose sign-in already
    belongs to another user (`users.auth_user_id` is unique)."""
    link = external_services.generate_invite_link(
        email=email, redirect_to=f"{get_settings().app_base_url}/auth/accept"
    )
    if existing_auth_user_id is not None and str(existing_auth_user_id) != str(link.auth_user_id):
        raise InviteLinkedElsewhere()
    session.execute(
        text(
            "UPDATE users SET auth_user_id = :auth_user_id, invite_sent_at = now(), "
            "updated_at = now() WHERE id = :id"
        ),
        {"auth_user_id": str(link.auth_user_id), "id": str(user_id)},
    )
    return email_outbox.enqueue(
        session,
        tenant_id=tenant_id,
        to_address=email,
        template=template,
        params={**params, "invite_link": link.url},
        related_type="user",
        related_id=user_id,
    )
