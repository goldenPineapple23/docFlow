"""
The single tenant-scoped data-access layer (CLAUDE.md Section 7.5).

Every query the tenant surface makes goes through `tenant_session()`. It never
accepts a tenant_id from the caller's request data -- only from the already-
authenticated `AuthenticatedUser` the auth dependency produced. Inside the
transaction it sets a Postgres session variable (`app.tenant_id`) that every
tenant-scoped table's RLS policy checks, so isolation is enforced by the
database, not just by application code:

    ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON <table>
      USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

Both this module and `admin_data_access.py` connect as the same Postgres
role (`docflow_app`, created with NOBYPASSRLS -- see SETUP.md / DECISIONS.md
D-013), so RLS is genuinely enforced rather than bypassed. Cross-tenant
reads still need to get past that same RLS, which is why every tenant-scoped
table's policy is written as an OR of two conditions -- the tenant match
above, and:

    CREATE POLICY platform_admin_access ON <table>
      USING (current_setting('app.is_platform_admin', true) = 'true');

`platform_session()` below sets that second flag. Since Postgres RLS
policies are permissive-OR'd, a row is visible if either policy matches.
The flag is only ever set from this one function, which is only ever called
from `admin_data_access.py` -- the same import boundary enforced for the
Python layer (Section 7.15.1) now also has a matching database-level
control, not just an honor system.

A third, narrower case: before a request even has a resolved tenant_id, the
auth layer (app/deps.py) needs to look up "which user is this auth token
for" -- that's not tenant access and it isn't admin access either, so it
gets its own minimal policy instead of borrowing the admin bypass for
something that isn't an admin action:

    CREATE POLICY self_lookup ON users
      USING (auth_user_id = current_setting('app.auth_user_id', true)::uuid);

`identity_lookup_session()` sets that variable and nothing else -- it can
never see another user's row, let alone another tenant's data.

There is no raw query path in this codebase that bypasses this module for
tenant-scoped tables. The only exception is `admin_data_access.py`, which is
a separate, explicitly named, import-restricted module (Section 7.15.1).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
from uuid import UUID

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from docflow_core.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        if not settings.database_url:
            raise RuntimeError(
                "DATABASE_URL is not set. See SETUP.md Step 1 -- copy the pooled "
                "connection string from Supabase Project Settings -> Database."
            )
        _engine = create_engine(settings.database_url, pool_pre_ping=True)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


@contextmanager
def tenant_session(tenant_id: UUID) -> Iterator[Session]:
    """
    Open a transaction scoped to exactly one tenant. `tenant_id` must come
    from the authenticated session (see app/deps.py), never from a request
    body, query string, or client-side state (CLAUDE.md Section 10).
    """
    session_factory = get_session_factory()
    session = session_factory()
    try:
        session.execute(text("SET LOCAL app.tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def platform_session() -> Iterator[Session]:
    """
    A transaction with no single tenant context -- used for genuinely
    global tables (platform_admins, admin_actions, onboarding_intakes,
    tiers, founder_alerts) and, exclusively from admin_data_access.py, for
    cross-tenant reads/writes on tenant-scoped tables. Sets
    `app.is_platform_admin`, which the `platform_admin_access` RLS policy on
    every tenant-scoped table checks (see module docstring). This function
    is only ever called from admin_data_access.py -- never import it
    directly to "get around" tenant_session() elsewhere.
    """
    session_factory = get_session_factory()
    session = session_factory()
    try:
        session.execute(text("SET LOCAL app.is_platform_admin = 'true'"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def identity_lookup_session(auth_user_id: str) -> Iterator[Session]:
    """
    Used only by app/deps.py to resolve a verified Supabase auth_user_id to
    its local `users` row (and, separately, to check `platform_admins`,
    which has no RLS -- see the module docstring). Grants visibility into
    exactly one row: the caller's own.
    """
    session_factory = get_session_factory()
    session = session_factory()
    try:
        session.execute(text("SET LOCAL app.auth_user_id = :auth_user_id"), {"auth_user_id": auth_user_id})
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
