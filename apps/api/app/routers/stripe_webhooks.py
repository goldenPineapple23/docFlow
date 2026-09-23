"""
Inbound Stripe webhook (CLAUDE.md Section 7.12). Thin adapter -- signature
verification and event handling live in docflow_core.billing_webhooks,
exactly like email_intake's split.

No platform-admin auth dependency: Stripe's signature (verified against
STRIPE_WEBHOOK_SECRET) IS the authentication, the same shape as the email
intake token. A request with no/invalid signature is refused before the
body is trusted as JSON at all -- Section 7.12's "document content is
untrusted data" applies here to webhook content too.
"""

from __future__ import annotations

from docflow_core import billing_webhooks
from docflow_core.config import get_settings
from docflow_core.external_services import ExternalServiceError, verify_webhook_signature
from fastapi import APIRouter, Header, HTTPException, Request

router = APIRouter(prefix="/webhooks", tags=["stripe-webhooks"])


@router.post("/stripe")
async def receive_stripe_webhook(
    request: Request, stripe_signature: str | None = Header(default=None)
) -> dict:
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Stripe webhooks are not configured.")
    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header.")

    payload = await request.body()
    try:
        event = verify_webhook_signature(payload, stripe_signature, settings.stripe_webhook_secret)
    except ExternalServiceError as exc:
        raise HTTPException(status_code=400, detail="Signature verification failed.") from exc

    outcome = billing_webhooks.process_event(event)
    return {"outcome": outcome}
