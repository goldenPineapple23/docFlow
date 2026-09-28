import base64

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


def review_schema_available() -> bool:
    """
    True once supabase/migrations/0007_review_and_approval.sql has been
    applied (the `review_actions` and `document_snapshots` tables, the
    approval columns on `documents`, and the acknowledgement link on
    `document_warnings`). Same manual-application constraint as 0002-0006 --
    the docflow_app role has no CREATE privilege (D-013/D-017) -- so tests
    needing these skip cleanly until the founder applies it (D-083).
    """
    if not validation_schema_available():
        return False
    from docflow_core.db import get_engine
    from sqlalchemy import text as _text

    try:
        with get_engine().connect() as conn:
            conn.execute(
                _text(
                    # `sequence` arrives in 0008 and the trail read depends on
                    # it (D-084), so both migrations gate these tests together.
                    "SELECT id, tenant_id, document_id, user_id, acting_as_tenant_id, action, "
                    "changes, warning_acknowledgements, note, deleted_at, sequence "
                    "FROM review_actions LIMIT 0"
                )
            )
            conn.execute(
                _text(
                    "SELECT id, tenant_id, document_id, review_action_id, snapshot, "
                    "snapshot_sha256, superseded_at FROM document_snapshots LIMIT 0"
                )
            )
            conn.execute(
                _text(
                    "SELECT approved_at, approved_by, approved_snapshot_hash, review_started_at "
                    "FROM documents LIMIT 0"
                )
            )
            conn.execute(
                _text("SELECT acknowledged_review_action_id FROM document_warnings LIMIT 0")
            )
            # 0009: the review API reads these on every document detail and
            # viewer request, so the review tests gate on it too (D-092).
            conn.execute(
                _text(
                    "SELECT preview_storage_path, preview_media_type, preview_kind "
                    "FROM documents LIMIT 0"
                )
            )
        return True
    except Exception:
        return False


requires_review_schema = pytest.mark.skipif(
    not review_schema_available(),
    reason=(
        "supabase/migrations/0007, 0008 and 0009 have "
        "not all been applied to this database yet -- see SETUP.md Step 5 / "
        "DECISIONS.md D-083, D-084 and D-092."
    ),
)


def exports_schema_available() -> bool:
    """
    True once supabase/migrations/0010_exports.sql has been applied. Same
    manual-application constraint as every migration before it (D-013/D-017),
    so the export tests skip cleanly until the founder applies it (D-098).
    """
    if not review_schema_available():
        return False
    from docflow_core.db import get_engine
    from sqlalchemy import text as _text

    try:
        with get_engine().connect() as conn:
            conn.execute(
                _text(
                    "SELECT id, tenant_id, document_id, snapshot_id, format, status, storage_path, "
                    "sha256, byte_size, snapshot_hash, error_code, generated_by, "
                    "acting_as_tenant_id, requested_at, generated_at, deleted_at "
                    "FROM exports LIMIT 0"
                )
            )
        return True
    except Exception:
        return False


requires_exports_schema = pytest.mark.skipif(
    not exports_schema_available(),
    reason=(
        "supabase/migrations/0010_exports.sql has not been applied to this database yet "
        "-- see SETUP.md Step 5 / DECISIONS.md D-098."
    ),
)


def console_schema_available() -> bool:
    """
    True once supabase/migrations/0011_console_foundations.sql has been
    applied (tiers, onboarding intakes, email outbox, founder alerts). Tests
    of the Phase 5 Console skip cleanly until the founder applies it (D-102).
    """
    if not database_available():
        return False
    from docflow_core.db import get_engine
    from sqlalchemy import text as _text

    try:
        with get_engine().connect() as conn:
            for statement in (
                "SELECT id, code, version, is_current, document_allowance FROM tiers LIMIT 0",
                "SELECT id, prospect_name, linked_tenant_id FROM onboarding_intakes LIMIT 0",
                "SELECT id, intake_id, storage_path FROM onboarding_intake_files LIMIT 0",
                "SELECT id, template, status FROM email_outbox LIMIT 0",
                "SELECT id, type, dedupe_key, acknowledged_at FROM founder_alerts LIMIT 0",
                "SELECT tier_id, onboarding_intake_id FROM tenants LIMIT 0",
            ):
                conn.execute(_text(statement))
        return True
    except Exception:
        return False


requires_console_schema = pytest.mark.skipif(
    not console_schema_available(),
    reason=(
        "supabase/migrations/0011_console_foundations.sql has not been applied to this database "
        "yet -- see SETUP.md Step 5 / DECISIONS.md D-102."
    ),
)


# ── The inbound-mail webhook's credentials (review finding H8) ──────────────
#
# Inbound mail now needs the provider's own HTTP Basic credentials, separately
# from the per-tenant token in the URL (which is public by design). Configured
# here for the whole API suite so every existing intake test keeps exercising
# what it was written to exercise; the tests that are *about* this check clear
# or change them for themselves.
#
# Obviously fake, and never a real secret (CLAUDE.md Section 0 rule 4, 7.10).
INTAKE_WEBHOOK_USERNAME = "docflow-test-inbound"
INTAKE_WEBHOOK_PASSWORD = "not-a-real-secret-only-a-test-credential"


def intake_webhook_headers(
    username: str = INTAKE_WEBHOOK_USERNAME, password: str = INTAKE_WEBHOOK_PASSWORD
) -> dict[str, str]:
    """The Authorization header Postmark's webhook URL would carry."""
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


@pytest.fixture(autouse=True)
def _console_mfa_off_unless_a_test_asks(monkeypatch):
    """
    Every test starts with CONSOLE_MFA_ENFORCED off, whatever the developer's
    .env says; the tests about enforcement turn it on themselves (the
    `enforced` fixture in test_console_mfa.py, which runs after this). With it
    read from .env, switching it on locally (RUNBOOK 4.1) failed 134 Console
    tests with AUTH-006 while CI, which doesn't set it, stayed green.
    """
    monkeypatch.setenv("CONSOLE_MFA_ENFORCED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _inbound_webhook_credentials(monkeypatch):
    monkeypatch.setenv("POSTMARK_WEBHOOK_USERNAME", INTAKE_WEBHOOK_USERNAME)
    monkeypatch.setenv("POSTMARK_WEBHOOK_PASSWORD", INTAKE_WEBHOOK_PASSWORD)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def cleans_up_refusal_alerts():
    """
    A refused inbound request raises a real `intake_webhook_refused` founder
    alert (D-171). On staging that lands in the founder's attention panel, so a
    test that sends refused requests removes the alerts -- and their emails --
    that it created, and nothing that existed before it ran. A no-op when there
    is no database.
    """
    from docflow_core.db import platform_session
    from sqlalchemy import text

    def existing() -> set[str]:
        with platform_session() as session:
            return {
                str(row[0])
                for row in session.execute(
                    text("SELECT id FROM founder_alerts WHERE type = 'intake_webhook_refused'")
                )
            }

    try:
        before = existing()
    except Exception:
        yield
        return
    yield
    created = existing() - before
    if not created:
        return
    # One row at a time: a bound Python list is sent as JSON by this engine,
    # which Postgres will not cast to uuid[] (found on CI's first run).
    with platform_session() as session:
        for alert_id in sorted(created):
            outbox_id = session.execute(
                text("DELETE FROM founder_alerts WHERE id = :id RETURNING email_outbox_id"),
                {"id": alert_id},
            ).scalar()
            if outbox_id is not None:
                session.execute(text("DELETE FROM email_outbox WHERE id = :id"), {"id": outbox_id})


@pytest.fixture(autouse=True)
def _console_mfa_notice_already_raised(monkeypatch, request):
    """
    While CONSOLE_MFA_ENFORCED is off, the first Console request of a process
    raises a real founder alert (D-177). In a test run that would be whichever
    Console test happens to go first, leaving an alert behind on staging -- so
    every test starts with the notice marked as already raised. The tests that
    are about the notice (marked `console_mfa_notice`) reset it themselves and
    remove what they raise.
    """
    if request.node.get_closest_marker("console_mfa_notice"):
        return
    from app.routers import admin

    monkeypatch.setattr(admin, "_console_mfa_off_noticed", True)
