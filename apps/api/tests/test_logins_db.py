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
  function (D-173 closed);
- migration 0039 (Stage 5, M7; D-214): anon and authenticated hold nothing
  in public, the two read_all policies serve every DocFlow login and neither
  Data API role, and each login still does what it does with the four
  functions Supabase's Security Advisor flagged.
"""

from __future__ import annotations

import hashlib
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

# packages/core/tests/test_ci_guards.py's table (0036's grants), plus 0039's
# staging function, checked here against the live grants.
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
    # 0039 (D-214): the nightly rollup's staging-TTL read.
    "staging_intakes_past_ttl(integer)": {"docflow_worker"},
}

# Policies with no flag that apply TO a role instead of every role (0039,
# D-214): every DocFlow login reads tiers and setup fees, through
# docflow_tables, and the Data API roles stay outside the policy.
NO_FLAG_POLICY_ROLES = {
    ("tiers", "read_all"): ["docflow_tables"],
    ("setup_fee_presets", "read_all"): ["docflow_tables"],
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
    than app.tenant_id applies TO exactly that flag's login; the two read_all
    policies apply TO the logins in NO_FLAG_POLICY_ROLES (0039); every other
    policy stays open to every role (tenant_isolation, which needs
    app.tenant_id, so a Data API caller matches no row)."""
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
            want = NO_FLAG_POLICY_ROLES.get((table, name), ["public"])
            if list(roles) != want:
                wrong.append(f"{table}.{name}: no flag, TO {list(roles)}, want {want}")
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
        ("rollup", "rollup_stale", "worker"),
        # 0039 (D-214): the nightly rollup's staging-TTL alert.
        ("rollup", "staging_ttl_elapsed", "worker"),
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


def test_the_rollup_flag_still_raises_no_other_tenant_less_alert_and_none_with_a_tenant(two_tenants):
    """0039 widened rollup_raise by one type, no more: on its own login the
    rollup flag is still refused any other type, and a tenant id."""
    attempts = [("review_backlog", None), ("staging_ttl_elapsed", two_tenants[0])]
    for alert_type, tenant_id in attempts:
        with _as("worker", rollup="true") as conn:
            nested = conn.begin_nested()
            try:
                conn.execute(
                    text(
                        "INSERT INTO founder_alerts (id, type, severity, tenant_id, payload) "
                        "VALUES (:id, :type, 'warning', :tenant, '{}'::jsonb)"
                    ),
                    {"id": str(uuid4()), "type": alert_type, "tenant": tenant_id},
                )
                allowed = True
            except Exception:  # noqa: BLE001 -- the refusal is the point
                allowed = False
            nested.rollback()
        assert not allowed, (alert_type, tenant_id)


# ── Migration 0039: Supabase's Data API roles (Stage 5, M7; D-214) ─────────

DATA_API_ROLES = ("anon", "authenticated")
TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN")
SEQUENCE_PRIVILEGES = ("SELECT", "UPDATE", "USAGE")
# The four functions Supabase's Security Advisor flagged -> the DocFlow
# logins that may EXECUTE each. documents_status_guard calls the first as the
# login that changes a status; the three trigger functions need no grant
# (Postgres checks EXECUTE when a trigger is created, not when it fires).
ADVISOR_FUNCTIONS = {
    "document_status_transition_allowed(text, text)": {"docflow_api", "docflow_worker", "docflow_admin"},
    "documents_status_guard()": set(),
    "exports_finished_rows_are_immutable()": set(),
    "users_protect_system_actors()": set(),
}


def _pg_array(values: tuple[str, ...]) -> str:
    return "{" + ",".join(values) + "}"


def test_anon_and_authenticated_hold_no_privilege_on_any_table_or_sequence_in_public():
    with platform_session() as session:
        checked = session.execute(
            text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')"
            )
        ).scalar_one()
        held = session.execute(
            text(
                "SELECT c.relname, r.role, p.privilege "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "CROSS JOIN unnest(CAST(:roles AS text[])) AS r(role) "
                "CROSS JOIN LATERAL unnest(CASE WHEN c.relkind = 'S' THEN CAST(:seq AS text[]) "
                "                               ELSE CAST(:tab AS text[]) END) AS p(privilege) "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S') "
                "  AND CASE WHEN c.relkind = 'S' THEN has_sequence_privilege(r.role, c.oid, p.privilege) "
                "           ELSE has_table_privilege(r.role, c.oid, p.privilege) END"
            ),
            {
                "roles": _pg_array(DATA_API_ROLES),
                "seq": _pg_array(SEQUENCE_PRIVILEGES),
                "tab": _pg_array(TABLE_PRIVILEGES),
            },
        ).all()
    assert checked >= 40  # 38 tables and 2 sequences on 2026-10-21
    assert [tuple(r) for r in held] == []


def test_anon_and_authenticated_can_execute_no_function_in_public():
    with platform_session() as session:
        callable_ = session.execute(
            text(
                "SELECT p.proname, r.role FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "CROSS JOIN unnest(CAST(:roles AS text[])) AS r(role) "
                "WHERE n.nspname = 'public' AND has_function_privilege(r.role, p.oid, 'EXECUTE')"
            ),
            {"roles": _pg_array(DATA_API_ROLES)},
        ).all()
    assert [tuple(r) for r in callable_] == []


def test_what_postgres_creates_in_public_later_gives_the_data_api_roles_nothing():
    with platform_session() as session:
        granted = session.execute(
            text(
                "SELECT d.defaclobjtype, pg_get_userbyid(a.grantee), a.privilege_type "
                "FROM pg_default_acl d JOIN pg_namespace n ON n.oid = d.defaclnamespace, "
                "     LATERAL aclexplode(d.defaclacl) a "
                "WHERE n.nspname = 'public' AND pg_get_userbyid(d.defaclrole) = 'postgres' "
                "  AND pg_get_userbyid(a.grantee) = ANY(CAST(:roles AS text[]))"
            ),
            {"roles": _pg_array(DATA_API_ROLES)},
        ).all()
    assert [tuple(r) for r in granted] == []


@pytest.mark.parametrize("function", sorted(ADVISOR_FUNCTIONS))
def test_each_advisor_function_has_an_empty_search_path_and_exactly_its_logins(function):
    roles = ["docflow_api", "docflow_worker", "docflow_admin", "docflow_stripe", *DATA_API_ROLES]
    with platform_session() as session:
        config = session.execute(
            text("SELECT proconfig FROM pg_proc WHERE oid = CAST(:fn AS regprocedure)"), {"fn": function}
        ).scalar_one()
        can = {
            role
            for role in roles
            if session.execute(
                text("SELECT has_function_privilege(:role, :fn, 'EXECUTE')"), {"role": role, "fn": function}
            ).scalar_one()
        }
    assert 'search_path=""' in (config or []), (function, config)
    assert can == ADVISOR_FUNCTIONS[function]


@pytest.fixture(scope="module")
def pending_document(two_tenants) -> Iterator[tuple[str, str]]:
    """One pending document in the first tenant, inserted by the Console's
    login (the guard's INSERT branch). Removed with the tenant."""
    tenant_id, document_id = two_tenants[0], str(uuid4())
    with platform_session() as session:
        session.execute(
            text(
                "INSERT INTO documents (id, tenant_id, original_filename, storage_path, source, status, "
                "content_sha256, created_at) "
                "VALUES (:id, :t, 'po.txt', :path, 'upload', 'pending', :sha, now())"
            ),
            {
                "id": document_id,
                "t": tenant_id,
                "path": f"tenants/{tenant_id}/acme-test/po.txt",
                "sha": hashlib.sha256(document_id.encode("utf-8")).hexdigest(),
            },
        )
    yield tenant_id, document_id


@pytest.mark.parametrize("login", ["api", "worker", "admin"])
def test_each_login_that_changes_a_status_still_runs_the_state_machine(login, pending_document):
    """documents_status_guard fires on the login's own update and calls
    document_status_transition_allowed as that login: a legal step passes, an
    illegal one is still refused by the guard (not by a missing grant)."""
    tenant_id, document_id = pending_document
    with _as(login, tenant_id=tenant_id) as conn:
        moved = conn.execute(
            text("UPDATE documents SET status = 'processing' WHERE id = :id"), {"id": document_id}
        ).rowcount
        assert moved == 1
        nested = conn.begin_nested()
        with pytest.raises(Exception) as excinfo:
            conn.execute(text("UPDATE documents SET status = 'approved' WHERE id = :id"), {"id": document_id})
        nested.rollback()
    assert getattr(excinfo.value.orig, "sqlstate", None) == "23514"  # check_violation
    assert "status processing -> approved is not allowed" in str(excinfo.value)


def test_the_stripe_login_can_no_longer_change_a_documents_status(pending_document):
    """It never does (billing_webhooks touches tenants and its two event
    functions only); since 0039 the database agrees."""
    tenant_id, document_id = pending_document
    with pytest.raises(Exception, match="permission denied for function document_status_transition_allowed"):
        with _as("stripe", tenant_id=tenant_id) as conn:
            conn.execute(
                text("UPDATE documents SET status = 'processing' WHERE id = :id"), {"id": document_id}
            )


@pytest.mark.parametrize("login", LOGINS)
def test_every_docflow_login_reads_tiers_and_setup_fee_presets(login):
    """0039's read_all TO docflow_tables: every login reads every row with no
    flag at all, as before 0039. A login left out of a list of readers would
    read none, silently (D-214: the worker's reactivate test found it)."""
    with platform_session() as session:
        want = session.execute(
            text("SELECT (SELECT count(*) FROM tiers), (SELECT count(*) FROM setup_fee_presets)")
        ).one()
    with _as(login) as conn:
        got = conn.execute(
            text("SELECT (SELECT count(*) FROM tiers), (SELECT count(*) FROM setup_fee_presets)")
        ).one()
    assert tuple(got) == tuple(want) and want[0] > 0 and want[1] > 0, (login, tuple(got), tuple(want))


@pytest.mark.parametrize("role", DATA_API_ROLES)
def test_anon_and_authenticated_read_no_row_of_tiers_or_setup_fee_presets(role):
    """Two layers: neither role holds any privilege on either table, and the
    read_all policy doesn't apply to it (it isn't a member of docflow_tables),
    so it would match no row even if a privilege came back."""
    with platform_session() as session:
        privileges = session.execute(
            text(
                "SELECT t, p FROM unnest(ARRAY['tiers', 'setup_fee_presets']) AS t, "
                "unnest(CAST(:tab AS text[])) AS p WHERE has_table_privilege(:role, 'public.' || t, p)"
            ),
            {"role": role, "tab": _pg_array(TABLE_PRIVILEGES)},
        ).all()
        member = session.execute(
            text("SELECT pg_has_role(:role, 'docflow_tables', 'MEMBER')"), {"role": role}
        ).scalar_one()
        policy_roles = session.execute(
            text(
                "SELECT DISTINCT unnest(roles)::text FROM pg_policies WHERE schemaname = 'public' "
                "AND policyname = 'read_all' AND tablename IN ('tiers', 'setup_fee_presets')"
            )
        ).scalars().all()
    assert [tuple(r) for r in privileges] == []
    assert member is False
    assert policy_roles == ["docflow_tables"]


def test_the_staging_function_answers_the_worker_with_ids_times_and_counts_only():
    with _as("worker") as conn:
        result = conn.execute(text("SELECT * FROM staging_intakes_past_ttl(36500)"))
        columns = list(result.keys())
        result.all()
    assert columns == ["intake_id", "created_at", "files"]
    with pytest.raises(Exception, match="permission denied for function staging_intakes_past_ttl"):
        with _as("api") as conn:
            conn.execute(text("SELECT * FROM staging_intakes_past_ttl(36500)"))


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
