"""
CI keeps its database and its skip check (Phase 5.5 Stage 0, D-148).

For six days in September 2026, CI reported green while 348 of 391 API tests,
the tenant-isolation tests among them, silently skipped for want of a database
(docs/REVIEW-PHASE5.md H7). These tests fail if any Python CI job stops
starting the database, stops connecting as `docflow_app`, or stops failing on
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
    "scripts/ci/create_app_role.py",
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


def test_ci_connects_as_the_non_bypassing_app_role():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "DATABASE_URL: postgresql://docflow_app:" in workflow


def test_every_approved_skip_has_a_reason():
    approvals = REPO / ".github" / "approved-skips.txt"
    for raw in approvals.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entry, _, reason = line.partition("#")
        assert reason.strip(), f"approved skip without a reason: {raw!r}"
        assert entry.split()[0] in PYTHON_JOBS, f"unknown suite in {raw!r}"


@pytest.mark.parametrize(
    ("function", "migration_file", "signature"),
    [
        (
            "record_stripe_subscription_event",
            "0029_stripe_event_function.sql",
            "(text, text, timestamptz, text, text, timestamptz, text)",
        ),
        ("record_stripe_card_event", "0031_card_billing.sql", "(text, text, text, text, bigint)"),
    ],
)
def test_each_stripe_event_function_is_granted_the_same_way_in_the_migration_and_in_ci(
    function, migration_file, signature
):
    """CI creates `docflow_app` after the migrations run, so a migration's
    guarded grant is skipped there and scripts/ci/create_app_role.py grants it
    again (D-173, D-181). The two must name the same signature, or CI would
    test a function the app can't call on staging -- or the reverse."""

    def collapse(source: str) -> str:
        # Join adjacent string literals and squeeze whitespace, so a statement
        # split across lines reads as one.
        return re.sub(r"\s+", " ", re.sub(r"['\"]\s*\n?\s*['\"]", "", source))

    grant = re.compile(
        rf"grant execute on function {function}\s*(\([^)]*\))\s*to docflow_app",
        re.IGNORECASE,
    )
    migration = REPO / "supabase" / "migrations" / migration_file
    ci_role = REPO / "scripts" / "ci" / "create_app_role.py"
    assert grant.findall(collapse(migration.read_text(encoding="utf-8"))) == [signature]
    assert grant.findall(collapse(ci_role.read_text(encoding="utf-8"))) == [signature]


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
    for step_id in ("supabase", "deps", "core", "app_role", "ruff", "mypy", "test"):
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
