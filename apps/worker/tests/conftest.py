import pytest
from docflow_core.config import get_settings


def database_available() -> bool:
    return bool(get_settings().database_url)


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
