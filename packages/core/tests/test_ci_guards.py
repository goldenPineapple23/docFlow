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


def test_the_stripe_event_function_is_granted_the_same_way_in_the_migration_and_in_ci():
    """CI creates `docflow_app` after the migrations run, so migration 0029's
    guarded grant is skipped there and scripts/ci/create_app_role.py grants it
    again (D-173). The two must name the same signature, or CI would test a
    function the app can't call on staging -- or the reverse."""

    def collapse(source: str) -> str:
        # Join adjacent string literals and squeeze whitespace, so a statement
        # split across lines reads as one.
        return re.sub(r"\s+", " ", re.sub(r"['\"]\s*\n?\s*['\"]", "", source))

    grant = re.compile(
        r"grant execute on function record_stripe_subscription_event\s*(\([^)]*\))\s*to docflow_app",
        re.IGNORECASE,
    )
    migration = REPO / "supabase" / "migrations" / "0029_stripe_event_function.sql"
    ci_role = REPO / "scripts" / "ci" / "create_app_role.py"
    signature = "(text, text, timestamptz, text, text, timestamptz, text)"
    assert grant.findall(collapse(migration.read_text(encoding="utf-8"))) == [signature]
    assert grant.findall(collapse(ci_role.read_text(encoding="utf-8"))) == [signature]
