"""
A refused inbound webhook request raises a founder alert (D-171, Stage 2c).

2a made the webhook refuse any request without Postmark's credentials. From
outside, a refusal is either a misconfigured cutover -- in which case no
customer's mail arrives at all -- or someone probing, and until now the only
trace was a log line nobody watches. The founder's blocking condition (RUNBOOK
2.1) is that credential enforcement does not go live on a real customer's
address until this alert exists.

What this proves: a refusal raises one high-severity alert with no tenant and
only the reason word in its payload, and its founder email; repeats collapse
into the open alert; the narrow session behind it (migration 0029) can insert
exactly that alert and email and read nothing; and an alert that cannot be
written never turns the 401 into something else.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import inspect
import re
from uuid import uuid4

import pytest
from docflow_core import email_intake
from docflow_core.config import get_settings
from docflow_core.db import intake_refusal_session, platform_session
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import deps
from tests.conftest import intake_webhook_headers, requires_database

pytestmark = pytest.mark.usefixtures("cleans_up_refusal_alerts")

MISMATCH_KEY = "intake_webhook_refused:mismatch"


@pytest.fixture(autouse=True)
def _founder_email(monkeypatch):
    """So the alert's email is written too, exercising 0029's outbox policy."""
    monkeypatch.setenv("FOUNDER_ALERT_EMAIL", "founder@example.test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _ForceRollback(Exception):
    """Guarantees a session rolls back even if a write that should have been
    refused was not (D-165)."""


def _refuse(client):
    # The token is never read on a refusal, so any value will do.
    return client.post(
        f"/intake/email/{uuid4().hex}",
        json={},
        headers=intake_webhook_headers(password="wrong-for-this-test"),
    )


def _open_alerts(dedupe_key: str) -> list[dict]:
    with platform_session() as session:
        return [
            dict(row)
            for row in session.execute(
                text(
                    "SELECT a.id, a.severity, a.tenant_id, a.payload, a.email_outbox_id, "
                    "       o.tenant_id AS outbox_tenant, o.template AS outbox_template "
                    "FROM founder_alerts a LEFT JOIN email_outbox o ON o.id = a.email_outbox_id "
                    "WHERE a.dedupe_key = :k AND a.acknowledged_at IS NULL"
                ),
                {"k": dedupe_key},
            ).mappings()
        ]


@requires_database
def test_a_refused_request_raises_one_high_severity_alert_with_no_tenant(client):
    already_open = _open_alerts(MISMATCH_KEY)
    assert already_open == [], (
        "An open intake_webhook_refused:mismatch alert already exists, so this test cannot "
        "show that a refusal raises one. Acknowledge it in the Console and re-run. "
        f"Alert ids: {[str(a['id']) for a in already_open]}"
    )
    assert _refuse(client).status_code == 401
    assert _refuse(client).status_code == 401  # a repeat collapses into the open alert

    alerts = _open_alerts(MISMATCH_KEY)
    assert len(alerts) == 1, alerts
    alert = alerts[0]
    assert alert["severity"] == "high"
    assert alert["tenant_id"] is None
    # The reason word only: never the credential, the token or the address (7.10).
    assert alert["payload"] == {"reason": "mismatch"}
    # One alert, two channels (Section 7.9): its email exists and has no tenant.
    assert alert["email_outbox_id"] is not None
    assert alert["outbox_tenant"] is None and alert["outbox_template"] == "founder_alert"


@requires_database
def test_the_refusal_session_can_raise_only_that_alert_with_no_tenant(client):
    insert = text(
        "INSERT INTO founder_alerts (type, severity, tenant_id) VALUES (:type, 'high', :tenant)"
    )
    for alert_type, tenant in (
        ("document_failed", None),  # another type
        ("intake_webhook_refused", str(uuid4())),  # the right type, but about a tenant
    ):
        with pytest.raises(DBAPIError, match="row-level security"):
            with intake_refusal_session() as session:
                session.execute(insert, {"type": alert_type, "tenant": tenant})
                raise _ForceRollback


@requires_database
def test_the_refusal_session_can_queue_only_the_founder_alert_email(client):
    insert = text(
        "INSERT INTO email_outbox (tenant_id, to_address, template, subject, body_text, related_type) "
        "VALUES (NULL, 'someone@example.test', :template, 's', 'b', :related)"
    )
    for template, related in (("tenant_invite", None), ("founder_alert", None)):
        with pytest.raises(DBAPIError, match="row-level security"):
            with intake_refusal_session() as session:
                session.execute(insert, {"template": template, "related": related})
                raise _ForceRollback


@requires_database
def test_the_refusal_session_reads_nothing(client):
    _refuse(client)  # so there is at least one alert and one email to not see
    with intake_refusal_session() as session:
        alerts = session.execute(text("SELECT count(*) FROM founder_alerts")).scalar_one()
        emails = session.execute(text("SELECT count(*) FROM email_outbox")).scalar_one()
        tenants = session.execute(text("SELECT count(*) FROM tenants")).scalar_one()
    assert (alerts, emails, tenants) == (0, 0, 0)


def test_an_alert_that_cannot_be_written_leaves_the_refusal_a_401(client, monkeypatch):
    def broken_session():
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(email_intake, "intake_refusal_session", broken_session)
    assert email_intake.alert_webhook_refused("mismatch") is False
    assert _refuse(client).status_code == 401


def test_a_reason_outside_the_fixed_set_is_not_raised(monkeypatch):
    def must_not_open():
        raise AssertionError("a session was opened for an unknown reason")

    monkeypatch.setattr(email_intake, "intake_refusal_session", must_not_open)
    assert email_intake.alert_webhook_refused("<anything from a request>") is False


def test_every_reason_the_credential_check_can_refuse_with_is_in_the_fixed_set():
    """If deps gains a reason the alert does not know, that refusal would go
    unalerted -- this keeps the two lists together."""
    source = inspect.getsource(deps.check_inbound_webhook_credentials)
    raised = set(re.findall(r'InboundWebhookRefused\("([a-z_]+)"\)', source))
    assert raised == set(email_intake.WEBHOOK_REFUSAL_REASONS)

