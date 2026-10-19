# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
PR 1b-ii (D-207) through the API, against the real database:

- VAL-017 and VAL-018, what an .eml or .msg unwrap couldn't read: the review
  screen shows VAL-017 with the attachment's name filled in, never
  `{filename}`; a validation re-run (an edit) leaves both alone; approval
  refuses until each is acknowledged, and the acknowledgement clears them.
- IMP-011, an import whose parse job failed on our side: an `error` answer,
  a crash, or an answer the worker refused. The import fails with IMP-011
  and the keyed `import_failed` alert names the import; the Console shows it
  as "Catalog import, no document". A job stopped at a limit stays IMP-004,
  with no such alert.

The parse answer is replaced here; the parse service is tested in apps/parse.
All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import pytest
from docflow_core import parse_client
from docflow_core.db import platform_session, tenant_session
from docflow_core.validation import record_parse_warnings, validate_document
from sqlalchemy import text

from tests.conftest import requires_review_schema
from tests.test_catalog_import_api import (  # noqa: F401 -- fixtures
    CATALOG_V1,
    _capture_enqueue,
    _import,
    requires_imports_schema,
)
from tests.test_console_api import (  # noqa: F401 -- fixtures
    _Console,
    _environment,
    stripe,
)
from tests.test_review_api import (  # noqa: F401 -- fixtures
    CLEAN_HEADER,
    CLEAN_LINES,
    _approve,
    _open_warnings,
    _ReviewTenant,
    _secrets,
    _status,
    _stored_warning,
)

# The longest label the parse service sends: its sanitized alphabet, the
# last 80 characters (B3: the review screen's layout must hold it).
LONG_LABEL = "Purchase_Order_" + "x" * 61 + ".zip"
LOST = [{"code": "VAL-017", "filename": LONG_LABEL}, {"code": "VAL-018", "filename": None}]


def _with_parse_warnings(tenant) -> str:
    document = tenant.create_document(header=CLEAN_HEADER, lines=CLEAN_LINES)
    with tenant_session(tenant.tenant_id) as session:
        record_parse_warnings(session, tenant.tenant_id, document, LOST)
        validate_document(session, tenant.tenant_id, document)
    return document


@requires_review_schema
def test_val_017_shows_the_attachments_name_never_the_placeholder(client):
    assert len(LONG_LABEL) == 80
    with _ReviewTenant("Acme Test Distributor -- unwrap name") as tenant:
        document = _with_parse_warnings(tenant)
        warnings = {w["code"]: w for w in _open_warnings(client, tenant, document)}
        assert set(warnings) == {"VAL-017", "VAL-018"}
        val_017 = warnings["VAL-017"]
        assert f'the attachment "{LONG_LABEL}".' in val_017["message"]
        assert "{filename}" not in val_017["message"]
        assert val_017["severity"] == "high" and val_017["title"] == "An email attachment couldn't be read"
        assert warnings["VAL-018"]["message"].startswith("The text of this email couldn't be read.")


@requires_review_schema
def test_an_edit_re_runs_validation_and_leaves_the_parse_warnings_open(client):
    with _ReviewTenant("Acme Test Distributor -- unwrap revalidate") as tenant:
        document = _with_parse_warnings(tenant)
        before = {w["code"]: w["id"] for w in _open_warnings(client, tenant, document)}

        detail = client.get(f"/review/documents/{document}", headers=tenant.headers()).json()
        assert (
            client.patch(
                f"/review/documents/{document}",
                headers=tenant.headers(),
                json={"header": {"payment_terms": "Net 45"}, "expected_version": detail["version"]},
            ).status_code
            == 200
        )
        with tenant_session(tenant.tenant_id) as session:
            validate_document(session, tenant.tenant_id, document)

        after = {w["code"]: w["id"] for w in _open_warnings(client, tenant, document)}
        assert after == before, "a validation re-run resolved a warning only the parse step can raise"


@requires_review_schema
def test_approval_needs_each_acknowledged_and_the_acknowledgement_clears_them(client):
    with _ReviewTenant("Acme Test Distributor -- unwrap approve") as tenant:
        document = _with_parse_warnings(tenant)
        warnings = _open_warnings(client, tenant, document)

        refused = _approve(client, tenant, document, [])
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "REV-001"

        assert _approve(client, tenant, document, [w["id"] for w in warnings]).status_code == 200
        assert _status(document) == "approved"
        for warning in warnings:
            assert _stored_warning(document, warning["id"])["status"] == "acknowledged"

        # The acknowledgement is recorded with the text as the reviewer read
        # it (7.3): the attachment's name, never the placeholder.
        with platform_session() as session:
            recorded = session.execute(
                text(
                    "SELECT warning_acknowledgements FROM review_actions "
                    "WHERE document_id = :d AND action = 'approved'"
                ),
                {"d": str(document)},
            ).scalar_one()
        texts = " ".join(item["text"] for item in recorded)
        assert LONG_LABEL in texts and "{filename}" not in texts


# ── IMP-011 ──────────────────────────────────────────────────────────────────


def _import_alerts(tenant_id: str) -> list[dict]:
    with platform_session() as session:
        rows = session.execute(
            text(
                "SELECT id, severity, payload, state_key FROM founder_alerts "
                "WHERE tenant_id = :t AND type = 'import_failed' ORDER BY created_at"
            ),
            {"t": tenant_id},
        ).mappings().all()
    return [dict(row) for row in rows]


def _answering(monkeypatch, answer=None, refuse: str | None = None) -> None:
    def parse_table(content, filename):
        if refuse is not None:
            raise parse_client.ParseAnswerInvalid(refuse)
        return answer

    monkeypatch.setattr(parse_client, "parse_table", parse_table)


@requires_imports_schema
@pytest.mark.parametrize(
    "answer,refuse,exception_type,location,release",
    [
        (
            parse_client.ParseAnswer(
                "error", error_type="TypeError", location="parse_service.parsing.tables:parse_table:157",
                release="deployment-P7",
            ),
            None, "TypeError", "parse_service.parsing.tables:parse_table:157", "deployment-P7",
        ),
        (
            parse_client.ParseAnswer("crashed", cause="signal_11", release="deployment-P7"),
            None, "crashed", "parse_service.job:signal_11:0", "deployment-P7",
        ),
        (None, "bad_table", "invalid_answer", "docflow_core.parse_client:bad_table:0", "unknown"),
    ],
    ids=["error", "crashed", "refused"],
)
def test_an_import_that_failed_on_our_side_is_imp_011_with_the_keyed_alert(
    client, stripe, monkeypatch, answer, refuse, exception_type, location, release
):
    _answering(monkeypatch, answer, refuse)
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        preview = _import(client, console, tenant_id, CATALOG_V1)
        assert preview["status"] == "failed"
        assert preview["error"]["code"] == "IMP-011"
        assert preview["error"]["title"] == "Reading this file failed on our side"

        (alert,) = _import_alerts(tenant_id)
        assert alert["severity"] == "high"
        payload = alert["payload"]
        assert payload["import_id"] == preview["id"] and "first_document_id" not in payload
        assert (payload["step"], payload["error_code"]) == ("catalog_import", "IMP-011")
        assert (payload["exception_type"], payload["location"], payload["release"]) == (
            exception_type, location, release
        )
        assert alert["state_key"].startswith(f"fault:import_failed:{tenant_id}:catalog_import:")

        listed = [
            a for a in client.get("/admin/alerts", headers=console.headers()).json()["alerts"]
            if a["id"] == str(alert["id"])
        ]
        assert len(listed) == 1
        assert listed[0]["no_document"] == "catalog_import"
        assert listed[0]["affected_documents"] is None


@requires_imports_schema
def test_the_same_fault_on_a_second_import_raises_nothing_new(client, stripe, monkeypatch):
    _answering(
        monkeypatch,
        parse_client.ParseAnswer(
            "error", error_type="TypeError", location="parse_service.parsing.tables:parse_table:157",
            release="deployment-P7",
        ),
    )
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        first = _import(client, console, tenant_id, CATALOG_V1)
        second = _import(client, console, tenant_id, CATALOG_V1)
        assert first["error"]["code"] == second["error"]["code"] == "IMP-011"
        (alert,) = _import_alerts(tenant_id)
        assert alert["payload"]["import_id"] == first["id"]


@requires_imports_schema
def test_a_stopped_import_job_stays_imp_004_with_no_import_alert(client, stripe, monkeypatch):
    _answering(monkeypatch, parse_client.ParseAnswer("stopped", cause="memory"))
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        preview = _import(client, console, tenant_id, CATALOG_V1)
        assert (preview["status"], preview["error"]["code"]) == ("failed", "IMP-004")
        assert _import_alerts(tenant_id) == []
