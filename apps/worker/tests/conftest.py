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

