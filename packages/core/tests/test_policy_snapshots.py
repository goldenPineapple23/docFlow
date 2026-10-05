"""
The two committed states around migration 0036, and the checks CI and the
cutover make against them (founder, 2026-10-05; RUNBOOK 10.2 step 5).

`supabase/reverse/0036_pre_snapshot.json` is staging before 0036 (read
2026-10-02). `supabase/reverse/0036_post_snapshot.json` is the state after
0036, as CI's database showed it through the same snapshot code. CI's
round trip (scripts/ci/migration_roundtrip.py, core job) fails unless its own
two states equal these two files, so the post file is what a database must
show once 0036 is applied: staging at its cutover, production in Phase 6.

No database here: these tests hold the files to each other, to the migration
and to FUNCTION_GRANTS, and test the comparison code on made-up states.
"""

import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from tests.test_ci_guards import FUNCTION_GRANTS

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "scripts" / "ci"
PRE = REPO / "supabase" / "reverse" / "0036_pre_snapshot.json"
POST = REPO / "supabase" / "reverse" / "0036_post_snapshot.json"
MIGRATION = REPO / "supabase" / "migrations" / "0036_separate_logins.sql"

# Changing either committed state is a visible change here too.
PRE_SHA256 = "11f4ebefb1ce0e33c41c723dfe800694ff26657c38d3ae10439528b1242a9b2e"
POST_SHA256 = "81bd3359915120f6f948fb42026f7befdcfaf6ccb4acc15d2a044c597e615f9d"

LOGINS = {"docflow_api", "docflow_admin", "docflow_worker", "docflow_stripe"}


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
    return _load("policy_snapshot")


@pytest.fixture(scope="module")
def roundtrip():
    return _load("migration_roundtrip")


@pytest.fixture(scope="module")
def pre(snap):
    return snap.load(PRE)


@pytest.fixture(scope="module")
def post(snap):
    return snap.load(POST)


def _policies(state) -> dict[tuple[str, str], dict]:
    return {(p["table"], p["name"]): p for p in state["policies"]}


def _alter_policy_roles() -> dict[tuple[str, str], list[str]]:
    """(table, policy) -> the roles 0036's own ALTER POLICY statements name."""
    sql = re.sub(r"--[^\n]*", "", MIGRATION.read_text(encoding="utf-8"))
    found = {}
    for name, table, roles in re.findall(r"alter policy (\w+) on (\w+)\s+to ([\w, ]+?)\s*(?:;|with\b)", sql):
        found[(table, name)] = sorted(r.strip() for r in roles.split(","))
    return found


# ── the committed files ──────────────────────────────────────────────────────


def test_each_committed_state_is_in_the_canonical_form_and_has_its_recorded_hash(snap, pre, post):
    for path, state, recorded in ((PRE, pre, PRE_SHA256), (POST, post, POST_SHA256)):
        text = path.read_text(encoding="utf-8")  # universal newlines: a CRLF checkout reads the same
        assert text == snap.canonical(state), f"{path.name} is not in the form policy_snapshot.py writes"
        assert snap.sha256(state) == recorded, f"{path.name} changed: its SHA-256 is recorded in this test"


def test_0036_changes_no_policy_except_the_ones_it_names(pre, post):
    before, after = _policies(pre), _policies(post)
    assert set(after) == set(before), "0036 adds and removes no policy"
    named = _alter_policy_roles()
    assert len(named) == 51 and set(named) <= set(after)
    for key, policy in after.items():
        was = before[key]
        assert policy["cmd"] == was["cmd"] and policy["using"] == was["using"], key
        if key in named:
            assert policy["roles"] == named[key], key
            assert len(policy["roles"]) == 1 and policy["roles"][0] in LOGINS, key
        else:
            assert policy == was, f"{key} is not named by 0036 and must not change"
        if key != ("founder_alerts", "dispatcher_raise"):
            assert policy["with_check"] == was["with_check"], key


def test_the_one_changed_expression_is_dispatcher_raise_gaining_worker_restarting(pre, post):
    key = ("founder_alerts", "dispatcher_raise")
    before, after = _policies(pre)[key]["with_check"], _policies(post)[key]["with_check"]
    assert "worker_restarting" not in before and "worker_restarting" in after


def test_the_function_grants_after_0036_are_exactly_the_approved_ones(pre, post):
    """FUNCTION_GRANTS (test_ci_guards.py) is the list a person wrote; the
    snapshot is what the database showed. They must agree, by function name."""
    after = {signature.split("(", 1)[0]: set(grantees) for signature, grantees in
             post["security_definer_functions"].items()}
    assert len(after) == len(post["security_definer_functions"]), "one function per name"
    expected = {signature.split("(", 1)[0]: grantees for signature, grantees in FUNCTION_GRANTS.items()}
    assert after == expected
    # Every function there before is still there; 0036 adds three.
    before = {signature.split("(", 1)[0] for signature in pre["security_definer_functions"]}
    assert before <= set(after)
    assert set(after) - before == {"record_worker_start", "worker_starts_last_hour", "dispatch_unclaimed_age"}


def test_docflow_app_holds_nothing_after_0036(pre, post, snap):
    assert "docflow_app" in snap.canonical(pre)
    assert "docflow_app" not in snap.canonical(post)
    assert "PUBLIC" not in {g for grantees in post["security_definer_functions"].values() for g in grantees}


# ── the comparison code, on made-up states ───────────────────────────────────


def _state(roles=("docflow_admin",), grantees=("docflow_worker",)):
    return {
        "policies": [
            {"table": "acme_test_table", "name": "acme_test_policy", "cmd": "ALL", "roles": list(roles),
             "using": "true", "with_check": None}
        ],
        "security_definer_functions": {"acme_test_function()": list(grantees)},
    }


def test_equal_states_have_equal_hashes_and_no_differences(snap):
    a, b = _state(), copy.deepcopy(_state())
    assert snap.sha256(a) == snap.sha256(b)
    assert snap.differences("a vs b", a, b) == []


def test_a_different_role_or_grantee_changes_the_hash_and_is_named(snap):
    base = _state()
    for other in (_state(roles=("public",)), _state(grantees=("docflow_app",))):
        assert snap.sha256(other) != snap.sha256(base)
        (line,) = snap.differences("got vs want", other, base)
        assert "acme_test" in line and "got" in line and "want" in line


def test_the_hash_is_over_the_canonical_text_not_the_files_bytes(snap, tmp_path):
    """A CRLF checkout, or different key order, is the same state."""
    state = _state()
    unix, windows, reordered = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "c.json"
    unix.write_bytes(snap.canonical(state).encode("utf-8"))
    windows.write_bytes(snap.canonical(state).replace("\n", "\r\n").encode("utf-8"))
    reordered.write_text(json.dumps(state, sort_keys=False), encoding="utf-8")
    assert len({snap.sha256(snap.load(p)) for p in (unix, windows, reordered)}) == 1


def test_the_command_line_prints_hashes_and_differences(snap, tmp_path, capsys):
    same, other = tmp_path / "same.json", tmp_path / "other.json"
    same.write_text(snap.canonical(_state()), encoding="utf-8")
    other.write_text(snap.canonical(_state(roles=("public",))), encoding="utf-8")
    assert snap.main(["policy_snapshot.py", "--sha256", str(same), str(other)]) == 0
    first, second = capsys.readouterr().out.splitlines()
    assert first.split()[0] == snap.sha256(_state()) and first.split()[0] != second.split()[0]
    assert snap.main(["policy_snapshot.py", "--diff", str(same), str(same)]) == 0
    assert snap.main(["policy_snapshot.py", "--diff", str(other), str(same)]) == 1
    assert "1 difference(s)" in capsys.readouterr().out
    assert snap.main(["policy_snapshot.py"]) == 2


def test_the_round_trip_passes_when_all_three_states_match_the_two_files(roundtrip, snap, capsys):
    forward, before = _state(), _state(roles=("public",))
    code = roundtrip.check_states(forward, before, copy.deepcopy(forward), before, copy.deepcopy(forward))
    out = capsys.readouterr().out
    assert code == 0
    assert f"::notice title=0036 pre-0036 state (after the reverse)::sha256={snap.sha256(before)}" in out
    assert f"::notice title=0036 post-0036 state (forward)::sha256={snap.sha256(forward)}" in out
    assert f"committed 0036_post_snapshot.json sha256={snap.sha256(forward)}" in out
    assert "::error" not in out and "gzip+base64" not in out
    assert "0 difference(s)" in out


def test_a_forward_state_that_differs_from_the_committed_one_fails_and_shows_both(roundtrip, snap, capsys):
    forward, before, committed = _state(), _state(roles=("public",)), _state(grantees=("docflow_app",))
    code = roundtrip.check_states(forward, before, copy.deepcopy(forward), before, committed)
    out = capsys.readouterr().out
    assert code == 1
    assert "::error title=0036 forward state differs from the committed one: 1-1 of 1::" in out
    assert "acme_test_function()" in out
    # What CI found is printed too, and decodes back to exactly that state.
    title = r"::notice title=0036 forward state CI found \(gzip\+base64\) part \d+/\d+::"
    parts = re.findall(title + r"(\S+)", out)
    assert parts and roundtrip.decode_parts(parts) == forward


def test_a_missing_post_file_fails(roundtrip, capsys):
    forward, before = _state(), _state(roles=("public",))
    code = roundtrip.check_states(forward, before, copy.deepcopy(forward), before, None)
    out = capsys.readouterr().out
    assert code == 1
    assert "0036_post_snapshot.json is not in the repository" in out
    assert "sha256=MISSING" in out


def test_a_reverse_that_does_not_restore_the_pre_state_fails(roundtrip, capsys):
    forward, before = _state(), _state(roles=("public",))
    code = roundtrip.check_states(forward, _state(roles=("docflow_api",)), copy.deepcopy(forward), before,
                                  copy.deepcopy(forward))
    out = capsys.readouterr().out
    assert code == 1
    assert "::error title=0036 reverse does not restore the pre-0036 state: 1-1 of 1::" in out


def test_a_second_forward_that_differs_from_the_first_fails(roundtrip, capsys):
    forward, before = _state(), _state(roles=("public",))
    code = roundtrip.check_states(forward, before, _state(grantees=("docflow_api",)), before,
                                  copy.deepcopy(forward))
    out = capsys.readouterr().out
    assert code == 1
    assert "::error title=0036 forward again differs from forward: 1-1 of 1::" in out


def test_a_real_sized_state_survives_the_annotation_encoding(roundtrip, pre):
    parts = roundtrip.encode_parts(pre)
    assert all(len(part) <= 3000 for part in parts) and len(parts) <= 8
    assert roundtrip.decode_parts(parts) == pre


def test_many_differences_fit_githubs_ten_annotations(roundtrip, capsys):
    roundtrip.annotate_problems("made-up differences:", [f"difference {i}" for i in range(57)])
    out = capsys.readouterr().out
    assert out.count("::error") <= 10
    assert all(f"difference {i}" in out for i in range(57))
