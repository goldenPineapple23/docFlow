import pytest
from docflow_core.config import get_settings
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def database_available() -> bool:
    return bool(get_settings().database_url)


requires_database = pytest.mark.skipif(
    not database_available(),
    reason=(
        "DATABASE_URL is not set -- these tests need the real docflow-staging Postgres "
        "connection to prove RLS/tenant-isolation behavior for real, not a mock. "
        "See SETUP.md Step 1. Per CLAUDE.md Section 0 rule 5, we never fake this to keep moving."
    ),
)


def documents_schema_available() -> bool:
    """
    True once supabase/migrations/0002_documents.sql has been applied. The
    `docflow_app` role has no CREATE privilege on the public schema by
    design (SETUP.md/DECISIONS.md D-013), so this migration is applied the
    same manual way 0001 was (SETUP.md Step 5), not programmatically by
    these tests. Tests needing the real documents/document_headers/
    document_lines/intake_rejections tables skip cleanly until then.
    """
    if not database_available():
        return False
    from docflow_core.db import get_engine
    from sqlalchemy import text as _text

    try:
        with get_engine().connect() as conn:
            conn.execute(_text("SELECT 1 FROM documents LIMIT 0"))
        return True
    except Exception:
        return False


requires_documents_schema = pytest.mark.skipif(
    not documents_schema_available(),
    reason=(
        "supabase/migrations/0002_documents.sql has not been applied to this database yet "
        "-- see SETUP.md Step 5 / DECISIONS.md."
    ),
)


def email_intake_schema_available() -> bool:
    """
    True once supabase/migrations/0003_email_intake.sql has been applied
    (the new raw_emails table, plus the quarantine/sender_email/message_id
    columns on documents). Same manual-application constraint as 0002 (the
    docflow_app role has no CREATE privilege -- see D-013/D-017); tests
    needing these skip cleanly until then.
    """
    if not database_available():
        return False
    from docflow_core.db import get_engine
    from sqlalchemy import text as _text

    try:
        with get_engine().connect() as conn:
            conn.execute(_text("SELECT 1 FROM raw_emails LIMIT 0"))
            conn.execute(_text("SELECT quarantine_reason, sender_email, message_id FROM documents LIMIT 0"))
        return True
    except Exception:
        return False


requires_email_intake_schema = pytest.mark.skipif(
    not email_intake_schema_available(),
    reason=(
        "supabase/migrations/0003_email_intake.sql has not been applied to this database yet "
        "-- see SETUP.md Step 5 / DECISIONS.md."
    ),
)


def matching_schema_available() -> bool:
    """
    True once supabase/migrations/0004_matching_foundations.sql has been
    applied (buyers, buyer_merge_candidates, items, learned_rules,
    extraction_runs, plus document_headers.buyer_id). Same manual-application
    constraint as 0002/0003 -- the docflow_app role has no CREATE privilege
    (D-013/D-017) -- so tests needing these skip cleanly until the founder
    applies it.
    """
    if not database_available():
        return False
    from docflow_core.db import get_engine
    from sqlalchemy import text as _text

    try:
        with get_engine().connect() as conn:
            conn.execute(_text("SELECT 1 FROM buyers LIMIT 0"))
            conn.execute(_text("SELECT 1 FROM buyer_merge_candidates LIMIT 0"))
            conn.execute(_text("SELECT buyer_id, field_provenance FROM document_headers LIMIT 0"))
        return True
    except Exception:
        return False


requires_matching_schema = pytest.mark.skipif(
    not matching_schema_available(),
    reason=(
        "supabase/migrations/0004_matching_foundations.sql has not been applied to this database "
        "yet -- see SETUP.md Step 5 / DECISIONS.md."
    ),
)


def sku_matching_schema_available() -> bool:
    """
    True once supabase/migrations/0005_sku_matching.sql has been applied (the
    match result columns on `document_lines`). Same manual-application
    constraint as 0002/0003/0004 -- the docflow_app role has no CREATE
    privilege (D-013/D-017) -- so tests needing these skip cleanly until the
    founder applies it.
    """
    if not matching_schema_available():
        return False
    from docflow_core.db import get_engine
    from sqlalchemy import text as _text

    try:
        with get_engine().connect() as conn:
            conn.execute(
                _text(
                    "SELECT matched_item_id, match_method, match_score, matched_uom, "
                    "uom_mismatch, match_candidates, matched_at FROM document_lines LIMIT 0"
                )
            )
            conn.execute(_text("SELECT 1 FROM items LIMIT 0"))
            conn.execute(_text("SELECT 1 FROM learned_rules LIMIT 0"))
        return True
    except Exception:
        return False


requires_sku_matching_schema = pytest.mark.skipif(
    not sku_matching_schema_available(),
    reason=(
        "supabase/migrations/0005_sku_matching.sql has not been applied to this database "
        "yet -- see SETUP.md Step 5 / DECISIONS.md."
    ),
)


def validation_schema_available() -> bool:
    """
    True once supabase/migrations/0006_validation_and_duplicates.sql has been
    applied (the `document_warnings` table and
    `documents.change_order_of_document_id`). Same manual-application
    constraint as 0002-0005 -- the docflow_app role has no CREATE privilege
    (D-013/D-017) -- so tests needing these skip cleanly until the founder
    applies it (D-079).
    """
    if not sku_matching_schema_available():
        return False
    from docflow_core.db import get_engine
    from sqlalchemy import text as _text

    try:
        with get_engine().connect() as conn:
            conn.execute(
                _text(
                    "SELECT id, tenant_id, document_id, document_line_id, code, severity, "
                    "field_name, line_number, detail, fingerprint, status, acknowledged_by, "
                    "acknowledged_at, resolved_at FROM document_warnings LIMIT 0"
                )
            )
            conn.execute(
                _text(
                    "SELECT is_possible_duplicate, duplicate_of_document_id, "
                    "is_possible_change_order, change_order_of_document_id FROM documents LIMIT 0"
                )
            )
        return True
    except Exception:
        return False


requires_validation_schema = pytest.mark.skipif(
    not validation_schema_available(),
    reason=(
        "supabase/migrations/0006_validation_and_duplicates.sql has not been applied to this "
        "database yet -- see SETUP.md Step 5 / DECISIONS.md D-079."
    ),
)
