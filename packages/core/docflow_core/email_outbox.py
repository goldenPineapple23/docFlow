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
    configured (a sender will deliver it), `held` otherwise.

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
                (id, tenant_id, to_address, template, subject, body_text, status,
                 related_type, related_id)
            VALUES
                (:id, :tenant_id, :to_address, :template, :subject, :body_text, :status,
                 :related_type, :related_id)
            """
        ),
        {
            "id": str(outbox_id),
            "tenant_id": str(tenant_id) if tenant_id else None,
            "to_address": to_address,
            "template": template,
            "subject": subject,
            "body_text": body,
            "status": "queued" if provider_configured() else "held",
            "related_type": related_type,
            "related_id": str(related_id) if related_id else None,
        },
    )
    return outbox_id
