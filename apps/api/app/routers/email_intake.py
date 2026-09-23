"""
Inbound email intake webhook (CLAUDE.md Section 7.2 / 7.16.3). Provider:
Postmark's inbound webhook (see DECISIONS.md). This router is a thin
adapter -- all parsing and abuse-defense decisions live in
docflow_core.email_intake, which is also what this router's tests import
directly, per this slice's own instructions.

No auth dependency: the per-tenant token in the URL path IS the
authentication (exactly like intake_addresses.token's existing design from
0001_foundations.sql). tenant_id is never trusted from the payload -- only
from what the token resolves to (CLAUDE.md Section 7.5 / Section 10).

Every handled outcome (accepted, rejected, quarantined, duplicate) returns
200: a webhook provider must never see this pipeline's own abuse decisions
as delivery failures, or it will retry and make things worse. Only a
genuinely bad request -- an unresolvable token, or an unparseable payload --
gets a non-200.
"""

from __future__ import annotations

from docflow_core import email_intake
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/intake", tags=["email-intake"])


@router.post("/email/{token}")
async def receive_inbound_email(token: str, request: Request) -> dict:
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

    result = email_intake.process_inbound_email(tenant_id, parsed)
    return {"outcome": result.outcome}
