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
