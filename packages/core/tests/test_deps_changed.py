"""
scripts/ci/deps_changed.sh (founder, 2026-10-23; D-219): whether a run's
commits changed an app's dependency files, which decides whether its
dependency audit blocks. Each test builds a throwaway git repository and
runs the real script with bash: a change means yes, an unrelated change
means no, and whenever it can't tell -- another event, an all-zero, missing
or unfetchable `before`, a missing parent -- it blocks.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = (REPO / "scripts" / "ci" / "deps_changed.sh").as_posix()


def _bash() -> str | None:
    """bash on PATH, as on CI's Linux runners. On Windows, Git for Windows'
    own bash, found from git: a Windows PATH usually has git but not its
    bash, and WSL's bash.exe is a different system."""
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    for parent in Path(git).parents if git else []:
        candidate = parent / "bin" / "bash.exe"
        if candidate.exists():
            return str(candidate)
    return None


BASH = _bash()
LOCK = "apps/web/package-lock.json"
PACKAGE = "apps/web/package.json"
FILES = [PACKAGE, LOCK]


@pytest.fixture()
def env(tmp_path) -> dict[str, str]:
    """Git with no user or system config of this machine, and an obviously
    fake identity."""
    config = tmp_path / "gitconfig"
    config.write_text("", encoding="utf-8")
    base = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GITHUB_"))}
    return {
        **base,
        "GIT_CONFIG_GLOBAL": str(config),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
        "GIT_AUTHOR_NAME": "Acme Test",
        "GIT_AUTHOR_EMAIL": "acme-test@example.invalid",
        "GIT_COMMITTER_NAME": "Acme Test",
        "GIT_COMMITTER_EMAIL": "acme-test@example.invalid",
    }


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _commit(repo: Path, env: dict[str, str], path: str, content: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, env, "add", path)
    _git(repo, env, "commit", "-q", "-m", f"change {path}")
    return _git(repo, env, "rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path, env) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, env, "init", "-q", "-b", "main")
    for name in (*FILES, "README.md"):
        _commit(path, env, name, f"{name} v1\n")
    return path


def _run(
    cwd: Path, env: dict[str, str], tmp_path: Path, event: str, before: str = "", sha: str = "", files=FILES
):
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    run_env = {**env, "EVENT": event, "BEFORE": before, "SHA": sha, "GITHUB_OUTPUT": out.as_posix()}
    done = subprocess.run([BASH, SCRIPT, *files], cwd=cwd, env=run_env, capture_output=True, text=True)
    lines = out.read_text(encoding="utf-8").splitlines()
    return done.returncode, dict(line.split("=", 1) for line in lines), done.stderr


def _pull_request(repo: Path, env: dict[str, str], path: str) -> None:
    """GitHub's checkout of a pull request: a merge commit whose first parent
    is the base branch, after the base has moved on."""
    _git(repo, env, "checkout", "-q", "-b", "feature")
    _commit(repo, env, path, f"{path} v2\n")
    _git(repo, env, "checkout", "-q", "main")
    _commit(repo, env, "docs/other.md", "the base branch moved on\n")
    _git(repo, env, "merge", "-q", "--no-ff", "-m", "merge", "feature")


def test_bash_is_there():
    assert BASH, "the dependency audit's change check needs bash"


def test_a_pull_request_that_changes_the_lock_file_blocks(repo, env, tmp_path):
    _pull_request(repo, env, LOCK)
    code, out, _ = _run(repo, env, tmp_path, "pull_request")
    assert code == 0
    assert out == {"blocking": "yes", "changed": LOCK}


def test_a_pull_request_that_changes_something_else_does_not_block(repo, env, tmp_path):
    _pull_request(repo, env, "apps/web/src/page.tsx")
    code, out, why = _run(repo, env, tmp_path, "pull_request")
    assert code == 0
    assert out == {"blocking": "no", "changed": ""}
    assert "none of them changed" in why


def test_the_base_branch_moving_on_is_not_the_pull_requests_change(repo, env, tmp_path):
    """Only the merge commit's first parent counts as the base: a lock change
    that landed on main meanwhile isn't this pull request's."""
    _git(repo, env, "checkout", "-q", "-b", "feature")
    _commit(repo, env, "apps/web/src/page.tsx", "v2\n")
    _git(repo, env, "checkout", "-q", "main")
    _commit(repo, env, LOCK, "changed on main meanwhile\n")
    _git(repo, env, "merge", "-q", "--no-ff", "-m", "merge", "feature")
    _, out, _ = _run(repo, env, tmp_path, "pull_request")
    assert out["blocking"] == "no"


def test_a_push_that_changes_both_files_blocks_and_names_them(repo, env, tmp_path):
    before = _git(repo, env, "rev-parse", "HEAD")
    _commit(repo, env, PACKAGE, "v2\n")
    sha = _commit(repo, env, LOCK, "v2\n")
    code, out, _ = _run(repo, env, tmp_path, "push", before, sha)
    assert code == 0
    assert out["blocking"] == "yes"
    assert sorted(out["changed"].split()) == sorted(FILES)


def test_a_push_that_changes_something_else_does_not_block(repo, env, tmp_path):
    before = _git(repo, env, "rev-parse", "HEAD")
    sha = _commit(repo, env, "README.md", "v2\n")
    _, out, _ = _run(repo, env, tmp_path, "push", before, sha)
    assert out == {"blocking": "no", "changed": ""}


@pytest.mark.parametrize("before", ["", "0" * 40])
def test_a_push_with_no_previous_commit_blocks(before, repo, env, tmp_path):
    sha = _git(repo, env, "rev-parse", "HEAD")
    _, out, why = _run(repo, env, tmp_path, "push", before, sha)
    assert out["blocking"] == "yes"
    assert "no previous commit" in why


def test_a_push_whose_previous_commit_cannot_be_fetched_blocks(repo, env, tmp_path):
    sha = _git(repo, env, "rev-parse", "HEAD")
    _, out, why = _run(repo, env, tmp_path, "push", "1234567890abcdef1234567890abcdef12345678", sha)
    assert out["blocking"] == "yes"
    assert "can't be fetched" in why


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch", "workflow_call", ""])
def test_any_other_event_blocks(event, repo, env, tmp_path):
    _, out, why = _run(repo, env, tmp_path, event)
    assert out["blocking"] == "yes"
    assert "always blocks" in why


def test_a_pull_request_checkout_without_the_parent_blocks(tmp_path, env):
    """fetch-depth 1 would leave no first parent: git can't compare, so it
    blocks."""
    path = tmp_path / "single"
    path.mkdir()
    _git(path, env, "init", "-q", "-b", "main")
    _commit(path, env, LOCK, "v1\n")
    _, out, why = _run(path, env, tmp_path, "pull_request")
    assert out["blocking"] == "yes"
    assert "can't compare" in why


def test_outside_a_git_repository_it_blocks(tmp_path, env):
    plain = tmp_path / "plain"
    plain.mkdir()
    _, out, why = _run(plain, env, tmp_path, "pull_request")
    assert out["blocking"] == "yes"
    assert "not in a git repository" in why


def test_it_runs_from_any_directory_inside_the_repository(repo, env, tmp_path):
    """CI runs it from each job's working directory (packages/core, apps/api,
    ...); the paths are always from the repository root."""
    _pull_request(repo, env, LOCK)
    _, out, _ = _run(repo / "apps" / "web", env, tmp_path, "pull_request")
    assert out == {"blocking": "yes", "changed": LOCK}


def test_no_paths_is_a_usage_error(repo, env, tmp_path):
    code, out, _ = _run(repo, env, tmp_path, "pull_request", files=[])
    assert code == 2
    assert out == {}
