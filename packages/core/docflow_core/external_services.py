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

import calendar
import functools
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from typing import Any
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


@functools.lru_cache(maxsize=4)
def _stripe_account_id(key: str) -> str | None:
    """Which Stripe account the key belongs to, asked once per key and process.
    A sandbox is an account of its own, so a dashboard link without the
    account opens whichever account the browser last used -- where the
    customer does not exist (found in the 5.9 walkthrough). None if Stripe
    can't be asked; the link then falls back to the unscoped form."""
    try:
        response = httpx.get("https://api.stripe.com/v1/account", auth=(key, ""), timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return None
    if response.status_code >= 300:
        return None
    account = response.json().get("id")
    return str(account) if account else None


def stripe_dashboard_url(customer_id: str | None) -> str | None:
    """A link to this customer in Stripe (Section 7.15.3), scoped to the
    account DocFlow's key belongs to, and to test mode when the key is a test
    key."""
    if not customer_id:
        return None
    key = get_settings().stripe_secret_key
    test = key.startswith(("sk_test_", "rk_test_"))
    account = _stripe_account_id(key) if key else None
    scope = f"/{account}" if account else ""
    return f"https://dashboard.stripe.com{scope}{'/test' if test else ''}/customers/{customer_id}"


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
    # When the founding discount stops applying (unix seconds), or None when
    # there is none (D-139). Read from Stripe's own answer.
    founding_ends_at: int | None = None


def _cents(amount: Decimal) -> int:
    """Money to Stripe's integer cents. Refuses anything that isn't a whole
    number of cents rather than rounding it (Section 7: money is never
    approximated)."""
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise ExternalServiceError("stripe", "amount has fractions of a cent")
    return int(cents)


def _stripe(
    method: str,
    path: str,
    *,
    data: dict | None = None,
    params: dict | None = None,
    idempotency_key: str | None = None,
) -> httpx.Response:
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


# Stripe refuses a coupon or product name longer than this (found in the 5.9
# walkthrough: a 47-character coupon name made a real plan change fail).
STRIPE_NAME_LIMIT = 40


def stripe_name(name: str) -> str:
    return name if len(name) <= STRIPE_NAME_LIMIT else name[: STRIPE_NAME_LIMIT - 1] + "…"


def _stripe_error(response: httpx.Response) -> str:
    """Which of Stripe's rules was broken, for the founder's log: its error
    code and the parameter it objected to -- never its message text, which can
    echo what was sent (card details, for instance). "parameter_invalid_string
    name" is enough to find a 40-character limit."""
    try:
        error = response.json().get("error", {})
    except ValueError:
        return ""
    return " ".join(str(error[k]) for k in ("code", "param") if error.get(k))


def _ensure(path: str, object_id: str, data: dict) -> None:
    """Create a Stripe object with a fixed id, or accept that it exists."""
    if "name" in data:
        data = {**data, "name": stripe_name(str(data["name"]))}
    response = _stripe("POST", path, data={"id": object_id, **data})
    if response.status_code < 300:
        return
    error_code = response.json().get("error", {}).get("code") if response.status_code == 400 else None
    if error_code == "resource_already_exists":
        return
    raise ExternalServiceError(
        "stripe", f"{path} create returned {response.status_code}: {_stripe_error(response)}"
    )


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
    trial_end: int | None = None,
) -> SubscriptionResult:
    """
    Go-live billing (Section 7.15.2 Step 9; D-113): a monthly subscription at
    the tier's price, invoiced to the customer (collection_method
    send_invoice) because nobody has entered a card yet; the setup fee, when
    billed through Stripe, as a pending invoice item that Stripe puts on that
    first invoice; the founding price as a coupon for the promo months.

    `trial_end` (unix seconds, D-125): delays that first invoice. Stripe
    generates no invoice at all for a trialing send_invoice subscription
    until the trial ends, so the pending setup-fee item added below simply
    waits, untouched, and is swept onto the same invoice as the first
    month's charge the moment the trial ends -- one invoice, not two. Pass
    None for an immediate first invoice (reactivation, D-123).

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

    existing = _stripe("GET", "subscriptions", params={"customer": customer_id, "status": "all", "limit": 20})
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
        # The founding discount's end date comes back in the answer (D-139).
        "expand[]": "discounts",
    }
    if coupon_id:
        data["discounts[0][coupon]"] = coupon_id
    if trial_end:
        data["trial_end"] = trial_end
    response = _stripe(
        "POST", "subscriptions", data=data, idempotency_key=f"docflow-subscription-{tenant_id}"
    )
    if response.status_code >= 300:
        raise ExternalServiceError("stripe", f"subscription create returned {response.status_code}")
    return _subscription_result(response.json())


# Stripe's own "about a month": its repeating coupons count billing months, and
# DocFlow bills monthly, so this only has to turn "time left" into "invoices
# left", rounding up so a customer never loses a founding month to arithmetic.
_DAYS_PER_MONTH = Decimal("30.44")


def _add_months(ts: int, months: int) -> int:
    """`ts` moved on by whole calendar months, as Stripe's monthly billing does
    (the 31st becomes the last day of a shorter month)."""
    at = datetime.fromtimestamp(ts, timezone.utc)
    index = at.month - 1 + months
    year, month = at.year + index // 12, index % 12 + 1
    day = min(at.day, calendar.monthrange(year, month)[1])
    return int(at.replace(year=year, month=month, day=day).timestamp())


def founding_months_left(discount_end: int | None, now: int, next_invoice_at: int | None = None) -> int:
    """How many more invoices the founding price is owed on (D-138): the
    monthly invoices from the next one onward that fall before the founding
    discount's end. A Stripe coupon repeating for that many months, applied
    now, discounts exactly those invoices and no more.

    Counting invoices, not time, matters: a customer who already had one
    founding invoice must not get a fourth because a plan change rounded the
    time left up (found in the 5.9 walkthrough: 19 Sep, 19 Oct, 19 Nov were
    owed; rounding time gave 19 Dec too). Without the next invoice date, falls
    back to whole months of time left, rounded up."""
    if not discount_end or discount_end <= now:
        return 0
    if next_invoice_at:
        count = 0
        while _add_months(next_invoice_at, count) < discount_end:
            count += 1
        return count
    days = Decimal(discount_end - now) / Decimal(86400)
    return int((days / _DAYS_PER_MONTH).to_integral_value(rounding=ROUND_CEILING))


def _founding_discount_end(sub: dict) -> int | None:
    """When the founding coupon on this subscription stops applying, or None.
    Newer API versions list `discounts`; older ones have a single `discount`.

    Where the coupon id sits also changed: older versions put the coupon object
    (or its id) in `coupon`; from the 2025 versions on -- including the
    `2026-08-26.dahlia` this account uses -- it is `source.coupon`, with
    `source.type == "coupon"`. Missing that once left a Growth founding coupon
    on a Starter price in the test sandbox (found in the 5.9 walkthrough, D-138),
    so every shape is read."""
    candidates = [d for d in sub.get("discounts") or [] if isinstance(d, dict)]
    if isinstance(sub.get("discount"), dict):
        candidates.append(sub["discount"])
    for discount in candidates:
        if str(_coupon_id(discount) or "").startswith("docflow_founding_"):
            return discount.get("end")
    return None


def _coupon_id(discount: dict) -> str | None:
    for holder in (discount, discount.get("source") or {}):
        if not isinstance(holder, dict):
            continue
        coupon = holder.get("coupon")
        if isinstance(coupon, dict) and coupon.get("id"):
            return str(coupon["id"])
        if isinstance(coupon, str) and coupon:
            return coupon
    return None


def change_subscription_tier(
    *,
    tenant_id: UUID,
    subscription_id: str,
    tier_id: UUID,
    tier_name: str,
    monthly_price: Decimal,
    promo_monthly_price: Decimal | None,
    founding: bool,
    now: int,
) -> tuple[SubscriptionResult, int]:
    """
    A plan change for a live customer (Section 7.16.1; slice 5.9, D-138):
    the subscription's one item moves to the new tier's price, with Stripe's
    default proration, so the difference lands on the next invoice. Returns the
    subscription and how many founding months carried over.

    A founding customer keeps a founding price -- the NEW tier's -- for the
    months their founding period has left (the founder's option (a)). Stripe
    holds when that period ends, so it is read from the subscription, never
    guessed. If the new tier has no founding price, or the period is over, the
    subscription ends up with no founding discount.
    """
    current = _stripe("GET", f"subscriptions/{subscription_id}", params={"expand[]": "discounts"})
    if current.status_code >= 300:
        raise ExternalServiceError("stripe", f"subscription read returned {current.status_code}")
    sub = current.json()
    items = sub.get("items", {}).get("data", [])
    if not items:
        raise ExternalServiceError("stripe", "subscription has no item to change")

    product_id = f"docflow_tier_{tier_id.hex}"
    _ensure(
        "products",
        product_id,
        {"name": f"DocFlow {tier_name}", "metadata[docflow_tier_id]": str(tier_id)},
    )

    # The next invoice: the end of the current period (the first invoice, for
    # a subscription still in its free week).
    next_invoice_at = _subscription_result(sub).current_period_end
    months = founding_months_left(_founding_discount_end(sub), now, next_invoice_at) if founding else 0
    data: dict[str, Any] = {
        "items[0][id]": items[0]["id"],
        "items[0][price_data][currency]": "usd",
        "items[0][price_data][product]": product_id,
        "items[0][price_data][unit_amount]": _cents(monthly_price),
        "items[0][price_data][recurring][interval]": "month",
        "proration_behavior": "create_prorations",
        "metadata[docflow_tier_id]": str(tier_id),
        "expand[]": "discounts",
    }
    if founding and months > 0 and promo_monthly_price is not None:
        coupon_id = f"docflow_founding_{tier_id.hex}_{months}m"
        _ensure(
            "coupons",
            coupon_id,
            {
                "name": f"DocFlow founding: {tier_name} ({months} mo left)",
                "amount_off": _cents(monthly_price - promo_monthly_price),
                "currency": "usd",
                "duration": "repeating",
                "duration_in_months": months,
            },
        )
        # Replaces the old tier's founding coupon rather than stacking on it.
        data["discounts[0][coupon]"] = coupon_id
    elif _founding_discount_end(sub) is not None:
        # A founding coupon from the old tier must not keep discounting a price
        # it was never sized for (option (c), rejected).
        data["discounts"] = ""
        months = 0

    response = _stripe(
        "POST",
        f"subscriptions/{subscription_id}",
        data=data,
        idempotency_key=f"docflow-tierchange-{tenant_id}-{tier_id}",
    )
    if response.status_code >= 300:
        raise ExternalServiceError(
            "stripe", f"subscription update returned {response.status_code}: {_stripe_error(response)}"
        )
    return _subscription_result(response.json()), months


def cancel_subscription(subscription_id: str) -> None:
    """
    Section 7.14: "Cancel recurring billing at the Stripe level (not just
    internally) so no further charge occurs" -- on a tenant entering
    suspended. Idempotent: Stripe 404s a subscription that's already
    canceled or gone, which is treated as success, not an error.
    """
    response = _stripe("DELETE", f"subscriptions/{subscription_id}")
    if response.status_code >= 300 and response.status_code != 404:
        raise ExternalServiceError("stripe", f"subscription cancel returned {response.status_code}")


def void_pending_setup_fee(*, customer_id: str, tenant_id: UUID) -> None:
    """
    Cleanup for a tenant cancelled during its trial (D-125): the setup-fee
    invoice item added at go-live never got swept onto an invoice, because
    the trial ended in a cancellation instead of a bill. Left alone it would
    sit pending against the customer and could land on some unrelated
    future invoice. Idempotent -- nothing pending for this tenant is a
    no-op, and an item already invoiced (the normal case) is no longer
    pending and so isn't touched.
    """
    pending = _stripe("GET", "invoiceitems", params={"customer": customer_id, "pending": "true", "limit": 50})
    if pending.status_code >= 300:
        raise ExternalServiceError("stripe", f"invoice item list returned {pending.status_code}")
    for item in pending.json().get("data", []):
        if item.get("metadata", {}).get("docflow_setup_fee_for") != str(tenant_id):
            continue
        response = _stripe("DELETE", f"invoiceitems/{item['id']}")
        if response.status_code >= 300 and response.status_code != 404:
            raise ExternalServiceError("stripe", f"invoice item delete returned {response.status_code}")


def verify_webhook_signature(
    payload: bytes, sig_header: str, secret: str, *, tolerance_seconds: int = 300
) -> dict:
    """
    Stripe's documented signature scheme (no `stripe` SDK dependency in this
    codebase -- see the module docstring): the header is
    `t=<timestamp>,v1=<hex hmac-sha256 of "<timestamp>.<payload>">`, at
    least one `v1` must match, and the timestamp must be recent -- both
    checks so a captured event can't be replayed later (Section 7.12:
    document/webhook content is untrusted until proven otherwise).
    """
    import hashlib
    import hmac
    import json
    import time

    timestamp: str | None = None
    signatures: list[str] = []
    for item in sig_header.split(","):
        key, _, value = item.partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    if not timestamp or not signatures:
        raise ExternalServiceError("stripe", "webhook signature header malformed")
    if abs(time.time() - int(timestamp)) > tolerance_seconds:
        raise ExternalServiceError("stripe", "webhook signature timestamp outside tolerance")
    expected = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + payload, hashlib.sha256
    ).hexdigest()
    if not any(hmac.compare_digest(expected, sig) for sig in signatures):
        raise ExternalServiceError("stripe", "webhook signature mismatch")
    return json.loads(payload)


def _subscription_result(sub: dict) -> SubscriptionResult:
    # Newer Stripe API versions moved the period onto the subscription item.
    period_end = sub.get("current_period_end")
    if period_end is None:
        items = sub.get("items", {}).get("data", [])
        period_end = items[0].get("current_period_end") if items else None
    return SubscriptionResult(
        subscription_id=str(sub["id"]),
        status=str(sub["status"]),
        current_period_end=period_end,
        founding_ends_at=_founding_discount_end(sub),
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
