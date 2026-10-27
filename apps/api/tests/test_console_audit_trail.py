# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
D-178 (D-220), against the real database: the Console's audit trail.

1. A Console request refused at the gate -- AUTH-006 (no authenticator on the
   sign-in) or AUTH-007 (the code is too old for a destructive action) -- is
   recorded as a `console_refused` row, with the same 403 as before. At most
   CONSOLE_REFUSAL_ALERT_COUNT rows per account per window; the row that
   reaches the count raises one high-severity founder alert; refusals past it
   are logged only. Another account is counted on its own.
2. Every Console write leaves an `outcome` row pointing back at its intent:
   succeeded, or failed with its catalog code (or a bare status). A request
   that crashed midway leaves none, and the Audit tab says the outcome is
   unknown -- never that it was done. The seven same-transaction actions
   commit their request row first, so a stop between it and the work reads
   the same way.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

import pytest
from docflow_core import admin_data_access
from docflow_core.constants import CONSOLE_REFUSAL_ALERT_COUNT
from docflow_core.db import platform_session
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.errors import catalog_detail
from app.main import app
from app.routers import admin
from tests.conftest import requires_database
from tests.test_console_api import _Console, _environment, stripe  # noqa: F401
from tests.test_console_mfa import LIMIT, _headers, enforced  # noqa: F401

pytestmark = [requires_database]


def _rows(sql: str, **params) -> list[dict]:
    with platform_session() as session:
        return [dict(r) for r in session.execute(text(sql), params).mappings().all()]


def _refusals(console: _Console) -> list[dict]:
    return _rows(
        "SELECT target_tenant_id, payload FROM admin_actions "
        "WHERE platform_admin_user_id = :u AND action = 'console_refused' ORDER BY created_at",
        u=str(console.user_id),
    )


def _intent_and_outcome(console: _Console, action: str) -> tuple[dict, dict | None]:
    intents = _rows(
        "SELECT id, target_tenant_id, target_id, payload FROM admin_actions "
        "WHERE platform_admin_user_id = :u AND action = :a",
        u=str(console.user_id),
        a=action,
    )
    assert len(intents) == 1, intents
    outcomes = _rows(
        "SELECT target_tenant_id, payload FROM admin_actions "
        "WHERE action = 'outcome' AND payload->>'intent_id' = :i",
        i=str(intents[0]["id"]),
    )
    assert len(outcomes) <= 1, outcomes
    return intents[0], (outcomes[0] if outcomes else None)


@pytest.fixture
def refusal_alerts():
    """Remove the refusal alerts (and their emails) a test raises, and nothing
    that existed before it: the run's leftover counts must read +0."""

    def ids() -> set[str]:
        return {
            str(r["id"])
            for r in _rows("SELECT id FROM founder_alerts WHERE type = 'console_refusals_repeated'")
        }

    before = ids()
    yield
    with platform_session() as session:
        for alert_id in sorted(ids() - before):
            outbox_id = session.execute(
                text("DELETE FROM founder_alerts WHERE id = :id RETURNING email_outbox_id"), {"id": alert_id}
            ).scalar()
            if outbox_id is not None:
                session.execute(text("DELETE FROM email_outbox WHERE id = :id"), {"id": outbox_id})


def _alerts_for(console: _Console) -> int:
    return len(
        _rows(
            "SELECT id FROM founder_alerts WHERE type = 'console_refusals_repeated' "
            "AND severity = 'high' AND tenant_id IS NULL AND payload->>'platform_admin_user_id' = :u",
            u=str(console.user_id),
        )
    )


# ── 1. Refusals ───────────────────────────────────────────────────────────────


def test_auth_006_is_recorded_once_with_its_code_and_path_and_the_same_403(client, enforced, _environment):
    with _Console() as console:
        response = client.get("/admin/tiers", headers=_headers(console.auth_user_id, aal="aal1"))
        rows = _refusals(console)
    assert response.status_code == 403
    assert response.json() == {"detail": catalog_detail("AUTH-006")}
    assert len(rows) == 1
    assert rows[0]["target_tenant_id"] is None
    assert rows[0]["payload"] == {
        "code": "AUTH-006",
        "method": "GET",
        "path_template": "/admin/tiers",
        "mfa_challenge_age_seconds": None,
    }


def test_auth_007_is_recorded_with_the_tenant_and_the_codes_age(
    client, enforced, stripe, monkeypatch, _environment
):
    with _Console() as console:
        monkeypatch.setenv("CONSOLE_MFA_ENFORCED", "false")
        from docflow_core.config import get_settings

        get_settings.cache_clear()
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        monkeypatch.setenv("CONSOLE_MFA_ENFORCED", "true")
        get_settings.cache_clear()

        response = client.post(
            f"/admin/tenants/{tenant_id}/intake-address/rotate",
            headers=_headers(console.auth_user_id, aal="aal2", totp_age=LIMIT + 1),
        )
        rows = _refusals(console)
    assert response.status_code == 403
    assert response.json() == {"detail": catalog_detail("AUTH-007")}
    assert len(rows) == 1
    assert str(rows[0]["target_tenant_id"]) == tenant_id
    assert rows[0]["payload"]["code"] == "AUTH-007"
    assert rows[0]["payload"]["method"] == "POST"
    assert rows[0]["payload"]["path_template"] == "/admin/tenants/{tenant_id}/intake-address/rotate"
    assert rows[0]["payload"]["mfa_challenge_age_seconds"] >= LIMIT + 1


def test_the_403_stands_when_recording_the_refusal_fails(client, enforced, monkeypatch, caplog, _environment):
    def broken(**kw):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(admin.admin_data_access, "record_console_refusal", broken)
    with _Console() as console, caplog.at_level(logging.ERROR, logger="docflow.api"):
        response = client.get("/admin/tiers", headers=_headers(console.auth_user_id, aal="aal1"))
    assert response.status_code == 403
    assert response.json() == {"detail": catalog_detail("AUTH-006")}
    assert "console_refusal_record_failed code=AUTH-006" in caplog.text
    assert "error_type=RuntimeError" in caplog.text


def test_five_refusals_write_five_rows_and_one_alert_and_the_rest_are_logged_only(
    client, enforced, refusal_alerts, caplog, _environment
):
    assert CONSOLE_REFUSAL_ALERT_COUNT == 5
    with _Console() as console, caplog.at_level(logging.WARNING, logger="docflow.api"):
        headers = _headers(console.auth_user_id, aal="aal1")
        for n in range(1, 21):
            response = client.get("/admin/tiers", headers=headers)
            assert response.status_code == 403
            assert response.json() == {"detail": catalog_detail("AUTH-006")}
            if n == 4:
                assert len(_refusals(console)) == 4 and _alerts_for(console) == 0
            if n == 5:
                assert len(_refusals(console)) == 5 and _alerts_for(console) == 1
        rows = _refusals(console)
        alerts = _alerts_for(console)
    assert len(rows) == 5
    assert alerts == 1
    assert caplog.text.count("console_refused_not_recorded code=AUTH-006") == 15


def test_another_account_is_counted_on_its_own(client, enforced, refusal_alerts, _environment):
    with _Console() as busy, _Console() as other:
        for _ in range(CONSOLE_REFUSAL_ALERT_COUNT + 2):
            client.get("/admin/tiers", headers=_headers(busy.auth_user_id, aal="aal1"))
        client.get("/admin/tiers", headers=_headers(other.auth_user_id, aal="aal1"))
        assert len(_refusals(busy)) == CONSOLE_REFUSAL_ALERT_COUNT
        assert len(_refusals(other)) == 1
        assert _alerts_for(other) == 0


# ── 2. Outcomes ───────────────────────────────────────────────────────────────


def test_a_console_write_that_succeeds_records_its_outcome(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        response = client.post(f"/admin/tenants/{tenant_id}/intake-address/rotate", headers=console.headers())
        assert response.status_code == 200, response.text
        intent, outcome = _intent_and_outcome(console, "intake_address_rotate")
    assert intent["payload"]["outcome_expected"] is True
    assert outcome is not None
    assert str(outcome["target_tenant_id"]) == tenant_id
    assert outcome["payload"] == {"intent_id": str(intent["id"]), "result": "succeeded", "code": None}


def test_a_console_write_that_ends_with_a_catalog_error_records_the_code(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        # Example prompting needs a confirmed golden run (D-141).
        response = client.put(
            f"/admin/tenants/{tenant_id}/example-prompting", headers=console.headers(), json={"enabled": True}
        )
        assert response.status_code == 422, response.text
        _, outcome = _intent_and_outcome(console, "example_prompting_set")
    assert outcome is not None
    assert outcome["payload"]["result"] == "failed"
    assert outcome["payload"]["code"] == response.json()["detail"]["code"]


def test_a_console_write_that_ends_with_a_bare_404_records_the_status(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        response = client.post(
            f"/admin/tenants/{tenant_id}/rules/{uuid4()}/enable", headers=console.headers()
        )
        assert response.status_code == 404
        _, outcome = _intent_and_outcome(console, "learned_rule_enable")
    assert outcome is not None
    assert outcome["payload"]["result"] == "failed"
    assert outcome["payload"]["code"] is None
    assert outcome["payload"]["detail"] == {"status": 404}


def test_a_console_write_that_crashes_leaves_no_outcome_and_reads_as_unknown(client, stripe, monkeypatch):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]

        def crash(*args, **kwargs):
            raise RuntimeError("worker died midway")

        monkeypatch.setattr(admin.intake_admin, "rotate_address", crash)
        crashing = TestClient(app, raise_server_exceptions=False)
        response = crashing.post(
            f"/admin/tenants/{tenant_id}/intake-address/rotate", headers=console.headers()
        )
        assert response.status_code == 500
        intent, outcome = _intent_and_outcome(console, "intake_address_rotate")
        assert outcome is None

        entries = client.get(f"/admin/tenants/{tenant_id}/audit", headers=console.headers()).json()["entries"]
    line = next(e for e in entries if e["id"] == str(intent["id"]))
    assert line["outcome"] is None
    assert line["outcome_state"] == "unknown"


def test_the_audit_tab_attaches_each_outcome_and_hides_outcome_rows(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        client.post(f"/admin/tenants/{tenant_id}/intake-address/rotate", headers=console.headers())
        client.put(
            f"/admin/tenants/{tenant_id}/example-prompting", headers=console.headers(), json={"enabled": True}
        )
        # A write from before D-178: no `outcome_expected`, no outcome.
        old_id = str(uuid4())
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO admin_actions (id, platform_admin_user_id, action, target_tenant_id, "
                    "target_type, target_id, payload, created_at) "
                    "VALUES (:id, :by, 'cancel', :t, 'tenant', :t, '{}'::jsonb, clock_timestamp())"
                ),
                {"id": old_id, "by": str(console.user_id), "t": tenant_id},
            )
        body = client.get(f"/admin/tenants/{tenant_id}/audit?limit=200", headers=console.headers()).json()
    entries = {e["event"]: e for e in body["entries"] if e["source"] == "console"}
    assert "outcome" not in entries
    assert body["total"] == len(body["entries"])  # outcome rows are not counted as lines either
    assert entries["intake_address_rotate"]["outcome_state"] == "succeeded"
    assert entries["intake_address_rotate"]["outcome"]["result"] == "succeeded"
    assert entries["example_prompting_set"]["outcome_state"] == "failed"
    assert entries["example_prompting_set"]["outcome"]["code"]
    assert entries["cancel"]["outcome_state"] == "not_recorded"
    assert entries["tenant_create"]["outcome_state"] == "succeeded"


# ── Actions that write their row in the same transaction as the work ─────────


def test_a_failed_tier_change_leaves_an_intent_and_a_failed_outcome(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        response = client.post(
            f"/admin/tenants/{tenant_id}/tier", headers=console.headers(), json={"tier": "growth"}
        )
        assert response.status_code == 409, response.text  # BIL-001: not live yet
        intent, outcome = _intent_and_outcome(console, "tier_change")
    assert intent["payload"]["outcome_expected"] is True
    assert intent["payload"]["tier"] == "growth"
    assert outcome is not None
    assert outcome["payload"]["code"] == "BIL-001"
    assert "detail" not in outcome["payload"]  # refused before Stripe was called


def test_a_failed_hard_delete_leaves_a_record_with_its_stage(client, stripe):
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        response = client.post(
            f"/admin/tenants/{tenant_id}/delete",
            headers=console.headers(),
            json={"confirm_name": "Acme Test Distributor", "reason": "a reason for the test"},
        )
        assert response.status_code == 409, response.text  # LIFE-006: not in the ready-to-delete queue
        _, outcome = _intent_and_outcome(console, "tenant_delete")
    assert outcome is not None
    assert outcome["payload"]["code"] == "LIFE-006"
    assert outcome["payload"]["detail"] == {"stage": "check"}


def test_a_failed_tenant_creation_names_the_tenant_only_by_target_id(client, stripe):
    stripe.fail = True
    with _Console() as console:
        response = console.create_tenant(client)
        assert response.status_code != 200
        intent, outcome = _intent_and_outcome(console, "tenant_create")
    assert intent["target_tenant_id"] is None and intent["target_id"] is not None
    assert outcome is not None
    assert outcome["target_tenant_id"] is None
    assert outcome["payload"]["result"] == "failed"


def test_a_created_tenants_request_row_names_it_by_target_id_and_its_line_reads_done(client, stripe):
    """The request row is committed before the tenant exists, so it can't name
    it in `target_tenant_id` (a foreign key); the outcome, written with the
    work, can. The tenant's Audit tab still shows the line, as Done."""
    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        intent, outcome = _intent_and_outcome(console, "tenant_create")
        entries = client.get(f"/admin/tenants/{tenant_id}/audit", headers=console.headers()).json()["entries"]
    assert intent["target_tenant_id"] is None and str(intent["target_id"]) == tenant_id
    assert outcome is not None and str(outcome["target_tenant_id"]) == tenant_id
    assert outcome["payload"]["detail"]["files_moved"] == 0
    line = next(e for e in entries if e["id"] == str(intent["id"]))
    assert line["outcome_state"] == "succeeded"


class _MachineStop(BaseException):
    """The process stopping: nothing after it runs, no handler sees it."""


def test_a_stop_between_the_request_row_and_the_work_reads_outcome_unknown(client, stripe, monkeypatch):
    """The founder's change (2026-10-27): the request row commits in its own
    transaction before the work, so a machine that stops in between leaves a
    request with no outcome, and the Audit tab says the outcome is unknown."""
    real_session = admin_data_access.platform_session
    opened = []

    def stop_after_the_request_row():
        opened.append(1)
        if len(opened) == 2:  # the work's transaction: the process stops here
            raise _MachineStop
        return real_session()

    with _Console() as console:
        tenant_id = console.create_tenant(client).json()["tenant_id"]
        monkeypatch.setattr(admin_data_access, "platform_session", stop_after_the_request_row)
        with pytest.raises(_MachineStop):
            admin_data_access.change_tier(
                platform_admin_user_id=console.user_id, tenant_id=UUID(tenant_id), tier_code="growth"
            )
        monkeypatch.setattr(admin_data_access, "platform_session", real_session)
        intent, outcome = _intent_and_outcome(console, "tier_change")
        entries = client.get(f"/admin/tenants/{tenant_id}/audit", headers=console.headers()).json()["entries"]
    assert len(opened) == 2
    assert str(intent["target_tenant_id"]) == tenant_id
    assert intent["payload"] == {"tier": "growth", "outcome_expected": True}
    assert outcome is None
    line = next(e for e in entries if e["id"] == str(intent["id"]))
    assert line["outcome"] is None
    assert line["outcome_state"] == "unknown"
