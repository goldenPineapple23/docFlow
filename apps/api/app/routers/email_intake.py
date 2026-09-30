"""
Inbound email intake webhook (CLAUDE.md Section 7.2 / 7.16.3). Provider:
Postmark's inbound webhook (see DECISIONS.md). This router is a thin
adapter -- all parsing and abuse-defense decisions live in
docflow_core.email_intake, which is also what this router's tests import
directly, per this slice's own instructions.

Two separate questions, deliberately (review finding H8):

- **Is this the mail provider?** HTTP Basic credentials that Postmark carries
  on the webhook URL, checked first, in constant time, before anything else
  touches the request. A blank configuration refuses everything -- see
  `deps.check_inbound_webhook_credentials` and RUNBOOK section 2's cutover
  order.
- **Which tenant is this for?** The per-tenant token in the URL path, exactly
  as before. It identifies; it no longer authenticates, because it is the local
  part of an address the customer hands to its buyers and is public by design.

Before H8 was fixed, the token answered both -- so anyone a tenant gave its
intake address to could post a Postmark-shaped body with any `From` and a
forged `Authentication-Results`, walking straight through the DMARC/SPF
quarantine, the unknown-sender velocity rule and sender-based example
selection (Section 7.16.3).

tenant_id is never trusted from the payload -- only from what the token
resolves to (CLAUDE.md Section 7.5 / Section 10).

Every handled outcome (accepted, rejected, quarantined, duplicate) returns
200: a webhook provider must never see this pipeline's own abuse decisions
as delivery failures, or it will retry and make things worse. Only a
genuinely bad request -- an unresolvable token, or an unparseable payload --
gets a non-200.
"""

from __future__ import annotations

import logging

from docflow_core import email_intake, founder_alerts
from docflow_core.storage import StorageUnavailableError
from fastapi import APIRouter, Header, HTTPException, Request

from app.deps import (
    InboundWebhookRefused,
    check_inbound_webhook_credentials,
    inbound_source_ip_note,
)

logger = logging.getLogger("docflow.api")

router = APIRouter(prefix="/intake", tags=["email-intake"])

def _refuse(reason: str, client_host: str | None) -> HTTPException:
    """
    Log a refused inbound request, then refuse it.

    Nothing here carries the credential -- supplied or configured -- into a log
    line or the response (Section 7.10). `reason` is one of a fixed set of words
    from `deps`, and the response body is empty.

    **And the founder is alerted** (D-171, Stage 2c): a misconfigured
    credential and an attacker look identical from outside, and the first means
    no mail arrives at all. `email_intake.alert_webhook_refused` raises one
    high-severity alert per reason (collapsed while open) through migration
    0029's narrow insert policies, and never raises -- so this stays a 401 even
    if the alert cannot be written.
    """
    logger.warning("intake_webhook_refused reason=%s", reason)
    email_intake.alert_webhook_refused(reason)
    if client_host is not None:
        # Recorded only alongside a refusal, where it helps tell a
        # misconfiguration from a probe. Never a reason to refuse (D-155).
        note = inbound_source_ip_note(client_host)
        if note:
            logger.warning("intake_webhook_refused %s", note)
    # 401, not 404: Postmark's own delivery log should say "unauthorized" so a
    # cutover mistake is legible from the provider's side too.
    return HTTPException(status_code=401, detail="Unauthorized.")


@router.post("/email/{token}")
async def receive_inbound_email(
    token: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    client_host = request.client.host if request.client else None
    try:
        check_inbound_webhook_credentials(authorization)
    except InboundWebhookRefused as refused:
        raise _refuse(refused.reason, client_host) from None

    note = inbound_source_ip_note(client_host)
    if note:
        # Log-only, always: the allowlist is corroboration until confirmed
        # against Postmark's published addresses (RUNBOOK section 2, D-155).
        logger.warning("intake_webhook_accepted %s", note)

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Malformed request body: not valid JSON.") from exc

    resolved = email_intake.resolve_tenant_by_token(token)
    if resolved is None or resolved[1] not in ("active", "grace"):
        # Generic 404 whether the token never existed or exists but isn't
        # active -- CLAUDE.md Section 7.15.1's "unreachable, not just
        # hidden" principle, applied here to intake tokens (DECISIONS.md).
        # A token that is active but belongs to a tenant not yet live is
        # accepted here and turned away inside process_inbound_email, with
        # the "not yet active" auto-reply (D-114).
        raise HTTPException(status_code=404)
    tenant_id, address_status = resolved

    try:
        parsed = email_intake.parse_postmark_payload(payload)
    except email_intake.MalformedPayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if address_status == "grace":
        # A rotated address inside its grace period (7.16.3): nothing is
        # processed, and the sender is told the address has changed. Past the
        # grace period the lookup treats it as retired, so it 404s above.
        result = email_intake.reply_address_changed(tenant_id, parsed)
        return {"outcome": result.outcome}

    try:
        result = email_intake.process_inbound_email(tenant_id, parsed)
    except StorageUnavailableError as exc:
        # Stage 3b (Q3): the whole email's transaction rolled back, so nothing
        # was received. A 5xx -- never a 403, which Postmark treats as final --
        # makes Postmark send the same email again later; the Message-ID and
        # attachment-hash dedupe (7.8) keeps a retry from doubling anything.
        founder_alerts.alert_storage_unavailable(tenant_id, where="email_intake")
        raise HTTPException(status_code=503, detail="Temporarily unavailable.") from exc
    return {"outcome": result.outcome}
