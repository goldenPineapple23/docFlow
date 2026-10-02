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

Every session connects as one of four Postgres logins (Stage 3e, F-1,
D-159; migration 0036), all NOBYPASSRLS, so RLS is genuinely enforced:
docflow_api (tenant requests and the sign-in and intake lookups),
docflow_worker (jobs and sweeps), docflow_admin (the Console and the
scripts) and docflow_stripe (the Stripe webhook). Cross-tenant reads still
need to get past RLS, which is why every tenant-scoped table's policy is an
OR of two conditions -- the tenant match above, and:

    CREATE POLICY platform_admin_access ON <table> TO docflow_admin
      USING (current_setting('app.is_platform_admin', true) = 'true');

`platform_session()` below sets that flag, and connects as docflow_admin,
the only login the policy applies to. A tenant connection that set the
flag would still see nothing more: the database refuses it, not just this
module's import boundary (Section 7.15.1). Each narrow flag session below
works the same way, with its own login.

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

# Stage 3e (F-1, D-159): one Postgres login per service. Each process says
# which is its own (`use_own_login`), and its tenant sessions run as that
# login. The narrow sessions below each run as the one login their policies
# are granted TO (migration 0036), whatever process opens them: a worker that
# opened token_lookup_session would connect as docflow_worker and the
# database would show it nothing.
LOGINS = ("api", "worker", "admin", "stripe")
_own_login = "api"
_engines: dict[str, Engine] = {}
_factories: dict[str, sessionmaker[Session]] = {}


class MissingLoginError(RuntimeError):
    """This process holds no URL for the login a session needs."""


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


def use_own_login(login: str) -> None:
    """Called once at start-up: the API says "api", the worker "worker", the
    maintenance and seed scripts "admin" (founder, 3e Q8). Tenant sessions
    then run as that login."""
    global _own_login, _SessionLocal
    if login not in LOGINS:
        raise ValueError(f"unknown login {login!r}")
    _own_login = login
    _SessionLocal = None


def own_login() -> str:
    return _own_login


def login_url(login: str) -> str:
    """The URL for one login, or "" if this process holds none.

    DATABASE_URL is the process's own login. The API and worker logins may
    also be named separately (API_DATABASE_URL, WORKER_DATABASE_URL), which
    is what lets one machine hold both: the founder's `.env`, and the test
    suites. The admin and Stripe logins have only their own setting, so a
    process without it can't open their sessions at all.
    """
    settings = get_settings()
    if login == "api":
        return settings.api_database_url or settings.database_url
    if login == "worker":
        return settings.worker_database_url or settings.database_url
    if login == "admin":
        return settings.admin_database_url
    if login == "stripe":
        return settings.stripe_database_url
    raise ValueError(f"unknown login {login!r}")


def engine_for(login: str) -> Engine:
    url = login_url(login)
    if not url:
        setting = {"admin": "ADMIN_DATABASE_URL", "stripe": "STRIPE_DATABASE_URL"}.get(
            login, "DATABASE_URL"
        )
        raise MissingLoginError(
            f"{setting} is not set, so this process has no docflow_{login} login. "
            "See SETUP.md Step 1 and RUNBOOK 10."
        )
    # Engines are shared by URL: in a process where two logins resolve to the
    # same string (the API's own login and "api"), there is one pool, not two.
    if url not in _engines:
        _engines[url] = create_engine(
            _psycopg3_url(url),
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
    return _engines[url]


def get_engine() -> Engine:
    """The process's own login (`use_own_login`)."""
    return engine_for(_own_login)


def forget_inherited_connections() -> None:
    """For a process forked from one that has already used the database
    (Celery's prefork children, Stage 3a): drop the pooled connections it
    inherited without closing them, since they belong to the parent. Two
    processes sharing one Postgres connection corrupt each other's traffic.
    The next query opens a fresh connection of this process's own."""
    for engine in _engines.values():
        engine.dispose(close=False)


def get_session_factory() -> sessionmaker[Session]:
    """Sessions as the process's own login: tenant_session and function_session."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def _session_factory(login: str) -> sessionmaker[Session]:
    """Sessions as one named login, for the narrow sessions below."""
    url = login_url(login)
    if url not in _factories:
        _factories[url] = sessionmaker(bind=engine_for(login), expire_on_commit=False)
    return _factories[url]


def connected_login(login: str) -> str:
    """`current_user` on one login's URL: what the start-up checks compare."""
    with engine_for(login).connect() as conn:
        return str(conn.execute(text("SELECT current_user")).scalar_one())


class WrongLoginError(RuntimeError):
    """A URL connects as a different role than the login it is configured for."""


def verify_logins(logins: tuple[str, ...]) -> list[str]:
    """
    Start-up check (3e, A4): each named login that has a URL must connect as
    `docflow_<login>`. Raises WrongLoginError naming the setting and the role
    it found (never the URL), so a pasted wrong URL stops the process before
    it serves anything. A login with no URL is skipped. A database that can't
    be reached is not a wrong login: the error propagates to the caller,
    which decides (the API logs and starts; /healthz must answer).
    Returns the logins checked.
    """
    checked: list[str] = []
    for login in logins:
        if not login_url(login):
            continue
        found = connected_login(login)
        if found != f"docflow_{login}":
            raise WrongLoginError(
                f"the docflow_{login} URL connects as {found!r}. Each service has its own "
                f"login (RUNBOOK 10); check which secret holds which URL."
            )
        checked.append(login)
    return checked


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
    session.execute(text("RESET app.intake_refusal"))
    session.execute(text("RESET app.dispatcher"))


@contextmanager
def tenant_session(tenant_id: UUID) -> Iterator[Session]:
    """
    Open a transaction scoped to exactly one tenant. `tenant_id` must come
    from the authenticated session (see app/deps.py), never from a request
    body, query string, or client-side state (CLAUDE.md Section 10).
    It runs as the process's own login (`use_own_login`).
    """
    with _tenant_transaction(get_session_factory(), tenant_id) as session:
        yield session


@contextmanager
def stripe_tenant_session(tenant_id: UUID) -> Iterator[Session]:
    """
    tenant_session() as docflow_stripe, for the Stripe webhook only
    (`docflow_core.billing_webhooks` and the card-event write it calls in
    `card_billing`). Only docflow_stripe may execute the two Stripe event
    functions (migration 0036), which run in the same transaction as the
    tenant update they stand for (D-173). An import-boundary test keeps every
    other module from using it.
    """
    with _tenant_transaction(_session_factory("stripe"), tenant_id) as session:
        yield session


@contextmanager
def _tenant_transaction(
    session_factory: sessionmaker[Session], tenant_id: UUID
) -> Iterator[Session]:
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
    session_factory = _session_factory("admin")
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
    session_factory = _session_factory("api")
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
    session_factory = _session_factory("api")
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
    session_factory = _session_factory("worker")
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
    session_factory = _session_factory("worker")
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
    Used only by the Stripe webhook handler (docflow_core.billing_webhooks)
    to look up which tenant a `stripe_customer_id` belongs to (0019's
    `stripe_webhook_lookup`, SELECT only). It can no longer write
    `stripe_webhook_events`: migration 0029 dropped that policy, and the
    event id is recorded only by `record_stripe_subscription_event()`, called
    from the tenant's own tenant_session() in the same transaction as the
    update it stands for (H11, D-173). Not the Section 7.15.1 admin bypass.
    """
    session_factory = _session_factory("stripe")
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
def intake_refusal_session() -> Iterator[Session]:
    """
    Used only by `docflow_core.email_intake.alert_webhook_refused` to raise the
    `intake_webhook_refused` founder alert when the inbound webhook refuses a
    request (D-171). The request was refused before its token was read, so
    there is no tenant. Migration 0029's `intake_refusal_raise` and
    `intake_refusal_enqueue` policies let this session insert exactly that
    alert type with no tenant, and the founder-alert email with it -- and read
    nothing, on any table. Not the Section 7.15.1 admin bypass.
    """
    session_factory = _session_factory("api")
    session = session_factory()
    try:
        _reset_rls_settings(session)
        session.execute(text("SET LOCAL app.intake_refusal = 'true'"))
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
    session_factory = _session_factory("worker")
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
def dispatcher_session() -> Iterator[Session]:
    """
    Used only by the dispatcher and the model-provider state
    (`docflow_core.dispatch`, `docflow_core.model_provider`; Stage 3d). Its
    reads and writes across tenants go through migration 0035's SECURITY
    DEFINER functions, which return ids, counts and times only. The flag
    itself opens exactly one thing: 0035's `dispatcher_raise` and
    `dispatcher_enqueue` policies, which let it insert the four tenant-less
    alerts the worker raises (model_api_failure, model_api_recovered,
    dispatcher_stopped, routing_model_failure) and their emails -- and read
    nothing, on any table. Not the Section 7.15.1 admin bypass.
    """
    session_factory = _session_factory("worker")
    session = session_factory()
    try:
        _reset_rls_settings(session)
        session.execute(text("SET LOCAL app.dispatcher = 'true'"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def function_session() -> Iterator[Session]:
    """
    A transaction with every app.* flag cleared: RLS shows it no row of any
    table, so all it can do is call a SECURITY DEFINER function granted to
    the process's own login. Used only to read the dispatcher's heartbeat
    (`docflow_core.dispatch`, for /healthz and the stuck sweep; Stage 3d, gap
    1) and the worker's starts in the last hour (`docflow_core.worker_starts`,
    for /healthz; Stage 3e).
    """
    session_factory = get_session_factory()
    session = session_factory()
    try:
        _reset_rls_settings(session)
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
    session_factory = _session_factory("worker")
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
