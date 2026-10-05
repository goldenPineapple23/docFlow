import pytest
from docflow_core.config import get_settings


def database_available() -> bool:
    return bool(get_settings().database_url)


# ── One test run at a time against a shared database (tests/suite_lock.py) ──
# A refused run writes no JUnit report; the lock's own annotation says why.
def pytest_sessionstart(session):
    if session.config.option.collectonly or not database_available():
        return
    from tests import suite_lock

    suite_lock.session_start(get_settings().database_url, suite="worker")


def pytest_sessionfinish(session, exitstatus):
    from tests import suite_lock

    suite_lock.session_finish()


def documents_schema_available() -> bool:
    """
    True once supabase/migrations/0002_documents.sql has actually been
    applied to the connected database. The `docflow_app` role has no CREATE
    privilege on the public schema (by design -- see SETUP.md/DECISIONS.md
    D-013), so this migration can't be applied programmatically the way
    these tests run; it's applied the same manual way 0001 was (SETUP.md
    Step 5). Tests that need the real table skip cleanly until then instead
    of failing on a missing-relation error.
    """
    if not database_available():
        return False
    from docflow_core.db import get_engine
    from sqlalchemy import text

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1 FROM documents LIMIT 0"))
        return True
    except Exception:
        return False


requires_database = pytest.mark.skipif(
    not database_available(),
    reason="DATABASE_URL is not set -- see SETUP.md Step 1.",
)

requires_documents_schema = pytest.mark.skipif(
    not documents_schema_available(),
    reason=(
        "supabase/migrations/0002_documents.sql has not been applied to this database yet "
        "-- see SETUP.md Step 5 / DECISIONS.md."
    ),
)


def timeout_schema_available() -> bool:
    """True once supabase/migrations/0030_timeouts_and_stripe_cancel.sql has
    been applied (documents.timeout_attempts, tenants.stripe_cancel_pending_at).
    Applied by hand on staging like every migration (D-013); CI applies it
    itself, so these tests never skip there."""
    if not database_available():
        return False
    from docflow_core.db import get_engine
    from sqlalchemy import text

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT timeout_attempts FROM documents LIMIT 0"))
            conn.execute(text("SELECT stripe_cancel_pending_at FROM tenants LIMIT 0"))
        return True
    except Exception:
        return False


requires_timeout_schema = pytest.mark.skipif(
    not timeout_schema_available(),
    reason=(
        "supabase/migrations/0030_timeouts_and_stripe_cancel.sql has not been applied "
        "to this database yet."
    ),
)


def stage3c_schema_available() -> bool:
    """True once supabase/migrations/0034_parse_lost_and_started_runs.sql has
    been applied (documents.parse_lost_attempts, extraction_runs.run_state).
    Applied by hand on staging like every migration (D-013); CI applies it
    itself, so these tests never skip there."""
    if not database_available():
        return False
    from docflow_core.db import get_engine
    from sqlalchemy import text

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT parse_lost_attempts FROM documents LIMIT 0"))
            conn.execute(text("SELECT run_state, started_run_id FROM extraction_runs LIMIT 0"))
        return True
    except Exception:
        return False


requires_stage3c_schema = pytest.mark.skipif(
    not stage3c_schema_available(),
    reason=(
        "supabase/migrations/0034_parse_lost_and_started_runs.sql has not been applied to this database yet."
    ),
)


def stage3d_schema_available() -> bool:
    """True once supabase/migrations/0035_dispatcher_and_waits.sql has been
    applied (documents.dispatched_at, the dispatcher and provider functions).
    Applied by hand on staging like every migration (D-013); CI applies it
    itself, so these tests never skip there."""
    if not database_available():
        return False
    from docflow_core.db import get_engine
    from sqlalchemy import text

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT dispatched_at, dispatch_lane, wait_cause FROM documents LIMIT 0"))
            conn.execute(text("SELECT * FROM public.dispatcher_status()"))
        return True
    except Exception:
        return False


requires_stage3d_schema = pytest.mark.skipif(
    not stage3d_schema_available(),
    reason="supabase/migrations/0035_dispatcher_and_waits.sql has not been applied to this database yet.",
)


@pytest.fixture(autouse=True)
def _no_real_dispatch_or_provider_state(request, monkeypatch):
    """
    Stage 3d: the document task ends with a dispatch pass and records the
    model provider's state -- both real, global writes. A test that drives the
    task must never mark the shared database's provider down, or dispatch
    other tests' documents, by accident. So by default they are stubbed, and
    the calls are recorded on `request.node.dispatch_calls`; a test that
    proves the real thing says so with @pytest.mark.real_dispatch and calls
    it directly.
    """
    if request.node.get_closest_marker("real_dispatch"):
        return
    calls: list[tuple] = []
    request.node.dispatch_calls = calls
    from docflow_core import model_provider

    import app.tasks.parse_and_extract as task

    monkeypatch.setattr(task, "dispatch_after_task", lambda: calls.append(("dispatch",)))
    monkeypatch.setattr(model_provider, "record_success", lambda: calls.append(("success",)) or False)
    monkeypatch.setattr(
        model_provider, "record_failure", lambda error: calls.append(("failure", error)) or False
    )
    monkeypatch.setattr(
        model_provider, "alert_routing_failure", lambda error: calls.append(("routing_failure", error))
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_dispatch: the test drives the real dispatcher and provider state (Stage 3d)"
    )


# ── Stage 3c: the parse service every document test reads through ──────────
import os  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
import urllib.request  # noqa: E402
from pathlib import Path  # noqa: E402

PARSE_DIR = Path(__file__).resolve().parents[2] / "parse"
PARSE_FIXTURES = PARSE_DIR / "tests" / "fixtures"
DEV_PARSE_TOKEN = "worker-tests-dev-parse-token"


def fixture_bytes(relative: str) -> bytes:
    """One of the parse service's committed fixtures (real files, fixed hashes)."""
    return (PARSE_FIXTURES / relative).read_bytes()


def _parse_python() -> str:
    for candidate in (PARSE_DIR / ".venv" / "Scripts" / "python.exe", PARSE_DIR / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    raise RuntimeError(
        "No parse service to test against: set PARSE_SERVICE_URL (CI: the real image) or create "
        "apps/parse/.venv (SETUP.md) so the dev service can be started."
    )


@pytest.fixture(scope="session", autouse=True)
def parse_service():
    """
    The parse service the task under test calls (Stage 3c). CI points
    PARSE_SERVICE_URL at the real image (isolation on); otherwise the dev
    service is started here from apps/parse/.venv (isolation off: the same
    code, without the sandbox -- logic only, never proof of isolation).
    """
    if os.environ.get("PARSE_SERVICE_URL"):
        get_settings.cache_clear()
        yield os.environ["PARSE_SERVICE_URL"]
        return
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("FLY_")}
    env.update(
        PARSE_ISOLATION="off", PARSE_SERVICE_TOKEN=DEV_PARSE_TOKEN, PORT=str(port), DOCFLOW_ENV="development"
    )
    proc = subprocess.Popen([_parse_python(), "-m", "parse_service.server"], env=env, cwd=str(PARSE_DIR))
    url = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1):
                break
        except OSError:
            time.sleep(0.1)
    os.environ["PARSE_SERVICE_URL"] = url
    os.environ["PARSE_SERVICE_TOKEN"] = DEV_PARSE_TOKEN
    get_settings.cache_clear()
    try:
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        os.environ.pop("PARSE_SERVICE_URL", None)
        os.environ.pop("PARSE_SERVICE_TOKEN", None)
        get_settings.cache_clear()


# ── Stage 3e: the suite leaves `worker_starts` as it found it ──────────────
# (founder, 2026-10-05; RUNBOOK 10.2 step 5.) `worker_starts` has no delete
# path: no policy, no function. Until this date the restart-record tests
# committed six starts a run, and each counts toward `worker_restarting`
# (three in an hour) on a database a real worker shares. Now a test that
# records a start does it inside `rolled_back_worker_starts`, and the session
# checks itself at the end: no test kept a start, and the one number any login
# can read (the starts in the last 60 minutes) did not go up.
#
# One test commits a start on purpose, to show it in the count /healthz reads.
# It runs only where the database is thrown away with the job
# (`throwaway_ci_database`), and the session expects exactly that one.
from collections.abc import Callable, Iterator  # noqa: E402
from contextlib import contextmanager  # noqa: E402


class WorkerStartsCheck:
    """What the session knows about the starts its own tests recorded."""

    def __init__(self) -> None:
        self.rolled_back = False  # a rolled_back_worker_starts block is open
        self.kept: list[str] = []  # tests that recorded a start outside one
        self.committing = False  # a worker_starts_committed_on_ci block is open
        self.committed = 0  # starts committed inside one (CI's own database only)
        self.before: int | None = None  # starts in the last 60 minutes, as the Console reads them
        self.after: int | None = None
        self.not_read: str | None = None  # why the count could not be read, if it couldn't

    def watch(self, record_start: Callable[[], int | None]) -> Callable[[], int | None]:
        def recorded() -> int | None:
            starts = record_start()
            if starts is None or self.rolled_back:
                return starts
            if self.committing:
                self.committed += 1
            else:
                self.kept.append(os.environ.get("PYTEST_CURRENT_TEST", "a test"))
            return starts

        return recorded

    def problems(self) -> list[str]:
        out = [f"recorded a worker start outside rolled_back_worker_starts: {test}" for test in self.kept]
        if (
            self.before is not None
            and self.after is not None
            and self.after > self.before + self.committed
        ):
            out.append(
                f"worker_starts_last_hour() went from {self.before} to {self.after} during the run, with "
                f"{self.committed} committed on purpose: a start was committed that should not have been "
                "(rows only leave that count by ageing out)"
            )
        return out

    def summary(self) -> str:
        if self.not_read:
            return f"worker_starts: count not read ({self.not_read}); starts kept by tests: {len(self.kept)}"
        return (
            f"worker_starts in the last 60 minutes: before {self.before}, after {self.after}; "
            f"starts kept by tests: {len(self.kept)}; committed on CI's own database: {self.committed}"
        )


WORKER_STARTS_CHECK = WorkerStartsCheck()


def worker_starts_last_hour() -> int:
    """The starts in the last 60 minutes, read as the Console would (docflow_admin)."""
    from docflow_core.db import platform_session
    from sqlalchemy import text

    with platform_session() as session:
        return int(session.execute(text("SELECT public.worker_starts_last_hour()")).scalar_one())


@contextmanager
def worker_starts_rolled_back() -> Iterator[None]:
    """
    Every start recorded inside this block is in ONE transaction on the
    worker's own login, rolled back when the block ends. `record_start()`
    still opens the real `dispatcher_session()`; only the connection under it
    is this one, so each "commit" releases a savepoint and nothing more. The
    real function, the real `dispatcher_raise` policy and the real unique
    index all run. Nothing is left in `worker_starts`, `founder_alerts` or
    `email_outbox`, and nothing that was already there is touched.
    """
    from docflow_core import db
    from sqlalchemy.orm import sessionmaker

    connection = db.engine_for("worker").connect()
    outer = connection.begin()
    joined = sessionmaker(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
    real_factory = db._session_factory

    def factory(login: str):
        return joined if login == "worker" else real_factory(login)

    db._session_factory = factory
    WORKER_STARTS_CHECK.rolled_back = True
    try:
        yield
    finally:
        WORKER_STARTS_CHECK.rolled_back = False
        db._session_factory = real_factory
        outer.rollback()
        connection.close()


_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def is_throwaway_ci_database(github_actions: str | None, worker_database_url: str) -> bool:
    """Both must hold: this is a GitHub Actions runner, AND the worker's login
    points at the runner's own Postgres. docflow-staging and production are
    never on a loopback address, so neither can pass, whatever is set."""
    from sqlalchemy.engine import make_url

    if github_actions != "true" or not worker_database_url:
        return False
    try:
        return make_url(worker_database_url).host in _LOOPBACK
    except Exception:
        return False


def throwaway_ci_database() -> bool:
    from docflow_core import db

    return is_throwaway_ci_database(os.environ.get("GITHUB_ACTIONS"), db.login_url("worker"))


requires_throwaway_ci_database = pytest.mark.skipif(
    not throwaway_ci_database(),
    reason=(
        "commits a worker start, which nothing can delete: runs only on CI's own database "
        "(GitHub Actions and a loopback database host); .github/approved-skips.txt, D-189"
    ),
)


@contextmanager
def worker_starts_committed_on_ci() -> Iterator[None]:
    """The one place a test may commit a start. Refuses anywhere but CI's own
    database, checked again here, at the moment of committing."""
    assert throwaway_ci_database(), "refusing to commit a worker start: this is not CI's own database"
    WORKER_STARTS_CHECK.committing = True
    try:
        yield
    finally:
        WORKER_STARTS_CHECK.committing = False


@pytest.fixture
def rolled_back_worker_starts() -> Iterator[None]:
    with worker_starts_rolled_back():
        yield


@pytest.fixture(scope="session", autouse=True)
def _worker_starts_left_as_found():
    if not database_available():
        yield
        return
    from docflow_core import worker_starts

    check = WORKER_STARTS_CHECK
    try:
        check.before = worker_starts_last_hour()
    except Exception as exc:  # a database without 0036, or no admin login here
        check.not_read = type(exc).__name__
    real = worker_starts.record_start
    worker_starts.record_start = check.watch(real)
    try:
        yield
    finally:
        worker_starts.record_start = real
    if check.not_read is None:
        check.after = worker_starts_last_hour()
    problems = check.problems()
    assert not problems, "the suite changed worker_starts:\n" + "\n".join(problems)


def pytest_terminal_summary(terminalreporter):
    check = WORKER_STARTS_CHECK
    if check.before is not None or check.not_read:  # not on a collect-only run
        terminalreporter.write_line(check.summary())
