"""
Migration 0039 and its reverse (Phase 5.5 Stage 5, M7; DECISIONS.md D-214):
what the two files say, the two committed states around them, and the checks
CI's round trip makes.

`supabase/reverse/0039_pre_snapshot.json` is staging before 0039, read
read-only on 2026-10-21 (scripts/ci/grants_snapshot.py).
`0039_post_snapshot.json` is that state with exactly the changes listed
here, by hand. CI's round trip (scripts/ci/migration_0039_roundtrip.py, core
job) fails unless its database shows these two states, so the list below,
the migration and the real database have to agree.

No database here. The real-database tests are in the API suite
(apps/api/tests/test_logins_db.py: each login, anon and authenticated) and
the worker suite (apps/worker/tests/test_rollup_alerts_db.py).
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "scripts" / "ci"
MIGRATION = REPO / "supabase" / "migrations" / "0039_m7_alerts_and_data_api_roles.sql"
REVERSE = REPO / "supabase" / "reverse" / "0039_reverse.sql"
PRE = REPO / "supabase" / "reverse" / "0039_pre_snapshot.json"
POST = REPO / "supabase" / "reverse" / "0039_post_snapshot.json"

# Changing either committed state is a visible change here too.
PRE_SHA256 = "b065a7c38b5ef8af0003c3838e50af09d6a1564cfc98967fda725c92edf3f5b2"
POST_SHA256 = "bb67bc5ee46580da7a30e38685f203a33748199a840e769309eb077eaaee32e2"

# ── The changes, by hand (founder, 2026-10-21) ──────────────────────────────
DATA_API_ROLES = ("anon", "authenticated")
TRANSITION = "document_status_transition_allowed(old_status text, new_status text)"
GUARD = "documents_status_guard()"
EXPORTS = "exports_finished_rows_are_immutable()"
USERS = "users_protect_system_actors()"
STAGING = "staging_intakes_past_ttl(ttl_days integer)"
# Who may EXECUTE each afterwards. service_role keeps the grant it had.
ADVISOR_EXECUTE = {
    TRANSITION: ["docflow_admin", "docflow_api", "docflow_worker", "service_role"],
    GUARD: ["service_role"],
    EXPORTS: ["service_role"],
    USERS: ["service_role"],
}
EMPTY_SEARCH_PATH = ['search_path=""']
# Every DocFlow login, through the role all four belong to (0036).
READ_ALL_ROLES = ["docflow_tables"]
ROLLUP_RAISE_BEFORE = (
    "((current_setting('app.rollup'::text, true) = 'true'::text) AND (type = 'rollup_stale'::text) "
    "AND (tenant_id IS NULL))"
)
ROLLUP_RAISE_AFTER = (
    "((current_setting('app.rollup'::text, true) = 'true'::text) AND "
    "(type = ANY (ARRAY['rollup_stale'::text, 'staging_ttl_elapsed'::text])) AND (tenant_id IS NULL))"
)
# postgres's defaults in public that lose anon and authenticated.
POSTGRES_DEFAULTS = (
    "postgres in public: tables",
    "postgres in public: sequences",
    "postgres in public: functions",
)


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def snap():
    return _load("grants_snapshot")


@pytest.fixture(scope="module")
def roundtrip():
    return _load("migration_0039_roundtrip")


def body_sha256(path: Path, function: str) -> str:
    """The SHA-256 of one function's body as the file defines it, line endings
    read as LF -- the same hash grants_snapshot.py takes of prosrc."""
    text = path.read_text(encoding="utf-8")
    pattern = rf"create (?:or replace )?function public\.{function}\(.*?\$\$(.*?)\$\$"
    found = re.findall(pattern, text, flags=re.S)
    assert len(found) == 1, (path.name, function, len(found))
    return hashlib.sha256(found[0].replace("\r\n", "\n").encode("utf-8")).hexdigest()


def expected_post(pre: dict[str, Any]) -> dict[str, Any]:
    """The pre-0039 state with exactly 0039's changes, from the list above."""
    post = copy.deepcopy(pre)
    for policy in post["policies"]:
        if policy["name"] == "read_all" and policy["table"] in ("tiers", "setup_fee_presets"):
            policy["roles"] = READ_ALL_ROLES
        if policy["name"] == "rollup_raise" and policy["table"] == "founder_alerts":
            assert policy["with_check"] == ROLLUP_RAISE_BEFORE
            policy["with_check"] = ROLLUP_RAISE_AFTER
    for signature, execute in ADVISOR_EXECUTE.items():
        post["functions"][signature]["execute"] = execute
        post["functions"][signature]["config"] = EMPTY_SEARCH_PATH
    for signature, name in ((GUARD, "documents_status_guard"), (USERS, "users_protect_system_actors")):
        post["functions"][signature]["body_sha256"] = body_sha256(MIGRATION, name)
    post["functions"][STAGING] = {
        "security_definer": True,
        "config": EMPTY_SEARCH_PATH,
        "body_sha256": body_sha256(MIGRATION, "staging_intakes_past_ttl"),
        "execute": ["docflow_worker"],
    }
    post["functions"] = dict(sorted(post["functions"].items()))
    for relation in post["relations"].values():
        for role in DATA_API_ROLES:
            relation["grants"].pop(role, None)
    for key in POSTGRES_DEFAULTS:
        for role in DATA_API_ROLES:
            post["default_privileges"][key].pop(role, None)
    return post


def _statements(path: Path) -> list[str]:
    """The file's SQL statements outside function bodies, comments removed,
    whitespace collapsed."""
    sql = re.sub(r"\$\$.*?\$\$", "$$", path.read_text(encoding="utf-8"), flags=re.S)
    sql = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [re.sub(r"\s+", " ", s).strip().lower() for s in sql.split(";") if s.strip()]


# ── The two committed states ────────────────────────────────────────────────


def test_the_committed_pre_state_is_stagings_as_read(snap):
    assert snap.sha256(snap.load(PRE)) == PRE_SHA256


def test_the_committed_post_state_is_the_pre_state_with_exactly_the_listed_changes(snap):
    assert snap.load(POST) == expected_post(snap.load(PRE))
    assert snap.sha256(snap.load(POST)) == POST_SHA256


def test_before_0039_the_data_api_roles_held_everything_the_migration_takes_away(snap):
    """What staging showed on 2026-10-21, so the list above is not a no-op."""
    pre = snap.load(PRE)
    assert len(pre["relations"]) == 40
    for name, relation in pre["relations"].items():
        for role in DATA_API_ROLES:
            assert relation["grants"].get(role), (name, role)
    for signature in ADVISOR_EXECUTE:
        assert set(pre["functions"][signature]["execute"]) >= {"PUBLIC", *DATA_API_ROLES}
        assert pre["functions"][signature]["config"] == []
    assert STAGING not in pre["functions"]
    read_all = [p for p in pre["policies"] if p["name"] == "read_all"]
    assert {p["table"] for p in read_all} == {"tiers", "setup_fee_presets"}
    assert all(p["roles"] == ["public"] for p in read_all)


def test_after_0039_no_data_api_role_holds_anything_in_public(snap):
    post = snap.load(POST)
    for name, relation in post["relations"].items():
        assert not set(relation["grants"]) & {"PUBLIC", *DATA_API_ROLES}, name
    for signature, function in post["functions"].items():
        assert not set(function["execute"]) & {"PUBLIC", *DATA_API_ROLES}, signature
    for key in POSTGRES_DEFAULTS:
        assert not set(post["default_privileges"][key]) & set(DATA_API_ROLES), key
    # supabase_admin's defaults are untouched: postgres can't alter them.
    pre = snap.load(PRE)
    for key, grants in post["default_privileges"].items():
        if key.startswith("supabase_admin "):
            assert grants == pre["default_privileges"][key]
    # Every policy open to every role keys on app.tenant_id (and so matches
    # no row through the Data API), apart from none.
    for policy in post["policies"]:
        if policy["roles"] == ["public"]:
            assert "app.tenant_id" in (policy["using"] or "") + (policy["with_check"] or ""), policy


def test_every_function_in_public_has_its_search_path_set_after_0039(snap):
    post = snap.load(POST)
    assert len(post["functions"]) == 21
    for signature, function in post["functions"].items():
        assert any(c.startswith("search_path=") for c in function["config"]), signature


# ── The two files ───────────────────────────────────────────────────────────


def test_0039_says_exactly_this():
    assert _statements(MIGRATION) == [
        "create function public.staging_intakes_past_ttl(ttl_days integer) returns table "
        "(intake_id uuid, created_at timestamptz, files integer) language sql stable security definer "
        "set search_path = '' as $$",
        "revoke all on function public.staging_intakes_past_ttl(integer) from public, anon, authenticated, "
        "service_role",
        "grant execute on function public.staging_intakes_past_ttl(integer) to docflow_worker",
        "alter policy rollup_raise on public.founder_alerts with check (current_setting('app.rollup', true) "
        "= 'true' and type in ('rollup_stale', 'staging_ttl_elapsed') and tenant_id is null)",
        "alter policy read_all on public.tiers to docflow_tables",
        "alter policy read_all on public.setup_fee_presets to docflow_tables",
        "alter function public.document_status_transition_allowed(text, text) set search_path = ''",
        "alter function public.exports_finished_rows_are_immutable() set search_path = ''",
        "create or replace function public.documents_status_guard() returns trigger language plpgsql "
        "set search_path = '' as $$",
        "create or replace function public.users_protect_system_actors() returns trigger language plpgsql "
        "set search_path = '' as $$",
        "revoke execute on function public.document_status_transition_allowed(text, text) from public, anon, "
        "authenticated",
        "revoke execute on function public.documents_status_guard() from public, anon, authenticated",
        "revoke execute on function public.exports_finished_rows_are_immutable() from public, anon, "
        "authenticated",
        "revoke execute on function public.users_protect_system_actors() from public, anon, authenticated",
        "grant execute on function public.document_status_transition_allowed(text, text) to docflow_api, "
        "docflow_worker, docflow_admin",
        "revoke all on all tables in schema public from anon, authenticated",
        "revoke all on all sequences in schema public from anon, authenticated",
        "alter default privileges for role postgres in schema public revoke all on tables from anon, "
        "authenticated",
        "alter default privileges for role postgres in schema public revoke all on sequences from anon, "
        "authenticated",
        "alter default privileges for role postgres in schema public revoke execute on functions from anon, "
        "authenticated",
    ]


def test_the_reverse_undoes_each_statement():
    assert _statements(REVERSE) == [
        "alter default privileges for role postgres in schema public grant execute on functions to anon, "
        "authenticated",
        "alter default privileges for role postgres in schema public grant all on sequences to anon, "
        "authenticated",
        "alter default privileges for role postgres in schema public grant all on tables to anon, "
        "authenticated",
        "grant all on all sequences in schema public to anon, authenticated",
        "grant all on all tables in schema public to anon, authenticated",
        "revoke execute on function public.document_status_transition_allowed(text, text) from docflow_api, "
        "docflow_worker, docflow_admin",
        "grant execute on function public.document_status_transition_allowed(text, text) to public, anon, "
        "authenticated",
        "grant execute on function public.documents_status_guard() to public, anon, authenticated",
        "grant execute on function public.exports_finished_rows_are_immutable() to public, anon, "
        "authenticated",
        "grant execute on function public.users_protect_system_actors() to public, anon, authenticated",
        "alter function public.document_status_transition_allowed(text, text) reset search_path",
        "alter function public.exports_finished_rows_are_immutable() reset search_path",
        "create or replace function public.documents_status_guard() returns trigger language plpgsql as $$",
        "create or replace function public.users_protect_system_actors() returns trigger language plpgsql "
        "as $$",
        "alter policy read_all on public.tiers to public",
        "alter policy read_all on public.setup_fee_presets to public",
        "alter policy rollup_raise on public.founder_alerts with check (current_setting('app.rollup', true) "
        "= 'true' and type = 'rollup_stale' and tenant_id is null)",
        "drop function public.staging_intakes_past_ttl(integer)",
    ]


@pytest.mark.parametrize(
    ("function", "source"),
    [
        ("documents_status_guard", "0027_document_state_machine.sql"),
        ("users_protect_system_actors", "0028_system_actors.sql"),
    ],
)
def test_the_reverse_restores_each_body_word_for_word(snap, function, source):
    """The bodies the reverse puts back hash to staging's, read before 0039,
    and to the migrations that wrote them."""
    signature = f"{function}()"
    assert body_sha256(REVERSE, function) == snap.load(PRE)["functions"][signature]["body_sha256"]
    original = (REPO / "supabase" / "migrations" / source).read_text(encoding="utf-8")
    (body,) = re.findall(rf"create function {function}\(\).*?\$\$(.*?)\$\$", original, flags=re.S)
    assert hashlib.sha256(body.replace("\r\n", "\n").encode("utf-8")).hexdigest() == body_sha256(
        REVERSE, function
    )


def test_the_new_bodies_change_only_the_schema_of_what_they_name():
    """documents_status_guard and users_protect_system_actors: 0039's body is
    the old one with `public.` / `pg_catalog.` added, nothing else."""
    def body(path: Path, function: str) -> str:
        (found,) = re.findall(rf"create or replace function public\.{function}\(\).*?\$\$(.*?)\$\$",
                              path.read_text(encoding="utf-8"),
                              flags=re.S)
        return found.replace("\r\n", "\n")

    guard_new, guard_old = body(MIGRATION, "documents_status_guard"), body(REVERSE, "documents_status_guard")
    assert guard_new == guard_old.replace(
        "if not document_status_transition_allowed(", "if not public.document_status_transition_allowed("
    )
    users_new = body(MIGRATION, "users_protect_system_actors")
    users_old = body(REVERSE, "users_protect_system_actors")
    assert users_new == users_old.replace("if pg_has_role(", "if pg_catalog.pg_has_role(").replace(
        "from pg_class ", "from pg_catalog.pg_class "
    )
    assert guard_new != guard_old and users_new != users_old


def test_the_staging_function_returns_no_prospect_name_or_address():
    text = MIGRATION.read_text(encoding="utf-8")
    pattern = r"create function public\.staging_intakes_past_ttl\(.*?\$\$(.*?)\$\$"
    (body,) = re.findall(pattern, text, flags=re.S)
    for column in ("prospect_name", "contact_email", "notes", "original_filename", "storage_path"):
        assert column not in body, column


# ── The round trip's comparisons ────────────────────────────────────────────


def test_the_round_trip_passes_on_the_committed_states(snap, roundtrip):
    pre, post = snap.load(PRE), snap.load(POST)
    assert roundtrip.check_reverse_step(post, pre, pre, post) == []
    assert roundtrip.check_forward_step(post, post) == []


def test_the_round_trip_catches_a_data_api_grant_left_behind(snap, roundtrip):
    pre, post = snap.load(PRE), snap.load(POST)
    broken = copy.deepcopy(post)
    broken["relations"]["tiers"]["grants"]["anon"] = ["TRUNCATE"]
    problems = roundtrip.check_reverse_step(broken, pre, pre, post)
    assert len(problems) == 1 and "relations tiers" in problems[0]


def test_the_round_trip_catches_a_reverse_that_misses_a_body(snap, roundtrip):
    pre, post = snap.load(PRE), snap.load(POST)
    broken = copy.deepcopy(pre)
    broken["functions"][GUARD]["body_sha256"] = post["functions"][GUARD]["body_sha256"]
    problems = roundtrip.check_reverse_step(post, broken, pre, post)
    assert len(problems) == 1 and f"functions {GUARD}" in problems[0]


def test_the_round_trip_catches_a_forward_again_that_differs(snap, roundtrip):
    post = snap.load(POST)
    broken = copy.deepcopy(post)
    broken["default_privileges"]["postgres in public: functions"]["anon"] = ["EXECUTE"]
    problems = roundtrip.check_forward_step(broken, post)
    assert len(problems) == 1 and "default_privileges" in problems[0]


def test_the_round_trip_catches_a_changed_policy(snap, roundtrip):
    pre, post = snap.load(PRE), snap.load(POST)
    broken = copy.deepcopy(post)
    for policy in broken["policies"]:
        if policy["name"] == "rollup_read_own":
            policy["using"] = policy["using"].replace("'rollup_stale'", "'staging_ttl_elapsed'")
    problems = roundtrip.check_reverse_step(broken, pre, pre, post)
    assert len(problems) == 1 and "rollup_read_own" in problems[0]


def test_the_snapshot_hashes_a_body_the_same_with_crlf_or_lf(snap):
    class Conn:
        def __init__(self, body: str) -> None:
            self.body = body

        def execute(self, sql, params=None):
            rows = []
            if "FROM pg_proc" in sql:
                rows = [("f()", False, [], self.body, ["PUBLIC"])]
            return type("R", (), {"fetchall": lambda _self: rows})()

    lf = snap.snapshot(Conn("\nbegin\n    return 1;\nend;\n"))
    crlf = snap.snapshot(Conn("\r\nbegin\r\n    return 1;\r\nend;\r\n"))
    assert lf == crlf
