"""
The email outbox (CLAUDE.md Section 7.9 / 7.15.2; DECISIONS.md D-103).

Every email DocFlow sends is written here first, rendered from a template in
`email_templates/` (Section 7.15.2 Step 9: "templates in the repo, not
hardcoded strings"). A sender delivers from the row. So:

  * nothing is sent from a code path that leaves no record;
  * with no email provider configured -- the state of every development
    machine, and of staging until a sending domain exists -- rows are
    `held`, and the founder reads them in the Console instead. An invite
    link, for instance, can be copied from there. Nothing is silently lost.

Templates are plain text with `$name` placeholders. Every placeholder must be
supplied (`string.Template.substitute`), so a template change that adds a
field fails loudly in tests rather than sending "$tenant_name" to a customer.

Reply-To (founder, 2026-09-29; migration 0032): every email to the customer's
own people carries the support mailbox (SUPPORT_EMAIL) as its Reply-To, so a
reply reaches someone who reads it. Founder alerts don't, and nor do the
intake address's automatic replies, which go to the customer's buyers: a
buyer's reply belongs with the supplier, not DocFlow's support. Every template
is in exactly one of the two sets below (a test checks it), so a new template
is a deliberate choice.
"""

from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core.config import get_settings

TEMPLATE_DIR = Path(__file__).parent / "email_templates"

# Emails to the customer's own people: Reply-To is the support mailbox.
TO_CUSTOMER = frozenset(
    {
        "allowance_notice",
        "cancellation_confirmed",
        "card_request_at_signing",
        "card_request_founding",
        "card_request_no_fee",
        "first_week_checkin",
        "go_live",
        "intake_address_rotated",
        "invite",
        "payment_failed",
        "payment_failed_reminder",
        "pending_deletion_reminder",
        "reactivated",
        "review_digest",
        "suspended_notice",
        "team_invite",
        "trial_ending_at_signing",
        "trial_ending_founding",
        "trial_ending_no_fee",
    }
)
# Emails to anyone else: no Reply-To. The founder's own alerts, and the intake
# address's automatic replies to the customer's buyers.
NOT_TO_CUSTOMER = frozenset(
    {"founder_alert", "intake_address_changed", "intake_held", "intake_not_active", "intake_suspended"}
)


def template_names() -> list[str]:
    return sorted(path.stem for path in TEMPLATE_DIR.glob("*.txt"))


def render(template: str, params: dict[str, Any]) -> tuple[str, str]:
    """(subject, body) for a template. Raises KeyError on a missing field."""
    if template not in template_names():
        raise KeyError(f"unknown email template {template!r}")
    raw = (TEMPLATE_DIR / f"{template}.txt").read_text(encoding="utf-8")
    first, _, body = raw.partition("\n\n")
    if not first.startswith("Subject: "):
        raise ValueError(f"template {template!r} must start with a 'Subject: ' line")
    values = {key: str(value) for key, value in params.items()}
    subject = Template(first[len("Subject: "):]).substitute(values)
    return subject.strip(), Template(body).substitute(values)


def reply_to_for(template: str) -> str | None:
    """The Reply-To an email from this template carries: SUPPORT_EMAIL for an
    email to the customer's own people (None while it is blank), else None."""
    if template not in TO_CUSTOMER:
        return None
    return get_settings().support_email.strip() or None


def provider_configured() -> bool:
    return bool(get_settings().email_provider_api_key)


def enqueue(
    session: Session,
    *,
    tenant_id: UUID | None,
    to_address: str,
    template: str,
    params: dict[str, Any],
    related_type: str | None = None,
    related_id: UUID | None = None,
) -> UUID:
    """
    Write one outbox row and return its id. `queued` when a provider is
    configured (a sender will deliver it), `held` otherwise. Its Reply-To is
    filled here, from the template (`reply_to_for`).

    Works in a tenant session for that tenant's own mail (0011's
    `tenant_enqueue` policy) and in a platform session for anything. It does
    not read the row back: a tenant session may add mail but never read it.
    """
    subject, body = render(template, params)
    outbox_id = uuid4()
    session.execute(
        text(
            """
            INSERT INTO email_outbox
                (id, tenant_id, to_address, reply_to, template, subject, body_text, status,
                 related_type, related_id)
            VALUES
                (:id, :tenant_id, :to_address, :reply_to, :template, :subject, :body_text, :status,
                 :related_type, :related_id)
            """
        ),
        {
            "id": str(outbox_id),
            "tenant_id": str(tenant_id) if tenant_id else None,
            "to_address": to_address,
            "reply_to": reply_to_for(template),
            "template": template,
            "subject": subject,
            "body_text": body,
            "status": "queued" if provider_configured() else "held",
            "related_type": related_type,
            "related_id": str(related_id) if related_id else None,
        },
    )
    return outbox_id
