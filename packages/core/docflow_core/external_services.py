"""
The two outside services the Console calls directly: Stripe (test mode until
Phase 6) and Supabase Auth's admin API. Kept in one small module so every
outbound call is easy to find, easy to mock in tests, and never carries
customer document data (Section 7.10) -- only a tenant's name, its id, and
the owner's email address, which both services need.

Called from API processes only (the Console), never from the worker, and
never with anything a client supplied beyond what the platform-admin route
has already validated.

Errors raised here carry a short type, never the provider's response text:
that text can echo request data, and it must not reach a tenant screen
(Section 7.16.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import httpx

from docflow_core.config import get_settings

TIMEOUT_SECONDS = 15


class ExternalServiceError(Exception):
    def __init__(self, service: str, reason: str):
        super().__init__(f"{service}: {reason}")
        self.service = service
        self.reason = reason


# ── Stripe ──────────────────────────────────────────────────────────────────


def _stripe_key() -> str:
    key = get_settings().stripe_secret_key
    if not key:
        raise ExternalServiceError("stripe", "STRIPE_SECRET_KEY is not set")
    return key


def create_stripe_customer(*, tenant_id: UUID, name: str, email: str) -> str:
    """
    Section 7.15.2 Step 2: "creates the Stripe customer in test mode". The
    idempotency key is the tenant id, so a retried creation cannot make two
    customers for one tenant.
    """
    response = httpx.post(
        "https://api.stripe.com/v1/customers",
        auth=(_stripe_key(), ""),
        data={"name": name, "email": email, "metadata[docflow_tenant_id]": str(tenant_id)},
        headers={"Idempotency-Key": f"docflow-customer-{tenant_id}"},
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code >= 300:
        raise ExternalServiceError("stripe", f"customer create returned {response.status_code}")
    return str(response.json()["id"])


def delete_stripe_customer(customer_id: str) -> None:
    """Undo a creation whose tenant transaction then failed (Step 2's "any
    failure rolls back everything"). Best effort; the caller logs failure."""
    response = httpx.delete(
        f"https://api.stripe.com/v1/customers/{customer_id}",
        auth=(_stripe_key(), ""),
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code >= 300 and response.status_code != 404:
        raise ExternalServiceError("stripe", f"customer delete returned {response.status_code}")


@dataclass(frozen=True)
class SubscriptionResult:
    subscription_id: str
    status: str
    current_period_end: int | None  # unix seconds, as Stripe gives it


def _cents(amount: Decimal) -> int:
    """Money to Stripe's integer cents. Refuses anything that isn't a whole
    number of cents rather than rounding it (Section 7: money is never
    approximated)."""
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise ExternalServiceError("stripe", "amount has fractions of a cent")
    return int(cents)


def _stripe(method: str, path: str, *, data: dict | None = None, params: dict | None = None,
            idempotency_key: str | None = None) -> httpx.Response:
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    return httpx.request(
        method,
        f"https://api.stripe.com/v1/{path}",
        auth=(_stripe_key(), ""),
        data=data,
        params=params,
        headers=headers,
        timeout=TIMEOUT_SECONDS,
    )


def _ensure(path: str, object_id: str, data: dict) -> None:
    """Create a Stripe object with a fixed id, or accept that it exists."""
    response = _stripe("POST", path, data={"id": object_id, **data})
    if response.status_code < 300:
        return
    error_code = response.json().get("error", {}).get("code") if response.status_code == 400 else None
    if error_code == "resource_already_exists":
        return
    raise ExternalServiceError("stripe", f"{path} create returned {response.status_code}")


def start_subscription(
    *,
    tenant_id: UUID,
    customer_id: str,
    tier_id: UUID,
    tier_name: str,
    monthly_price: Decimal,
    promo_monthly_price: Decimal | None,
    promo_months: int | None,
    setup_fee: Decimal | None,
    days_until_due: int,
) -> SubscriptionResult:
    """
    Go-live billing (Section 7.15.2 Step 9; D-113): a monthly subscription at
    the tier's price, invoiced to the customer (collection_method
    send_invoice) because nobody has entered a card yet; the setup fee, when
    billed through Stripe, as a pending invoice item that Stripe puts on that
    first invoice; the founding price as a coupon for the promo months.

    Safe to retry at any time -- a go-live whose database transaction failed
    after Stripe succeeded must never bill twice:
      * the product and coupon have fixed ids per tier version;
      * the setup fee is added only if no pending one for this tenant exists;
      * an existing live subscription for this tenant is returned, not duplicated;
      * every create also carries an idempotency key.
    Prices come from the tiers table the caller read -- never typed in here.
    """
    product_id = f"docflow_tier_{tier_id.hex}"
    _ensure(
        "products",
        product_id,
        {"name": f"DocFlow {tier_name}", "metadata[docflow_tier_id]": str(tier_id)},
    )

    coupon_id = None
    if promo_monthly_price is not None and promo_months:
        coupon_id = f"docflow_founding_{tier_id.hex}"
        _ensure(
            "coupons",
            coupon_id,
            {
                "name": f"DocFlow founding price ({tier_name})",
                "amount_off": _cents(monthly_price - promo_monthly_price),
                "currency": "usd",
                "duration": "repeating",
                "duration_in_months": promo_months,
            },
        )

    existing = _stripe(
        "GET", "subscriptions", params={"customer": customer_id, "status": "all", "limit": 20}
    )
    if existing.status_code >= 300:
        raise ExternalServiceError("stripe", f"subscription list returned {existing.status_code}")
    for sub in existing.json().get("data", []):
        if sub.get("metadata", {}).get("docflow_tenant_id") == str(tenant_id) and sub.get("status") not in (
            "canceled",
            "incomplete_expired",
        ):
            return _subscription_result(sub)

    if setup_fee is not None and setup_fee > 0:
        pending = _stripe(
            "GET", "invoiceitems", params={"customer": customer_id, "pending": "true", "limit": 50}
        )
        if pending.status_code >= 300:
            raise ExternalServiceError("stripe", f"invoice item list returned {pending.status_code}")
        already = any(
            item.get("metadata", {}).get("docflow_setup_fee_for") == str(tenant_id)
            for item in pending.json().get("data", [])
        )
        if not already:
            created = _stripe(
                "POST",
                "invoiceitems",
                data={
                    "customer": customer_id,
                    "amount": _cents(setup_fee),
                    "currency": "usd",
                    "description": "DocFlow setup fee",
                    "metadata[docflow_setup_fee_for]": str(tenant_id),
                },
                idempotency_key=f"docflow-setupfee-{tenant_id}",
            )
            if created.status_code >= 300:
                raise ExternalServiceError("stripe", f"invoice item create returned {created.status_code}")

    data = {
        "customer": customer_id,
        "collection_method": "send_invoice",
        "days_until_due": days_until_due,
        "items[0][price_data][currency]": "usd",
        "items[0][price_data][product]": product_id,
        "items[0][price_data][unit_amount]": _cents(monthly_price),
        "items[0][price_data][recurring][interval]": "month",
        "metadata[docflow_tenant_id]": str(tenant_id),
    }
    if coupon_id:
        data["discounts[0][coupon]"] = coupon_id
    response = _stripe(
        "POST", "subscriptions", data=data, idempotency_key=f"docflow-subscription-{tenant_id}"
    )
    if response.status_code >= 300:
        raise ExternalServiceError("stripe", f"subscription create returned {response.status_code}")
    return _subscription_result(response.json())


def _subscription_result(sub: dict) -> SubscriptionResult:
    # Newer Stripe API versions moved the period onto the subscription item.
    period_end = sub.get("current_period_end")
    if period_end is None:
        items = sub.get("items", {}).get("data", [])
        period_end = items[0].get("current_period_end") if items else None
    return SubscriptionResult(
        subscription_id=str(sub["id"]), status=str(sub["status"]), current_period_end=period_end
    )


# ── Supabase Auth (admin API, service role key) ─────────────────────────────


@dataclass(frozen=True)
class InviteLink:
    auth_user_id: UUID
    url: str


def generate_invite_link(*, email: str, redirect_to: str) -> InviteLink:
    """
    A one-time "set your password" link for an invited user, WITHOUT
    Supabase sending its own email: Supabase's built-in mail only reaches the
    project's own team, so DocFlow sends the link through its outbox (D-105).

    First invite: type `invite` creates the Supabase Auth user. A resend for
    someone who already has an Auth user: type `recovery`, which lands on the
    same set-password page.
    """
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise ExternalServiceError("supabase", "SUPABASE_URL / SERVICE_ROLE_KEY not set")
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }
    url = f"{settings.supabase_url}/auth/v1/admin/generate_link"

    response = None
    for link_type in ("invite", "recovery"):
        response = httpx.post(
            url,
            headers=headers,
            json={"type": link_type, "email": email, "redirect_to": redirect_to},
            timeout=TIMEOUT_SECONDS,
        )
        if response.status_code < 300:
            body = response.json()
            user = body.get("user") or body
            action_link = body.get("action_link") or (body.get("properties") or {}).get("action_link")
            if not action_link or not user.get("id"):
                raise ExternalServiceError("supabase", "generate_link response had no link")
            return InviteLink(auth_user_id=UUID(user["id"]), url=action_link)
        # 422: the address already has an Auth user -- try a recovery link.
        if response.status_code != 422:
            break
    status = response.status_code if response is not None else "none"
    raise ExternalServiceError("supabase", f"generate_link returned {status}")
