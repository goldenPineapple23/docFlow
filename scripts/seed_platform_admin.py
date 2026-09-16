"""
One-time script: grants platform-admin status to the founder's own account.

CLAUDE.md Section 7.15.1: "No API can insert into [platform_admins]; it is
seeded by migration or by a documented CLI command." RUNBOOK.md (the fuller
operational doc this really belongs in) isn't due until Phase 6, but Phase 0
needs a working platform_admin to test the Console against -- so this script
is that "documented CLI command" for now, and its usage note gets folded
into RUNBOOK.md once that exists.

Usage (after you've signed up once through the app's normal login flow, so
a Supabase Auth user exists for your email):

    python scripts/seed_platform_admin.py you@example.com

This is NOT reachable from any API route -- it connects directly to the
database with the same docflow_app credentials as the app itself, run by
hand, by the founder, once.
"""

from __future__ import annotations

import sys
from uuid import uuid4

from sqlalchemy import text

from docflow_core.db import platform_session


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/seed_platform_admin.py <your-email>")
    email = sys.argv[1]

    with platform_session() as session:
        user_row = session.execute(
            text("SELECT id FROM users WHERE email = :email AND tenant_id IS NULL"),
            {"email": email},
        ).mappings().first()

        if user_row is None:
            user_id = uuid4()
            session.execute(
                text(
                    """
                    INSERT INTO users (id, tenant_id, email, role, is_active)
                    VALUES (:id, NULL, :email, 'owner', true)
                    """
                ),
                {"id": str(user_id), "email": email},
            )
            print(f"Created platform-admin-only users row for {email} ({user_id}).")
            print(
                "Sign in once through Supabase Auth with this email, then update this row's "
                "auth_user_id to match (see SETUP.md) -- until then this account can't log in."
            )
        else:
            user_id = user_row["id"]
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
