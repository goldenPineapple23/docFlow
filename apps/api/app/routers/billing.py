"""
The owner's Billing page (card billing, founder 2026-09-29; decisions D1-D6 in
docs/BUILD-STATUS.md; D-181).

Owners and admins only (`require_tenant_admin`), with `tenant_id` from the
session and never the request (Section 7.5). Card details never reach DocFlow:
both buttons return a Stripe-hosted page to go to -- Checkout to add a card
(and pay a standard customer's setup fee at signing, D2), the customer portal
to change it. A Checkout link expires after 24 hours, so each click makes a
fresh one; the emails DocFlow sends point here, not at Stripe.
"""

from __future__ import annotations

import logging

from docflow_core import card_billing, external_services
from docflow_core.config import get_settings
from docflow_core.db import tenant_session
from docflow_core.external_services import ExternalServiceError
from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.deps import AuthenticatedIdentity, get_current_identity, require_tenant_admin
from app.errors import catalog_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


def _tenant(tenant_id) -> dict:
    with tenant_session(tenant_id) as session:
        row = (
            session.execute(
                text(
                    """
                SELECT onboarding_status, billing_method, stripe_customer_id, stripe_subscription_status,
                       card_on_file_at, setup_fee_paid_at, setup_fee_amount, setup_fee_billing,
                       founding_price
                  FROM tenants WHERE id = :id
                """
                ),
                {"id": str(tenant_id)},
            )
            .mappings()
            .one()
        )
        banner = card_billing.past_due_banner(session, tenant_id)
    return {**row, "banner": banner}


@router.get("")
def get_billing(identity: AuthenticatedIdentity = Depends(get_current_identity)) -> dict:
    tenant_id = require_tenant_admin(identity)
    t = _tenant(tenant_id)
    fee = card_billing.fee_due_at_signing(t)
    banner = t["banner"]
    return {
        "billing_method": t["billing_method"] if t["onboarding_status"] == "live" else None,
        "card_on_file": t["card_on_file_at"] is not None,
        "subscription_status": t["stripe_subscription_status"],
        "setup_fee_due_now": str(fee) if fee is not None else None,
        "setup_fee_paid": t["setup_fee_paid_at"] is not None,
        "banner": (
            {"code": banner.code, "title": banner.title, "message": banner.message, "action": banner.action}
            if banner
            else None
        ),
    }


@router.post("/card-page")
def open_card_page(identity: AuthenticatedIdentity = Depends(get_current_identity)) -> dict:
    """A fresh Stripe Checkout page to add a card: setup mode, or payment mode
    for a setup fee due at signing. Nothing is charged until the customer
    confirms on Stripe's page."""
    tenant_id = require_tenant_admin(identity)
    t = _tenant(tenant_id)
    if not t["stripe_customer_id"]:
        raise catalog_error("BIL-008", status_code=502, extra={"reason": "no Stripe customer"})
    base = get_settings().app_base_url.rstrip("/")
    try:
        page = external_services.create_card_page(
            tenant_id=tenant_id,
            customer_id=t["stripe_customer_id"],
            setup_fee=card_billing.fee_due_at_signing(t),
            success_url=f"{base}/billing?card=saved",
            cancel_url=f"{base}/billing",
        )
    except ExternalServiceError as exc:
        logger.error("card_page_failed tenant_id=%s error=%s", tenant_id, exc)
        raise catalog_error("BIL-008", status_code=502) from exc
    return {"url": page.url}


@router.post("/card-update-page")
def open_card_update_page(identity: AuthenticatedIdentity = Depends(get_current_identity)) -> dict:
    """Stripe's portal, on its "update payment method" step. The new card
    becomes the default; if the account is past due, DocFlow then charges the
    open invoice to it (billing_webhooks._card_updated)."""
    tenant_id = require_tenant_admin(identity)
    t = _tenant(tenant_id)
    if not t["stripe_customer_id"]:
        raise catalog_error("BIL-008", status_code=502, extra={"reason": "no Stripe customer"})
    base = get_settings().app_base_url.rstrip("/")
    try:
        url = external_services.create_card_update_page(
            customer_id=t["stripe_customer_id"], return_url=f"{base}/billing"
        )
    except ExternalServiceError as exc:
        logger.error("card_update_page_failed tenant_id=%s error=%s", tenant_id, exc)
        raise catalog_error("BIL-008", status_code=502) from exc
    return {"url": url}
