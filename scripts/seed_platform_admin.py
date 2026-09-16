"""
One-time script: creates (if needed) a Supabase Auth account for the founder
and grants platform-admin status to it.

CLAUDE.md Section 7.15.1: "No API can insert into [platform_admins]; it is
seeded by migration or by a documented CLI command." RUNBOOK.md (the fuller
operational doc this really belongs in) isn't due until Phase 6, but Phase 0
needs a working platform_admin to test the Console against -- so this script
is that "documented CLI command" for now, and its usage note gets folded
into RUNBOOK.md once that exists.

There is deliberately no public /signup route (CLAUDE.md Section 3), and the
login page only signs in an existing account -- so the founder's own first
account can't be created that way either. This script uses the Supabase
Admin API (the service role key, never exposed to any app route) to create
that Supabase Auth account directly, exactly like it would for a tenant
invite, and links it to a platform-admin-only `users` row.

Usage:

    python scripts/seed_platform_admin.py you@example.com [password]

If you omit the password, one is generated and printed once -- change it
after your first login.

This is NOT reachable from any API route -- it connects directly to the
database with the same docflow_app credentials as the app itself, and to
Supabase Auth with the service role key, run by hand, by the founder, once.
"""

from __future__ import annotations

import secrets
import sys
from uuid import uuid4

import httpx
from sqlalchemy import text

from docflow_core.config import get_settings
from docflow_core.db import platform_session


def _get_or_create_auth_user(email: str, password: str | None) -> str:
    """Returns the Supabase Auth user's id, creating the account if needed."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        sys.exit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env -- see SETUP.md Step 1.")

    admin_users_url = f"{settings.supabase_url}/auth/v1/admin/users"
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }

    resp = httpx.get(admin_users_url, headers=headers, params={"email": email}, timeout=10)
    resp.raise_for_status()
    for existing in resp.json().get("users", []):
        if existing.get("email", "").lower() == email.lower():
            print(f"Found existing Supabase Auth account for {email}.")
            return existing["id"]

    generated = password is None
    password = password or secrets.token_urlsafe(18)
    resp = httpx.post(
        admin_users_url,
        headers=headers,
        json={"email": email, "password": password, "email_confirm": True},
        timeout=10,
    )
    resp.raise_for_status()
    if generated:
        print(f"Created a new Supabase Auth account for {email} with generated password: {password}")
        print("Log in with it once, then change it -- this is shown only here, it isn't stored anywhere.")
    else:
        print(f"Created a new Supabase Auth account for {email}.")
    return resp.json()["id"]


def main() -> None:
    if len(sys.argv) not in (2, 3):
        sys.exit("Usage: python scripts/seed_platform_admin.py <your-email> [password]")
    email = sys.argv[1]
    password = sys.argv[2] if len(sys.argv) == 3 else None

    auth_user_id = _get_or_create_auth_user(email, password)

    with platform_session() as session:
        user_row = session.execute(
            text("SELECT id, auth_user_id FROM users WHERE email = :email AND tenant_id IS NULL"),
            {"email": email},
        ).mappings().first()

        if user_row is None:
            user_id = uuid4()
            session.execute(
                text(
                    """
                    INSERT INTO users (id, tenant_id, email, role, is_active, auth_user_id)
                    VALUES (:id, NULL, :email, 'owner', true, :auth_user_id)
                    """
                ),
                {"id": str(user_id), "email": email, "auth_user_id": auth_user_id},
            )
            print(f"Created platform-admin-only users row for {email} ({user_id}).")
        else:
            user_id = user_row["id"]
            if str(user_row["auth_user_id"]) != auth_user_id:
                session.execute(
                    text("UPDATE users SET auth_user_id = :auth_user_id WHERE id = :id"),
                    {"auth_user_id": auth_user_id, "id": str(user_id)},
                )
            print(f"Found existing users row for {email} ({user_id}).")

        already_admin = session.execute(
            text("SELECT 1 FROM platform_admins WHERE user_id = :user_id AND revoked_at IS NULL"),
            {"user_id": str(user_id)},
        ).first()
        if already_admin:
            print(f"{email} is already a platform admin.")
            return

        session.execute(
            text("INSERT INTO platform_admins (user_id, granted_by) VALUES (:user_id, :user_id)"),
            {"user_id": str(user_id)},
        )
        print(f"Granted platform-admin status to {email}.")


if __name__ == "__main__":
    main()
