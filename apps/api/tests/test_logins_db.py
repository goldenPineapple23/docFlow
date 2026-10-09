"""
The four database logins, against the real database (Stage 3e, F-1, D-159;
migration 0036; BUILD-STATUS "3e detailed design", part E1).

Before 3e every flag policy applied to every role, and only this codebase
decided who set a flag. Now each applies TO one login, so the database
refuses a flag set on any other login. These tests prove that on the real
Postgres, and that each SECURITY DEFINER function is callable by exactly its
logins:

- each login connects as itself, with the properties a login must have;
- every flag policy in the catalog names exactly its login (a future
  migration adding a flag policy without one fails here);
- the same flag on the wrong login opens nothing (behaviour, not just the
  catalog), and a docflow_api tenant session that sets every flag still sees
  only its own tenant;
- the EXECUTE table, including docflow_api being refused the Stripe event
  function (D-173 closed).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

import pytest
from docflow_core import db
from docflow_core.constants import WORKER_RESTART_WINDOW_MIN
from docflow_core.db import platform_session
from sqlalchemy import text
from sqlalchemy.engine import Connection

from tests.conftest import requires_console_schema
from tests.tenant_cleanup import purge_test_tenant, register_test_tenant

pytestmark = requires_console_schema

LOGINS = ("api", "worker", "admin", "stripe")
# Lists go to Postgres as jsonb in this codebase (db.py registers the dumper),
# so the ids travel as one comma-separated string.
_THESE = "id = ANY(CAST(string_to_array(:ids, ',') AS uuid[]))"

# The flag -> the one login its policies apply to (3e design, A2).
FLAG_OWNER = {
    "is_platform_admin": "docflow_admin",
    "rollup": "docflow_worker",
    "scheduler": "docflow_worker",
    "lifecycle": "docflow_worker",
    "pipeline_sweep": "docflow_worker",
    "dispatcher": "docflow_worker",
    "auth_user_id": "docflow_api",
    "intake_token": "docflow_api",
    "intake_refusal": "docflow_api",
    "stripe_webhook": "docflow_stripe",
}

# The same table as packages/core/tests/test_ci_guards.py, checked here
# against the live grants.
FUNCTION_GRANTS = {
    "record_stripe_subscription_event(text, text, timestamptz, text, text, timestamptz, text)": {
        "docflow_stripe"
    },
    "record_stripe_card_event(text, text, text, text, bigint)": {"docflow_stripe"},
    "dispatch_candidates(integer)": {"docflow_worker"},
    "probe_candidate()": {"docflow_worker"},
    "mark_dispatched(uuid)": {"docflow_worker"},
    "clear_dispatched(uuid)": {"docflow_worker"},
    "dispatcher_heartbeat()": {"docflow_worker"},
    "provider_record_failure(text, text, text, boolean, integer, integer)": {"docflow_worker"},
    "provider_record_success(text)": {"docflow_worker"},
    "provider_take_probe(text, integer)": {"docflow_worker"},
    "count_routing_model_failure()": {"docflow_worker"},
    "record_worker_start(text, text)": {"docflow_worker"},
    "dispatch_unclaimed_age()": {"docflow_worker"},
    "dispatcher_status()": {"docflow_api", "docflow_worker", "docflow_admin"},
    "provider_state(text)": {"docflow_api", "docflow_worker", "docflow_admin"},
    "worker_starts_last_hour()": {"docflow_api", "docflow_admin"},
}


@contextmanager
def _as(login: str, **flags: str) -> Iterator[Connection]:
    """A raw transaction on one login with the given app.* settings, always
    rolled back. Raw on purpose: it sets flags no helper in db.py would set
    on that login, which is the attack the database must now refuse."""
    with db.engine_for(login).connect() as conn:
        with conn.begin() as tx:
            for name, value in flags.items():
                conn.execute(text("SELECT set_config(:n, :v, true)"), {"n": f"app.{name}", "v": value})
            yield conn
            tx.rollback()


@pytest.fixture(scope="module")
def two_tenants() -> Iterator[tuple[str, str]]:
    ids = (str(uuid4()), str(uuid4()))
    with platform_session() as session:
        for i, tenant_id in enumerate(ids):
            session.execute(
                text(
                    "INSERT INTO tenants "
                    "(id, name, status, onboarding_status, created_at, updated_at, status_changed_at) "
                    "VALUES (:id, :name, 'active', 'tenant_created', now(), now(), now())"
                ),
                {"id": tenant_id, "name": f"Acme Test Logins {i} {tenant_id[:8]}"},
            )
            register_test_tenant(tenant_id)
    yield ids
    for tenant_id in ids:
        purge_test_tenant(tenant_id)


@pytest.mark.parametrize("login", LOGINS)
def test_each_login_connects_as_itself_and_is_only_a_member_of_docflow_tables(login):
    assert db.connected_login(login) == f"docflow_{login}"
    with _as(login) as conn:
        row = conn.execute(
            text(
                "SELECT r.rolbypassrls, r.rolsuper, has_schema_privilege(current_user, 'public', 'CREATE'), "
                "       ARRAY(SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
                "             WHERE m.member = r.oid ORDER BY 1)::text[], "
                "       current_setting('idle_in_transaction_session_timeout') "
                "FROM pg_roles r WHERE r.rolname = current_user"
            )
        ).one()
    bypass, superuser, can_create, member_of, idle = row
    assert not bypass and not superuser
    assert not can_create
    assert list(member_of) == ["docflow_tables"]
    assert idle == "5min"


def test_every_flag_policy_applies_to_exactly_its_login():
    """The catalog half: any policy whose expressions name an app.* flag other
    than app.tenant_id applies TO exactly that flag's login; every other
    policy stays open to every role (tenant_isolation, read_all)."""
    with platform_session() as session:
        rows = session.execute(
            text(
                "SELECT tablename, policyname, roles::text[], coalesce(qual, '') || ' ' || "
                "coalesce(with_check, '') FROM pg_policies WHERE schemaname = 'public'"
            )
        ).all()
    assert rows
    wrong = []
    flag_policies = 0
    for table, name, roles, expressions in rows:
        flags = set(re.findall(r"app\.([a-z_]+)", expressions)) - {"tenant_id"}
        if not flags:
            if list(roles) != ["public"]:
                wrong.append(f"{table}.{name}: no flag but TO {roles}")
            continue
        flag_policies += 1
        owners = {FLAG_OWNER.get(f, f"<unknown flag app.{f}>") for f in flags}
        if len(owners) != 1 or list(roles) != sorted(owners):
            wrong.append(f"{table}.{name}: flags {sorted(flags)} TO {list(roles)}, want {sorted(owners)}")
    assert wrong == []
    assert flag_policies >= 51  # staging's count on 2026-10-02


@pytest.mark.parametrize(
    "flag",
    ["is_platform_admin", "rollup", "lifecycle", "pipeline_sweep", "stripe_webhook"],
)
def test_a_flag_that_opens_tenants_opens_it_only_on_its_own_login(flag, two_tenants):
    owner = FLAG_OWNER[flag].removeprefix("docflow_")
    for login in LOGINS:
        with _as(login, **{flag: "true"}) as conn:
            seen = conn.execute(
                text(f"SELECT count(*) FROM tenants WHERE {_THESE}"),
                {"ids": ",".join(two_tenants)},
            ).scalar_one()
        assert seen == (2 if login == owner else 0), (flag, login, seen)


def test_an_api_tenant_session_that_sets_every_flag_sees_only_its_own_tenant(two_tenants):
    mine, _other = two_tenants
    every_flag = {f: "true" for f in FLAG_OWNER if f not in ("auth_user_id", "intake_token")}
    every_flag["auth_user_id"] = str(uuid4())
    every_flag["intake_token"] = "acme-test-not-a-token"
    with _as("api", tenant_id=mine, **every_flag) as conn:
        seen = [
            str(r[0])
            for r in conn.execute(
                text(f"SELECT id FROM tenants WHERE {_THESE}"),
                {"ids": ",".join(two_tenants)},
            )
        ]
    assert seen == [mine]


@pytest.mark.parametrize(
    ("flag", "alert_type", "owner"),
    [
        ("dispatcher", "model_api_failure", "worker"),
        ("dispatcher", "worker_restarting", "worker"),
        ("intake_refusal", "intake_webhook_refused", "api"),
    ],
)
def test_a_tenant_less_alert_flag_inserts_only_on_its_own_login(flag, alert_type, owner):
    """The insert-only policies (0029, 0035, 0036): the flag on any other
    login is refused by the database."""
    for login in LOGINS:
        with _as(login, **{flag: "true"}) as conn:
            nested = conn.begin_nested()
            try:
                conn.execute(
                    text(
                        "INSERT INTO founder_alerts (id, type, severity, tenant_id, payload) "
                        "VALUES (:id, :type, 'high', NULL, '{}'::jsonb)"
                    ),
                    {"id": str(uuid4()), "type": alert_type},
                )
                allowed = True
            except Exception:  # noqa: BLE001 -- the refusal is the point
                allowed = False
            nested.rollback()
        assert allowed == (login == owner), (flag, alert_type, login)


@pytest.mark.parametrize("function", sorted(FUNCTION_GRANTS))
def test_each_security_definer_function_is_callable_by_exactly_its_logins(function):
    roles = ["docflow_api", "docflow_worker", "docflow_admin", "docflow_stripe"]
    with platform_session() as session:
        public_roles = [
            r for (r,) in session.execute(
                text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")
            )
        ]
        can = {
            role: session.execute(
                text("SELECT has_function_privilege(:role, :fn, 'EXECUTE')"),
                {"role": role, "fn": function},
            ).scalar_one()
            for role in [*roles, *public_roles]
        }
    assert {role for role, ok in can.items() if ok} == FUNCTION_GRANTS[function]


def test_the_api_login_is_refused_the_stripe_event_function():
    """D-173's residual risk, closed: code running as the API's login can no
    longer call the Stripe event function with a fabricated event id."""
    with pytest.raises(Exception, match="permission denied"):
        with _as("api") as conn:
            conn.execute(
                text(
                    "SELECT record_stripe_subscription_event('evt_acme_test', 'x', now(), "
                    "'cus_acme_test', 'active', now(), 'event')"
                )
            )


def test_worker_starts_is_reached_only_through_its_functions():
    """Global, RLS on, no policies: no login reads or writes it directly."""
    for login in LOGINS:
        with _as(login, is_platform_admin="true", dispatcher="true") as conn:
            assert conn.execute(text("SELECT count(*) FROM worker_starts")).scalar_one() == 0
    with platform_session() as session:
        rls, policies = session.execute(
            text(
                "SELECT c.relrowsecurity, "
                "       (SELECT count(*) FROM pg_policies p WHERE p.tablename = 'worker_starts') "
                "FROM pg_class c WHERE c.relname = 'worker_starts'"
            )
        ).one()
    assert rls and policies == 0


def test_the_restart_window_in_the_migration_matches_the_constant():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[3] / "supabase" / "migrations" / "0036_separate_logins.sql"
    ).read_text(encoding="utf-8")
    windows = re.findall(r"interval '(\d+) minutes'", source)
    assert windows and set(windows) == {str(WORKER_RESTART_WINDOW_MIN)}


def test_the_three_new_functions_have_an_empty_search_path():
    with platform_session() as session:
        rows = session.execute(
            text(
                "SELECT p.proname, p.prosecdef, p.proconfig FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' "
                "AND p.proname IN "
                "    ('record_worker_start', 'worker_starts_last_hour', 'dispatch_unclaimed_age')"
            )
        ).all()
    assert len(rows) == 3
    for name, definer, config in rows:
        assert definer, name
        assert 'search_path=""' in (config or []), (name, config)


# ── Start-up checks (3e design, A4 / E2) ────────────────────────────────────


@pytest.fixture
def settings_env(monkeypatch):
    """Change one setting through the environment for a test, then restore."""
    from docflow_core.config import get_settings

    def set_env(name: str, value: str) -> None:
        monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    yield set_env
    get_settings.cache_clear()


def test_the_api_starts_when_every_url_is_its_own_login():
    from app.main import login_startup_check

    assert set(login_startup_check()) == {"api", "admin", "stripe"}


def test_the_api_refuses_to_start_when_a_url_is_another_logins(settings_env):
    """A pasted wrong URL (here: the API's own URL given as the admin login)
    stops the API, naming the setting and the role found, never the URL."""
    from docflow_core.config import get_settings

    from app.main import login_startup_check

    settings_env("ADMIN_DATABASE_URL", get_settings().database_url)
    with pytest.raises(db.WrongLoginError) as excinfo:
        login_startup_check()
    message = str(excinfo.value)
    assert "docflow_admin" in message and "'docflow_api'" in message
    assert "postgresql" not in message and "@" not in message


def test_a_session_for_a_login_this_process_does_not_hold_refuses_to_open(settings_env):
    """The worker holds no admin URL: platform_session can't even connect."""
    settings_env("ADMIN_DATABASE_URL", "")
    with pytest.raises(db.MissingLoginError, match="ADMIN_DATABASE_URL"):
        with platform_session():
            pass
