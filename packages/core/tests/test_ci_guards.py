"""
CI keeps its database and its skip check (Phase 5.5 Stage 0, D-148).

For six days in September 2026, CI reported green while 348 of 391 API tests,
the tenant-isolation tests among them, silently skipped for want of a database
(docs/REVIEW-PHASE5.md H7). These tests fail if any Python CI job stops
starting the database, stops connecting as the four logins (Stage 3e), or stops failing on
unapproved skips. Removing one is then a deliberate, visible change to this
file too, not a quiet edit to the workflow.
"""

import ast
import os
import re
import shutil
import subprocess
import time
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


def test_the_core_job_runs_the_0037_round_trip_and_the_skip_check_can_name_it():
    """D-203: 0037's reverse is tested in CI, not just checked in. It runs
    after 0036's round trip (which puts the roles back) and before the suite,
    and a run where it failed says so by name instead of pointing at pytest."""
    block = _job_block(WORKFLOW.read_text(encoding="utf-8"), "core")
    assert (
        block.index("scripts/ci/migration_roundtrip.py")
        < block.index("scripts/ci/migration_0037_roundtrip.py")
        < block.index("id: test\n")
    )
    assert "id: roundtrip_0037\n" in block
    assert "roundtrip_0037" in _check_skips().STEP_NAMES


def test_the_core_job_runs_the_0038_round_trip_after_0037s_and_the_skip_check_can_name_it():
    """D-206: 0038's reverse is tested the same way, after 0037's and before
    the suite, and a run where it failed says so by name."""
    block = _job_block(WORKFLOW.read_text(encoding="utf-8"), "core")
    assert (
        block.index("scripts/ci/migration_0037_roundtrip.py")
        < block.index("scripts/ci/migration_0038_roundtrip.py")
        < block.index("id: test\n")
    )
    assert "id: roundtrip_0038\n" in block
    assert "roundtrip_0038" in _check_skips().STEP_NAMES


def test_the_core_job_runs_the_0040_round_trip_after_0038s_and_the_skip_check_can_name_it():
    """D-232: 0040's reverse is tested the same way, after the earlier round
    trips and before the suite, and a run where it failed says so by name."""
    block = _job_block(WORKFLOW.read_text(encoding="utf-8"), "core")
    assert (
        block.index("scripts/ci/migration_0038_roundtrip.py")
        < block.index("scripts/ci/migration_0040_roundtrip.py")
        < block.index("id: test\n")
    )
    assert "id: roundtrip_0040\n" in block
    assert "roundtrip_0040" in _check_skips().STEP_NAMES


def test_the_core_job_runs_the_0041_round_trip_after_0040s_and_the_skip_check_can_name_it():
    """B1 (D-230): 0041's reverse is tested the same way, after the earlier
    round trips and before the suite, and a run where it failed says so by name."""
    block = _job_block(WORKFLOW.read_text(encoding="utf-8"), "core")
    assert (
        block.index("scripts/ci/migration_0040_roundtrip.py")
        < block.index("scripts/ci/migration_0041_roundtrip.py")
        < block.index("id: test\n")
    )
    assert "id: roundtrip_0041\n" in block
    assert "roundtrip_0041" in _check_skips().STEP_NAMES


PARSE_CONFIG = REPO / "apps" / "parse" / "parse_service" / "config.py"
WORKER_FLY_TOML = REPO / "apps" / "worker" / "fly.toml"
RUN_WORKERS = REPO / "apps" / "worker" / "app" / "run_workers.py"


def _pages_command_strings() -> list[str]:
    """The string elements of run_workers' "pages" command, read from its
    source (core can't import the worker app)."""
    tree = ast.parse(RUN_WORKERS.read_text(encoding="utf-8"))
    commands = [
        value
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and key.value == "pages"
    ]
    assert len(commands) == 1 and isinstance(commands[0], ast.List), "run_workers has no one pages command"
    return [
        element.value
        for element in commands[0].elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    ]


def test_parse_slots_were_sized_for_one_document_slot_and_one_page_job():
    """D-232: PARSE_SLOTS = 2 is one document job plus one page job, and fits
    only while the worker has one document slot. Raising the target without
    revisiting PARSE_SLOTS fails here. It can't see a value set as a Fly
    secret: the worker refuses to start with any value other than this
    default and prints the one it got (app.run_workers; A6, D-236). It also fails if the
    pages worker stops being --concurrency=1, or gains a -c or an
    --autoscale; it can't see a second worker machine."""
    from docflow_core.config import Settings

    found = re.search(r"^PARSE_SLOTS = (\d+)$", PARSE_CONFIG.read_text(encoding="utf-8"), re.M)
    assert found, "PARSE_SLOTS is no longer a plain number in the parse service's config"
    slots = int(found.group(1))
    target = Settings.model_fields["dispatch_in_flight_target"].default
    fly = WORKER_FLY_TOML.read_text(encoding="utf-8")
    assert re.search(r"^\s*DISPATCH_IN_FLIGHT_TARGET\s*=", fly, re.M) is None
    assert (target, slots) == (1, 2), (
        "PARSE_SLOTS = 2 is one document job plus one page job, and fits only while "
        "dispatch_in_flight_target is 1: revisit both together (A3)"
    )
    # render_pages has no per-document lock (founder, 2026-11-04; D-232): it
    # is safe only with one pages process at concurrency 1.
    pages = _pages_command_strings()
    assert [s for s in pages if s.startswith("--concurrency")] == ["--concurrency=1"], (
        "the pages worker must run at --concurrency=1: render_pages has no per-document lock, "
        "and a second render of one document can delete the pages of another (D-232)"
    )
    assert [s for s in pages if s == "-c" or s.startswith("--autoscale")] == [], (
        "the pages worker's concurrency is set by --concurrency=1 alone: no -c and no --autoscale (D-232)"
    )


REVIEW_ROUTER = REPO / "apps" / "api" / "app" / "routers" / "review.py"


def test_the_viewers_text_cut_is_the_text_previews_cut():
    """D-233: the API cuts a TXT, CSV or MD file's stored text where the worker
    cuts every other text preview, so the page says "Cut at 200,000
    characters" truthfully for both. The API may not import
    docflow_core.previews (test_parsing_boundary.py), so it keeps its own
    number, read here from its source."""
    from docflow_core.previews import MAX_PREVIEW_CHARS

    found = re.search(r"^_TEXT_VIEW_CHARS = ([\d_]+)$", REVIEW_ROUTER.read_text(encoding="utf-8"), re.M)
    assert found, "_TEXT_VIEW_CHARS is no longer a plain number in the review router"
    assert int(found.group(1)) == MAX_PREVIEW_CHARS


PAGE_VIEWER = REPO / "apps" / "web" / "src" / "components" / "review" / "PageViewer.tsx"


def test_the_viewers_page_token_lifetime_is_the_apis():
    """D-234: the page viewer asks for fresh links on a page turn 60 s before
    the page token runs out, timed from when the links arrived, so it has to
    know the token's lifetime. It keeps its own copy (the answer carries no
    server time), read here from its source."""
    from docflow_core.signed_urls import DEFAULT_TTL_SECONDS

    found = re.search(
        r"^export const PAGE_TOKEN_LIFETIME_SECONDS = (\d+);$",
        PAGE_VIEWER.read_text(encoding="utf-8"),
        re.M,
    )
    assert found, "PAGE_TOKEN_LIFETIME_SECONDS is no longer a plain number in PageViewer.tsx"
    assert int(found.group(1)) == DEFAULT_TTL_SECONDS


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


def _one_job(workflow: str, job: str) -> str:
    """One job's block, up to the next job's key (whatever its name)."""
    start = workflow.index(f"\n  {job}:\n")
    following = re.search(r"\n  [a-z][a-z0-9_-]*:\n", workflow[start + 1 :])
    return workflow[start : start + 1 + following.start()] if following else workflow[start:]


@pytest.mark.parametrize("job", ["api", "worker"])
def test_the_image_check_runs_last_in_the_api_and_worker_jobs_whatever_the_tests_did(job):
    """D-225 item 6: what the image installs, on its own, after the tests and
    the audit, and not skipped because one of them failed."""
    block = _one_job(WORKFLOW.read_text(encoding="utf-8"), job)
    step = f'scripts/ci/image_check.py" {job}'
    assert step in block, f"CI job {job!r} no longer runs the image check"
    assert block.index(step) > block.index("scripts/ci/pip_audit.py"), "the image check runs after the audit"
    assert "\n      - " not in block[block.index(step) :], "the image check is the job's last step"
    assert "if: (success() || failure()) && steps.deps.outcome == 'success'" in block


def test_the_api_job_starts_the_real_image_and_reads_both_address_families():
    """D-227: the fault that kept Fly's edge out lived in the image's start
    command, which no test ran. A build with a time limit, then the check."""
    block = _one_job(WORKFLOW.read_text(encoding="utf-8"), "api")
    step = 'scripts/ci/api_image_listen.sh" docflow-api:ci'
    build = block.index("name: Build the API's image")
    assert step in block, "the api job no longer reads the image's two address families"
    assert build < block.index(step) < block.index('scripts/ci/image_check.py" api')
    assert "timeout-minutes: 15" in block[build : block.index(step)]
    assert "cache-from: type=gha,scope=api" in block
    assert "cache-to: type=gha,mode=max,scope=api" in block


def test_the_api_image_is_built_only_when_buildx_was_set_up_and_the_skip_check_can_name_it():
    """D-231: the build ran on success() || failure(), so a failed stack start
    skipped buildx and the build went red too, pointing away from the cause
    (push runs #510 and #511). It still runs whatever the tests did."""
    block = _one_job(WORKFLOW.read_text(encoding="utf-8"), "api")
    setup = block.index("- uses: docker/setup-buildx-action@v4\n        id: buildx\n")
    build = block.index("name: Build the API's image")
    assert setup < block.index("id: test\n") < build
    assert "if: (success() || failure()) && steps.buildx.outcome == 'success'" in block[build : build + 200]
    assert "buildx" in _check_skips().STEP_NAMES


API_FLY_TOML = REPO / "apps" / "api" / "fly.toml"


def test_the_public_staging_apis_fly_config_redirects_http_and_never_hides_its_address():
    """Staging's API is public for the demo (D-235 item 2; D-237): http is
    redirected to https, and the documented deploy no longer passes
    --no-public-ips. Its addresses are allocated under a Grant, not here."""
    import tomllib

    text = API_FLY_TOML.read_text(encoding="utf-8")
    config = tomllib.loads(text)
    assert config["app"] == "docflow-api-staging"
    assert config["http_service"]["force_https"] is True
    assert config["env"]["DOCFLOW_ENV"] == "staging"
    assert "fly deploy --config apps/api/fly.toml" in text
    assert "--no-public-ips" not in text


# ── D-238: time limits on every job, the Playwright installs and the uploads ──

INSTALL_SCRIPT = REPO / "scripts" / "ci" / "playwright_install.sh"
CI_JOBS = ("web", "web-live", "core", "api", "worker", "parse")


def _jobs(workflow: str) -> dict[str, str]:
    """Each top-level job's text, by its key (two-space indent under jobs:)."""
    body = workflow[workflow.index("\njobs:\n") :]
    starts = [(m.group(1), m.start()) for m in re.finditer(r"^  ([a-z][a-z0-9_-]*):\n", body, re.M)]
    return {
        name: body[start : starts[i + 1][1] if i + 1 < len(starts) else len(body)]
        for i, (name, start) in enumerate(starts)
    }


def _steps(job: str) -> list[str]:
    """A job's steps, each as its text from `- ` to the next step."""
    marks = [m.start() for m in re.finditer(r"^      - ", job, re.M)]
    return [job[a : marks[i + 1] if i + 1 < len(marks) else len(job)] for i, a in enumerate(marks)]


def test_every_ci_job_has_a_time_limit():
    """A hang ends the job instead of running for hours (#95's web job ran
    1 h 42 min). The values and why each is what it is: D-238 item 3."""
    jobs = _jobs(WORKFLOW.read_text(encoding="utf-8"))
    assert set(jobs) == set(CI_JOBS)
    expected = {"web": 14, "web-live": 15, "core": 7, "api": 21, "worker": 13, "parse": 21}
    for name, block in jobs.items():
        found = re.findall(r"^    timeout-minutes: (\d+)$", block, re.M)
        assert found == [str(expected[name])], (name, found)


def test_every_upload_step_fails_in_5_minutes_and_stays_required():
    """On #97 three uploads hung 11 to 13 minutes. They stay required: no
    continue-on-error (founder, 2026-11-12)."""
    uploads = [
        step
        for block in _jobs(WORKFLOW.read_text(encoding="utf-8")).values()
        for step in _steps(block)
        if "uses: actions/upload-artifact@" in step
    ]
    assert len(uploads) == 3
    for step in uploads:
        assert "\n        timeout-minutes: 5\n" in step, step
        assert "continue-on-error" not in step, step


APT_LIMITS_SCRIPT = REPO / "scripts" / "ci" / "apt_timeouts.sh"
_INSTALL = 'run: bash "${{ github.workspace }}/scripts/ci/playwright_install.sh" '


def _named(block: str, name: str) -> str:
    [step] = [step for step in _steps(block) if f"- name: {name}\n" in step]
    return step


@pytest.mark.parametrize(
    ("job", "browsers", "key", "attempt1"),
    [
        ("web", "chromium webkit", "chromium-webkit", "120"),
        ("web-live", "chromium", "chromium", "120"),
    ],
)
def test_each_playwright_install_is_two_steps_each_retried_inside_its_own_limit(job, browsers, key, attempt1):
    """D-247 (superseding D-238's single step): apt's limits first; the system
    packages as root through the local binary, with root's own HOME and a
    root `timeout` that reaches apt-get; no root-owned file left behind; the
    browser on its own, skipped when the cache by Playwright's version hits."""
    block = _jobs(WORKFLOW.read_text(encoding="utf-8"))[job]
    names = [m.group(1) for m in re.finditer(r"^      - name: (.+)$", block, re.M)]
    order = [
        "apt's own limits (D-247)",
        "Playwright's version",
        "Restore Playwright's browsers",
        "Install Playwright's system packages",
        "No root-owned file left by the system packages step (D-247)",
        "Install Playwright's browsers",
        "Save Playwright's browsers",
    ]
    assert [n for n in names if n in order] == order
    assert "--with-deps" not in block and "npx playwright" not in block

    assert 'scripts/ci/apt_timeouts.sh"' in _named(block, "apt's own limits (D-247)")
    packages = _named(block, "Install Playwright's system packages")
    assert "\n        timeout-minutes: 11\n" in packages and 'PW_APT_RECOVERY: "1"' in packages
    # Each job's own attempt-1 limit: about twice its healthy attempt time
    # (founder, 2026-11-20); attempt 2 keeps the script's 300 s.
    assert f'PW_ATTEMPT1_SECONDS: "{attempt1}"' in packages and "PW_ATTEMPT_SECONDS" not in packages
    # The root `timeout` follows each attempt's limit, 10 s inside it.
    assert (
        _INSTALL + 'sudo env "PATH=$PATH" HOME=/root timeout --kill-after=10 __inner_limit__ '
        f"./node_modules/.bin/playwright install-deps {browsers}\n"
    ) in packages
    assert "find \"$HOME/.npm\" node_modules -user root" in _named(
        block, "No root-owned file left by the system packages step (D-247)"
    )
    browser = _named(block, "Install Playwright's browsers")
    assert "if: steps.pw_cache.outputs.cache-hit != 'true'" in browser
    assert "\n        timeout-minutes: 7\n" in browser and 'PW_ATTEMPT_SECONDS: "180"' in browser
    assert (_INSTALL + f"./node_modules/.bin/playwright install {browsers}\n") in browser
    cache_key = f"key: playwright-${{{{ runner.os }}}}-${{{{ steps.pw_version.outputs.version }}}}-{key}\n"
    for name in ("Restore Playwright's browsers", "Save Playwright's browsers"):
        step = _named(block, name)
        assert cache_key in step and "path: ~/.cache/ms-playwright" in step
        # actions/cache only warns on a cache service error; and the web job
        # has no continue-on-error at all (test_npm_audit.py).
        assert "continue-on-error" not in step


def test_apt_gives_up_on_a_silent_mirror_and_waits_for_a_held_lock():
    """D-247: per file, so the step's limit stays the real bound."""
    script = APT_LIMITS_SCRIPT.read_text(encoding="utf-8")
    for line in (
        'Acquire::Retries "3";',
        'Acquire::http::Timeout "30";',
        'Acquire::https::Timeout "30";',
        'DPkg::Lock::Timeout "180";',
    ):
        assert line in script
    assert "/etc/apt/apt.conf.d/99ci-timeouts" in script


def test_the_install_script_recovers_apt_before_its_retry_only_when_asked():
    """The packages step kills what a failed attempt left running, waits for
    the dpkg lock and finishes an interrupted unpack (D-247); the proof mode
    is off unless set."""
    script = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert 'APT_RECOVERY="${PW_APT_RECOVERY:-0}"' in script
    assert 'PROVE_STALL="${PW_PROVE_STALL:-0}"' in script
    assert 'sudo fuser "${DPKG_LOCK}"' in script and "sudo dpkg --configure -a" in script
    assert "sudo kill -KILL ${leftover}" in script


NEXT_MIRROR_SCRIPT = REPO / "scripts" / "ci" / "apt_next_mirror.sh"


def test_each_packages_attempt_prints_the_mirror_list_and_a_stalled_first_attempt_drops_its_mirror():
    """D-247 item 8 (#582): every packages attempt prints the runner's mirror
    list; only when attempt 1 stopped at its limit (124 or 137) does attempt 2
    run without the first-priority mirror. No apt clean: the archives attempt
    1 downloaded stay. The proof proxy's apt file sorts after 99ci-timeouts,
    so its own timeouts win."""
    script = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert "MIRRORS=/etc/apt/apt-mirrors.txt" in script
    assert '    before=$(apt_pids)\n    show_mirrors "${attempt}"\n' in script
    assert (
        '    if [ "${APT_RECOVERY}" = "1" ] && '
        '{ [ "${status}" -eq 124 ] || [ "${status}" -eq 137 ]; }; then\n'
        "      drop_first_mirror\n"
    ) in script
    assert 'bash "${NEXT_MIRROR}" "${MIRRORS}"' in script and 'sudo tee "${MIRRORS}"' in script
    assert "apt-get clean" not in script and "apt clean" not in script
    assert "PROVE_CONF=/etc/apt/apt.conf.d/99zz-ci-prove-stall" in script
    assert "99zz-ci-prove-stall" > "99ci-timeouts"


def test_no_mirror_address_is_written_into_the_ci_files():
    """D-247 item 8: the retry uses the runner's own list; the repo names no
    mirror."""
    for path in [WORKFLOW, *sorted((REPO / "scripts" / "ci").glob("*.sh"))]:
        assert not re.search(r"[a-z0-9-]+\.ubuntu\.com", path.read_text(encoding="utf-8")), path.name


def _next_mirror(tmp_path: Path, listing: str) -> subprocess.CompletedProcess:
    mirrors = tmp_path / "apt-mirrors.txt"
    mirrors.write_bytes(listing.encode("utf-8"))
    return subprocess.run(
        [_bash(), str(NEXT_MIRROR_SCRIPT), str(mirrors)], capture_output=True, text=True, timeout=30
    )


@pytest.mark.parametrize(
    ("listing", "left", "message"),
    [
        (
            "http://slow.example/ubuntu/\tpriority:1\nhttps://next.example/ubuntu/\tpriority:2\n"
            "https://third.example/ubuntu/\tpriority:3\n",
            "https://next.example/ubuntu/\tpriority:2\nhttps://third.example/ubuntu/\tpriority:3\n",
            "removed the first-priority mirror: http://slow.example/ubuntu/\tpriority:1",
        ),
        (
            "https://b.example/\tpriority:5\nhttp://a.example/\tpriority:2\n",
            "https://b.example/\tpriority:5\n",
            "removed the first-priority mirror: http://a.example/\tpriority:2",
        ),
        (
            "# comment\nhttp://x.example/\n\nhttp://y.example/\n",
            "# comment\n\nhttp://y.example/\n",
            "removed the first-priority mirror: http://x.example/",
        ),
        (
            "http://only.example/\tpriority:1\n",
            "http://only.example/\tpriority:1\n",
            "only one mirror listed; nothing removed",
        ),
    ],
)
def test_the_next_mirror_list_drops_only_the_first_priority_mirror(tmp_path, listing, left, message):
    done = _next_mirror(tmp_path, listing)
    assert done.returncode == 0, done.stderr
    assert done.stdout == left
    assert done.stderr.strip() == message


def test_the_next_mirror_list_refuses_without_a_file(tmp_path):
    done = subprocess.run(
        [_bash(), str(NEXT_MIRROR_SCRIPT), str(tmp_path / "missing.txt")],
        capture_output=True, text=True, timeout=30,
    )
    assert done.returncode == 2 and "give one mirror list file" in done.stderr


def _bash() -> str:
    found = shutil.which("bash")
    assert found, "bash is needed to run scripts/ci/playwright_install.sh"
    return found


def _install(
    command: list[str], tmp_path: Path, attempt_seconds: int = 300, attempt1_seconds: int | None = None
) -> subprocess.CompletedProcess:
    env = {**os.environ, "PW_ATTEMPT_SECONDS": str(attempt_seconds), "PW_WAIT_SECONDS": "0"}
    env.pop("PW_ATTEMPT1_SECONDS", None)
    if attempt1_seconds is not None:
        env["PW_ATTEMPT1_SECONDS"] = str(attempt1_seconds)
    return subprocess.run(
        [_bash(), str(INSTALL_SCRIPT), *command],
        capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60,
    )


def test_the_install_script_tries_again_once_and_says_which_attempt(tmp_path):
    flag = tmp_path / "failed-once"
    done = _install(["bash", "-c", f"[ -f {flag.name} ] && exit 0; touch {flag.name}; exit 7"], tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "attempt 1 of 2" in done.stdout and "attempt 2 of 2: done" in done.stdout
    assert "::warning title=Playwright install::attempt 1 of 2 failed (exit 7)" in done.stdout


def test_the_install_script_fails_loudly_after_two_attempts(tmp_path):
    done = _install(["false"], tmp_path)
    assert done.returncode == 1
    assert done.stdout.count("::warning title=Playwright install::") == 2
    assert "::error title=Playwright install::both attempts failed" in done.stdout


def test_the_install_script_stops_a_hung_attempt_at_its_limit(tmp_path):
    started = time.monotonic()
    done = _install(["sleep", "50"], tmp_path, attempt_seconds=12)
    assert done.returncode == 1
    assert done.stdout.count("stopped at its 12 s limit") == 2
    assert time.monotonic() - started < 40


def test_attempt_1_has_its_own_limit_and_attempt_2_keeps_the_shared_one(tmp_path):
    """Founder, 2026-11-20: each job's attempt 1 is cut at its own limit; a
    retry still gets PW_ATTEMPT_SECONDS."""
    started = time.monotonic()
    done = _install(["sleep", "50"], tmp_path, attempt_seconds=14, attempt1_seconds=11)
    assert done.returncode == 1
    assert "attempt 1 of 2 (at most 11 s)" in done.stdout and "attempt 2 of 2 (at most 14 s)" in done.stdout
    assert "attempt 1 of 2 failed (stopped at its 11 s limit)" in done.stdout
    assert "attempt 2 of 2 failed (stopped at its 14 s limit)" in done.stdout
    assert "both attempts failed (attempt 1 at most 11 s, attempt 2 at most 14 s)" in done.stdout
    assert time.monotonic() - started < 50


def test_the_inner_limit_follows_each_attempt_and_names_the_limit_that_fired(tmp_path):
    """The `__inner_limit__` argument becomes each attempt's limit less 10 s,
    and a stop by it is reported as that limit, not the outer one: #595
    said "stopped at its 300 s limit" after 290 s, when the fixed inner 290
    had fired."""
    started = time.monotonic()
    done = _install(
        ["timeout", "__inner_limit__", "sleep", "50"], tmp_path, attempt_seconds=13, attempt1_seconds=12
    )
    assert done.returncode == 1
    assert "attempt 1 of 2 (at most 2 s)" in done.stdout and ": timeout 2 sleep 50" in done.stdout
    assert "attempt 2 of 2 (at most 3 s)" in done.stdout and ": timeout 3 sleep 50" in done.stdout
    assert "attempt 1 of 2 failed (stopped at its 2 s limit) after " in done.stdout
    assert "attempt 2 of 2 failed (stopped at its 3 s limit) after " in done.stdout
    assert "__inner_limit__" not in done.stdout
    assert time.monotonic() - started < 20


@pytest.mark.parametrize("bad", ["10", "0", "abc"])
def test_an_attempt_limit_at_or_under_the_inner_margin_is_refused(tmp_path, bad):
    done = _install(["true"], tmp_path, attempt1_seconds=bad)  # type: ignore[arg-type]
    assert done.returncode == 2
    assert "an attempt limit must be a whole number of seconds above 10" in done.stdout

