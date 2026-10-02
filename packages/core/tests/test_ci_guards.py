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


def test_every_approved_skip_has_a_reason():
    approvals = REPO / ".github" / "approved-skips.txt"
    for raw in approvals.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entry, _, reason = line.partition("#")
        assert reason.strip(), f"approved skip without a reason: {raw!r}"
        assert entry.split()[0] in PYTHON_JOBS, f"unknown suite in {raw!r}"


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
