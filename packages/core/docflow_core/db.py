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
      USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

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
      USING (auth_user_id = nullif(current_setting('app.auth_user_id', true), '')::uuid);

`identity_lookup_session()` sets that variable and nothing else -- it can
never see another user's row, let alone another tenant's data.

There is no raw query path in this codebase that bypasses this module for
tenant-scoped tables. The only exception is `admin_data_access.py`, which is
a separate, explicitly named, import-restricted module (Section 7.15.1).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, cast
from uuid import UUID

import psycopg
from psycopg.types.json import JsonbBinaryDumper, JsonbDumper
from sqlalchemy import CursorResult, Engine, Result, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# psycopg3 doesn't know a bare Python dict/list should serialize as jsonb --
# every jsonb column in this schema (admin_actions.payload,
# tenant_lifecycle_events.payload, etc.) is written from a plain dict, so
# register this globally once rather than wrapping every call site in Jsonb().
psycopg.adapters.register_dumper(dict, JsonbDumper)
psycopg.adapters.register_dumper(dict, JsonbBinaryDumper)
psycopg.adapters.register_dumper(list, JsonbDumper)
psycopg.adapters.register_dumper(list, JsonbBinaryDumper)

from docflow_core.config import get_settings

_engine: Engine | None = None


def rowcount(result: Result[Any]) -> int:
    """Rows affected by an INSERT/UPDATE/DELETE. `Session.execute` is typed as
    returning `Result`, but a DML statement always returns a `CursorResult`,
    which is what carries `rowcount`."""
    return cast(CursorResult[Any], result).rowcount

_SessionLocal: sessionmaker[Session] | None = None


def _psycopg3_url(database_url: str) -> str:
    """
    Supabase's connection strings use the bare "postgresql://" scheme, which
    makes SQLAlchemy default to the psycopg2 dialect. We depend on psycopg
    (v3, see pyproject.toml) instead, so force that dialect explicitly rather
    than requiring everyone pasting a Supabase URL into .env to remember to
    edit the scheme too.
    """
    if database_url.startswith("postgresql+"):
        return database_url
    return database_url.replace("postgresql://", "postgresql+psycopg://", 1)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        if not settings.database_url:
            raise RuntimeError(
                "DATABASE_URL is not set. See SETUP.md Step 1 -- copy the pooled "
                "connection string from Supabase Project Settings -> Database."
            )
        _engine = create_engine(
            _psycopg3_url(settings.database_url),
            pool_pre_ping=True,
            # DATABASE_URL is Supabase's transaction-mode pooler (port 6543):
            # a given logical "connection" can be handed a different backend
            # Postgres connection between transactions. Server-side prepared
            # statements (which psycopg3 creates automatically after a query
            # shape repeats -- the default prepare_threshold) are tied to one
            # specific backend connection, so they're unsafe under this kind
            # of pooling. Supabase's own docs recommend disabling them for
            # exactly this reason; see also _reset_rls_settings below for the
            # other pooling-specific defense this connection needs.
            connect_args={"prepare_threshold": None},
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def _reset_rls_settings(session: Session) -> None:
    """
    Reset every app.* RLS setting to its unset (NULL) state at the
    start of every transaction, before setting whichever one this
    transaction actually needs.

    This matters because DATABASE_URL is Supabase's transaction-mode pooler
    (port 6543): each transaction can be handed a different backend Postgres
    connection than the last, potentially one still carrying leftover
    app.* state from a different transaction entirely. `set_config(..., true)`
    ("local") settings are transaction-scoped and undone on COMMIT/ROLLBACK,
    so nothing WE set here can leak -- but nothing guarantees the connection
    we're handed hasn't picked up a stray committed value some other way. A
    stray non-NULL app.tenant_id or app.is_platform_admin would be a tenant-
    isolation bypass (CLAUDE.md Section 7.5, "the one thing that cannot ever
    fail"), so we defensively RESET (not just skip) every setting this
    transaction doesn't explicitly set, rather than trusting the connection's
    ambient state. Plain RESET (not RESET ... LOCAL, which doesn't exist) is
    itself transactional -- undone on ROLLBACK, and safely re-applied by the
    next transaction on COMMIT -- so this is correct under pooling either way.
    """
    session.execute(text("RESET app.tenant_id"))
    session.execute(text("RESET app.auth_user_id"))
    session.execute(text("RESET app.is_platform_admin"))
    session.execute(text("RESET app.intake_token"))
    session.execute(text("RESET app.scheduler"))
    session.execute(text("RESET app.rollup"))
    session.execute(text("RESET app.lifecycle"))
    session.execute(text("RESET app.pipeline_sweep"))
    session.execute(text("RESET app.stripe_webhook"))


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
        _reset_rls_settings(session)
        # Postgres's SET/SET LOCAL grammar takes a literal, not a bind parameter --
        # set_config() is a plain function call, so it can be parameterized safely.
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
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
        _reset_rls_settings(session)
        session.execute(text("SET LOCAL app.is_platform_admin = 'true'"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def token_lookup_session(token: str) -> Iterator[Session]:
    """
    Used only by the email-intake webhook (app/routers/email_intake.py) to
    resolve a per-tenant intake token to its tenant_id before any tenant_id
    is known -- there is no tenant context yet at this point in the request,
    so neither tenant_session() nor platform_session() applies. Mirrors
    identity_lookup_session()'s narrow shape: this grants visibility into
    exactly one intake_addresses row (the one matching this exact token, per
    the `token_lookup` RLS policy added in
    supabase/migrations/0003_email_intake.sql), and nothing else. This is
    not the Section 7.15.1 admin bypass and must never be used as one.
    """
    session_factory = get_session_factory()
    session = session_factory()
    try:
        _reset_rls_settings(session)
        session.execute(
            text("SELECT set_config('app.intake_token', :token, true)"),
            {"token": token},
        )
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
        _reset_rls_settings(session)
        session.execute(
            text("SELECT set_config('app.auth_user_id', :auth_user_id, true)"),
            {"auth_user_id": auth_user_id},
        )
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def rollup_session() -> Iterator[Session]:
    """
    Used only by the nightly metrics rollup (`docflow_core.metrics`) to list
    the tenants it must roll up and to record the run in `rollup_runs`
    (migration 0017's `rollup_read` / `rollup_access` policies). It can read
    the `tenants` table and write `rollup_runs`, and nothing else -- every
    number is computed inside that tenant's own tenant_session(). Like the
    scheduler and the token lookups, this is not the Section 7.15.1 admin
    bypass: it cannot read a document, a line or a customer.
    """
    session_factory = get_session_factory()
    session = session_factory()
    try:
        _reset_rls_settings(session)
        session.execute(text("SET LOCAL app.rollup = 'true'"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def scheduler_session() -> Iterator[Session]:
    """
    Used only by `docflow_core.scheduled_jobs` to find and claim due rows in
    `scheduled_jobs` across tenants (migration 0013's `scheduler_access`
    policy). It sees that one table and nothing else -- every job then runs
    in a tenant_session() for its own tenant. Like the token and identity
    lookups, this is not the Section 7.15.1 admin bypass.
    """
    session_factory = get_session_factory()
    session = session_factory()
    try:
        _reset_rls_settings(session)
        session.execute(text("SET LOCAL app.scheduler = 'true'"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def stripe_webhook_session() -> Iterator[Session]:
    """
    Used only by the Stripe webhook handler (app/routers/stripe_webhooks.py)
    to record the event id for idempotency (migration 0019's `webhook_access`
    policy on `stripe_webhook_events`) and to look up which tenant a
    `stripe_customer_id`/`stripe_subscription_id` belongs to (`
    stripe_webhook_lookup`, SELECT only). The actual update to that tenant's
    subscription status happens in a normal tenant_session() once the
    tenant_id is known. Not the Section 7.15.1 admin bypass.
    """
    session_factory = get_session_factory()
    session = session_factory()
    try:
        _reset_rls_settings(session)
        session.execute(text("SET LOCAL app.stripe_webhook = 'true'"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def pipeline_sweep_session() -> Iterator[Session]:
    """
    Used only by the stuck-document sweep (`docflow_core.stuck_documents`,
    Section 7.9) to list the tenants it must look at (migration 0027's
    `pipeline_sweep_read` policy). It can SELECT `tenants` and nothing else;
    each tenant's documents are then read and changed in that tenant's own
    tenant_session(). Not the Section 7.15.1 admin bypass.
    """
    session_factory = get_session_factory()
    session = session_factory()
    try:
        _reset_rls_settings(session)
        session.execute(text("SET LOCAL app.pipeline_sweep = 'true'"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def lifecycle_session() -> Iterator[Session]:
    """
    Used only by `docflow_core.lifecycle`'s recurring sweep (Section 7.15.4:
    "a scheduled job moves cancelling tenants to suspended at their
    effective date") to find tenants whose cancellation_effective_at has
    passed (migration 0019's `lifecycle_read` policy). It can SELECT
    `tenants` and nothing else -- the suspend action for each tenant found
    then runs inside that tenant's own tenant_session(), exactly like the
    rollup and the scheduler. Not the Section 7.15.1 admin bypass.
    """
    session_factory = get_session_factory()
    session = session_factory()
    try:
        _reset_rls_settings(session)
        session.execute(text("SET LOCAL app.lifecycle = 'true'"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
