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
