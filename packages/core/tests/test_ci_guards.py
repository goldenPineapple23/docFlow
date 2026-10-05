"""
CI keeps its database and its skip check (Phase 5.5 Stage 0, D-148).

For six days in September 2026, CI reported green while 348 of 391 API tests,
the tenant-isolation tests among them, silently skipped for want of a database
(docs/REVIEW-PHASE5.md H7). These tests fail if any Python CI job stops
starting the database, stops connecting as the four logins (Stage 3e), or stops failing on
unapproved skips. Removing one is then a deliberate, visible change to this
file too, not a quiet edit to the workflow.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

PYTHON_JOBS = ("core", "api", "worker")
REQUIRED_STEPS = (
    "supabase start",
    "scripts/ci/create_logins.py",
    "pytest -v --junitxml=junit.xml",
    "scripts/ci/check_skips.py",
)


def _job_block(workflow: str, job: str) -> str:
    start = workflow.index(f"\n  {job}:\n")
    later = [
        workflow.find(f"\n  {other}:\n", start + 1)
        for other in ("web", *PYTHON_JOBS)
        if other != job
    ]
    later = [i for i in later if i > start]
    return workflow[start : min(later) if later else len(workflow)]


@pytest.mark.parametrize("job", PYTHON_JOBS)
def test_every_python_ci_job_runs_against_a_database_and_fails_on_unapproved_skips(job):
    block = _job_block(WORKFLOW.read_text(encoding="utf-8"), job)
    for step in REQUIRED_STEPS:
        assert step in block, f"CI job {job!r} no longer runs {step!r}"
    assert "if: success() || failure()" in block, (
        f"CI job {job!r}: the skip check must run after a failure too"
    )


def test_ci_connects_as_the_four_logins():
    """Stage 3e (F-1): each session connects as its own login, as on staging.
    The worker job's own login is docflow_worker, as on Fly."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for setting, login in (
        ("DATABASE_URL", "docflow_api"),
        ("API_DATABASE_URL", "docflow_api"),
        ("WORKER_DATABASE_URL", "docflow_worker"),
        ("ADMIN_DATABASE_URL", "docflow_admin"),
        ("STRIPE_DATABASE_URL", "docflow_stripe"),
    ):
        assert f"\n  {setting}: postgresql://{login}:" in workflow, setting
    assert "      DATABASE_URL: postgresql://docflow_worker:" in _job_block(workflow, "worker")
    assert "docflow_app" not in workflow


def test_the_core_job_runs_the_0036_round_trip_before_the_logins_are_turned_on():
    """Founder's Q5 condition 2: forward -> reverse -> forward, in CI. The
    reverse drops the roles, so it must come before their passwords are set."""
    block = _job_block(WORKFLOW.read_text(encoding="utf-8"), "core")
    assert block.index("scripts/ci/migration_roundtrip.py") < block.index("scripts/ci/create_logins.py")


# A skip on docflow-staging only (founder, 2026-10-05; D-189). No CI job reads
# these lines, so the test still has to run in CI.
STAGING_ONLY = tuple(f"{job}-staging" for job in PYTHON_JOBS)
APPROVALS = REPO / ".github" / "approved-skips.txt"


def _approval_lines() -> list[tuple[str, str, str]]:
    """(suite, test id, reason) for each line of the approvals file."""
    found = []
    for raw in APPROVALS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entry, _, reason = line.partition("#")
        suite, test_id = entry.split(None, 1)
        found.append((suite, test_id.strip(), reason.strip()))
    return found


def test_every_approved_skip_has_a_reason():
    for suite, test_id, reason in _approval_lines():
        assert reason, f"approved skip without a reason: {suite} {test_id}"
        assert suite in PYTHON_JOBS + STAGING_ONLY, f"unknown suite in {suite} {test_id}"


# Who may execute each SECURITY DEFINER function (3e design, A3). The
# database test (test_logins_db.py) checks the same table against the live
# grants; this one checks migration 0036 says it, so a reviewer reads one file.
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


def test_migration_0036_grants_each_function_to_exactly_its_logins():
    source = (REPO / "supabase" / "migrations" / "0036_separate_logins.sql").read_text(encoding="utf-8")
    found: dict[str, set[str]] = {}
    for function, grantees in re.findall(
        r"^grant execute on function (\S+\(.*?\)) to ([\w, ]+);$", source, flags=re.M
    ):
        found.setdefault(function, set()).update(g.strip() for g in grantees.split(","))
    assert found == FUNCTION_GRANTS
    # Every one is revoked from docflow_app and PUBLIC first, in the same list.
    revoked = re.search(r"foreach f in array array\[(.*?)\] loop", source, flags=re.S)
    assert revoked is not None
    assert set(re.findall(r"'([^']+)'", revoked.group(1))) == set(FUNCTION_GRANTS)


def _check_skips():
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_skips", REPO / "scripts" / "ci" / "check_skips.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("job", PYTHON_JOBS)
def test_the_skip_check_is_told_which_step_failed(job):
    """A missing test report names the setup step that failed (2026-09-29),
    which needs the step ids and CI_STEPS in every Python job."""
    block = _job_block(WORKFLOW.read_text(encoding="utf-8"), job)
    assert "CI_STEPS: ${{ toJSON(steps) }}" in block
    for step_id in ("supabase", "deps", "core", "app_role", "ruff", "mypy", "test"):  # + roundtrip in core
        assert f"id: {step_id}\n" in block, f"CI job {job!r} lost the step id {step_id!r}"


def test_a_missing_report_names_the_setup_step_that_failed():
    reason = _check_skips().no_report_reason(
        "junit.xml",
        '{"supabase": {"outcome": "failure", "conclusion": "failure"}, "test": {"outcome": "skipped"}}',
    )
    assert reason.startswith('pytest never ran: the step "Start the local Supabase stack" failed first')


def test_a_missing_report_after_pytest_started_points_at_the_test_step():
    reason = _check_skips().no_report_reason(
        "junit.xml", '{"supabase": {"outcome": "success"}, "test": {"outcome": "failure"}}'
    )
    assert "stopped before or while collecting tests" in reason


@pytest.mark.parametrize("steps_json", ["", "not json", '{"pip": {"outcome": "failure"}}'])
def test_a_missing_report_with_no_named_failure_says_where_to_look(steps_json):
    reason = _check_skips().no_report_reason("junit.xml", steps_json)
    assert "first failed step in the job's step list" in reason


def test_a_staging_only_skip_is_never_approved_for_ci():
    """`worker-staging` lets a test skip on docflow-staging. The same test
    skipped in CI must still fail the job: no CI line for it, and the skip
    check itself must not read the staging line as an approval."""
    lines = _approval_lines()
    for_ci = {(suite, test_id) for suite, test_id, _ in lines if suite in PYTHON_JOBS}
    staging_only = [(suite, test_id) for suite, test_id, _ in lines if suite in STAGING_ONLY]
    for suite, test_id in staging_only:
        job = suite.removesuffix("-staging")
        assert (job, test_id) not in for_ci, f"{test_id} may skip on staging only, not in CI"
        assert test_id not in _check_skips().read_approvals(APPROVALS, job)


def test_the_committed_worker_start_test_is_the_staging_only_skip():
    committed = "tests.test_worker_starts_db::test_a_committed_start_appears_in_the_count_healthz_reads"
    assert ("worker-staging", committed) in {(suite, test_id) for suite, test_id, _ in _approval_lines()}
    source = (REPO / "apps" / "worker" / "tests" / "test_worker_starts_db.py").read_text(encoding="utf-8")
    assert f"@requires_throwaway_ci_database\ndef {committed.split('::')[1]}(" in source


# ── a staging-only skip must run and pass in CI (check_skips.py) ─────────────

MUST_RUN = "tests.test_acme::test_commits_on_ci_only"


def _report(tmp_path, body: str) -> Path:
    path = tmp_path / "junit.xml"
    path.write_text(f"<testsuites><testsuite>{body}</testsuite></testsuites>", encoding="utf-8")
    return path


def _approvals(tmp_path) -> Path:
    path = tmp_path / "approved-skips.txt"
    path.write_text(f"worker-staging {MUST_RUN}  # acme test reason\n", encoding="utf-8")
    return path


def _case(name: str, inner: str = "") -> str:
    return f'<testcase classname="tests.test_acme" name="{name}" time="0.412">{inner}</testcase>'


def test_a_staging_only_test_that_ran_and_passed_in_ci_is_shown_on_the_run_page(tmp_path, capsys):
    report = _report(tmp_path, _case("test_commits_on_ci_only") + _case("test_other"))
    code = _check_skips().main(["check_skips.py", "worker", str(report), str(_approvals(tmp_path))])
    out = capsys.readouterr().out
    assert code == 0
    assert f"::notice title=worker: must run in CI::{MUST_RUN}: ran and passed (0.412s)." in out
    assert "::error" not in out


@pytest.mark.parametrize(
    ("body", "shown"),
    [
        (_case("test_commits_on_ci_only", '<skipped message="acme test: not CI"/>'), "skipped"),
        (_case("test_commits_on_ci_only", '<failure message="acme test: assert 1 == 2"/>'), "failed"),
        (_case("test_commits_on_ci_only", '<error message="acme test: teardown"/>'), "failed"),
        (_case("test_other"), "not in the report"),
    ],
)
def test_a_staging_only_test_that_did_not_pass_in_ci_fails_the_job(tmp_path, capsys, body, shown):
    code = _check_skips().main(
        ["check_skips.py", "worker", str(_report(tmp_path, body)), str(_approvals(tmp_path))]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert f"::error title=worker: must run in CI::{MUST_RUN}: {shown}." in out
    assert "::notice title=worker: must run in CI" not in out


def test_another_suites_staging_only_line_asks_nothing_of_this_job(tmp_path, capsys):
    report = _report(tmp_path, _case("test_other"))
    code = _check_skips().main(["check_skips.py", "api", str(report), str(_approvals(tmp_path))])
    assert code == 0
    assert "must run in CI" not in capsys.readouterr().out


def test_ci_must_run_the_committed_worker_start_test():
    committed = "tests.test_worker_starts_db::test_a_committed_start_appears_in_the_count_healthz_reads"
    assert _check_skips().read_approvals(APPROVALS, "worker-staging") == {committed}
