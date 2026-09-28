"""
The inbound-mail webhook authenticates the *provider*, not the address
(review finding H8; CLAUDE.md Section 7.2 / 7.16.3 / 7.10).

**The hole this closes.** The per-tenant token in the intake URL was the whole
authentication, and that token is the local part of the address a customer
gives to its buyers -- so it is public the moment the product is used as
intended. Anyone holding an address could POST a Postmark-shaped body with any
`From` and `Authentication-Results: spf=pass dkim=pass dmarc=pass`, and walk
past the DMARC quarantine, the unknown-sender velocity rule and sender-based
example selection in one request.

The token still says which tenant. It no longer says who is asking.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import base64
import logging
from uuid import uuid4

import pytest
from docflow_core.config import get_settings
from docflow_core.db import platform_session
from sqlalchemy import text

from tests.conftest import (
    INTAKE_WEBHOOK_PASSWORD,
    INTAKE_WEBHOOK_USERNAME,
    intake_webhook_headers,
    requires_database,
    requires_email_intake_schema,
)
from tests.test_email_intake import _pm_attachment, _pm_payload, _TestIntakeTenant

# Refused requests raise real founder alerts now (D-171); remove the ones made here.
pytestmark = pytest.mark.usefixtures("cleans_up_refusal_alerts")

# A body claiming every authentication check passed. Before H8 was fixed this
# was enough, by itself, to be treated as a verified buyer.
FORGED_AUTH_HEADERS = [
    {"Name": "Authentication-Results", "Value": "spf=pass dkim=pass dmarc=pass"},
    {"Name": "Received-SPF", "Value": "pass"},
]


def _forged_payload(sender: str = "buyer@not-really-this-domain.test") -> dict:
    return _pm_payload(
        sender,
        attachments=[_pm_attachment("po-forged.txt", b"PO number FORGED-1")],
        headers=FORGED_AUTH_HEADERS,
    )


def _post(client, token, headers=None, payload=None):
    return client.post(
        f"/intake/email/{token}",
        json=payload if payload is not None else _forged_payload(),
        **({"headers": headers} if headers is not None else {}),
    )


def _documents_for(tenant_id) -> int:
    with platform_session() as session:
        return session.execute(
            text("SELECT count(*) FROM documents WHERE tenant_id = :t"), {"t": str(tenant_id)}
        ).scalar_one()


# -- The address is no longer a credential ----------------------------------


@requires_database
@requires_email_intake_schema
def test_the_intake_address_alone_no_longer_admits_anything(client):
    """H8, stated as its own test: knowing the address is not enough."""
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        response = _post(client, tenant.token)  # no Authorization header at all
        assert response.status_code == 401
        # And nothing was ingested, so it cost nothing and entered no queue.
        assert _documents_for(tenant.tenant_id) == 0


@requires_database
@requires_email_intake_schema
def test_a_forged_authentication_results_header_never_gets_that_far(client):
    """
    The forged-headers attack from H8. The point of this test is *where* it is
    refused: before the payload is parsed, so the claimed SPF/DKIM/DMARC passes
    are never read and the unknown-sender rules are never consulted.
    """
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        assert _post(client, tenant.token).status_code == 401
        with platform_session() as session:
            # No intake_rejections row either: this was refused as a request,
            # not judged as mail.
            rejections = session.execute(
                text("SELECT count(*) FROM intake_rejections WHERE tenant_id = :t"),
                {"t": str(tenant.tenant_id)},
            ).scalar_one()
        assert rejections == 0


@requires_database
@requires_email_intake_schema
def test_the_right_credentials_are_processed_as_before(client):
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        response = _post(client, tenant.token, headers=intake_webhook_headers())
        assert response.status_code == 200, response.text
        assert "outcome" in response.json()


# -- Wrong credentials, in each way they can be wrong -----------------------


@requires_database
@requires_email_intake_schema
def test_a_wrong_password_is_refused(client):
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        headers = intake_webhook_headers(password="not-the-password")
        assert _post(client, tenant.token, headers=headers).status_code == 401


@requires_database
@requires_email_intake_schema
def test_a_wrong_username_with_the_right_password_is_refused(client):
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        headers = intake_webhook_headers(username="someone-else")
        assert _post(client, tenant.token, headers=headers).status_code == 401


@requires_database
@requires_email_intake_schema
def test_a_malformed_credential_is_refused_without_crashing(client):
    """Each of these reaches a different branch; none may 500."""
    malformed = [
        {"Authorization": "Bearer something"},
        {"Authorization": "Basic"},
        {"Authorization": "Basic !!!not-base64!!!"},
        {"Authorization": "Basic dXNlcm5hbWUtd2l0aC1uby1jb2xvbg=="},  # decodes, no ':'
        {"Authorization": ""},
        # Non-UTF-8 bytes, and a non-ASCII username. Both must be a 401, not a
        # 500: `hmac.compare_digest` raises TypeError on a str holding
        # non-ASCII characters, so comparing decoded *text* would let the
        # refusal path be crashed by the very thing it refuses. The comparison
        # is done on bytes for exactly this reason.
        {"Authorization": "Basic " + base64.b64encode(b"\xff\xfe:pass").decode()},
        {"Authorization": "Basic " + base64.b64encode("üser:pass".encode()).decode()},
    ]
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        for headers in malformed:
            response = _post(client, tenant.token, headers=headers)
            assert response.status_code == 401, (headers, response.status_code)


@requires_database
@requires_email_intake_schema
def test_an_unresolvable_token_is_refused_at_the_credential_first(client):
    """
    Order matters: the credential is checked before the token is looked up, so
    an unauthenticated request cannot be used to probe which tokens exist.
    """
    assert _post(client, uuid4().hex).status_code == 401


# -- Unconfigured credentials refuse everything, deliberately ---------------


@requires_database
@requires_email_intake_schema
def test_blank_configuration_refuses_everything(client, monkeypatch):
    """
    A missing environment variable must not silently reopen H8. Inbound mail
    stopping is loud and recoverable; token-only authentication is neither.
    """
    monkeypatch.setenv("POSTMARK_WEBHOOK_USERNAME", "")
    monkeypatch.setenv("POSTMARK_WEBHOOK_PASSWORD", "")
    get_settings.cache_clear()
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        # Even credentials that would otherwise be correct.
        assert _post(client, tenant.token, headers=intake_webhook_headers()).status_code == 401


@requires_database
@requires_email_intake_schema
def test_half_a_configuration_is_no_configuration(client, monkeypatch):
    monkeypatch.setenv("POSTMARK_WEBHOOK_PASSWORD", "")
    get_settings.cache_clear()
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        assert _post(client, tenant.token, headers=intake_webhook_headers()).status_code == 401


# -- The credential never appears anywhere it could leak (7.10) -------------


@requires_database
@requires_email_intake_schema
def test_the_credentials_never_reach_a_log_line_or_a_response(client, caplog):
    """
    Not the supplied value, not the configured one, on the refusal path or the
    accepted path. A credential in a log is a credential in a screenshot and in
    a support thread.

    **Sentry is not asserted here because Sentry is not wired up yet** -- there
    is a `sentry_dsn` setting and no SDK, no `init`, no `before_send` anywhere
    in the codebase. When Sentry is initialised in Phase 6 the same assertion is
    owed against its `before_send` scrubber; a test here that mentioned Sentry
    today would only look like it covered it.
    """
    secrets = (INTAKE_WEBHOOK_PASSWORD, INTAKE_WEBHOOK_USERNAME, "not-the-password")
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        with caplog.at_level(logging.DEBUG):
            refused = _post(
                client, tenant.token, headers=intake_webhook_headers(password="not-the-password")
            )
            accepted = _post(client, tenant.token, headers=intake_webhook_headers())

        assert refused.status_code == 401 and accepted.status_code == 200
        logged = "\n".join(r.getMessage() for r in caplog.records)
        for secret in secrets:
            assert secret not in logged, f"{secret!r} reached a log line"
            assert secret not in refused.text, f"{secret!r} reached the refusal body"
            assert secret not in accepted.text, f"{secret!r} reached the response body"
        # The refusal still says enough to diagnose a bad cutover (RUNBOOK 1.6).
        assert "intake_webhook_refused" in logged and "reason=mismatch" in logged


@requires_database
@requires_email_intake_schema
def test_a_refusal_says_which_kind_of_refusal_it_was(client, caplog):
    """
    Each refusal reason is distinguishable in the log, because "inbound mail is
    being refused" has two very different causes -- a misconfigured credential
    and someone probing -- and the first means no mail arrives at all
    (RUNBOOK 1.6).

    Since Stage 2c a refusal also raises a founder alert naming the reason
    (D-171, `test_intake_refusal_alert.py`); the log line stays, because it is
    what a log search for a customer's report finds.
    """
    cases = {
        None: "no_credentials",
        "Bearer x": "not_basic",
        "": "no_credentials",
    }
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        with caplog.at_level(logging.WARNING):
            for header, expected in cases.items():
                caplog.clear()
                response = _post(
                    client,
                    tenant.token,
                    headers=None if header is None else {"Authorization": header},
                )
                assert response.status_code == 401
                logged = "\n".join(r.getMessage() for r in caplog.records)
                assert f"reason={expected}" in logged, (header, logged)

            caplog.clear()
            wrong = _post(client, tenant.token, headers=intake_webhook_headers(password="wrong"))
            assert wrong.status_code == 401
            logged = "\n".join(r.getMessage() for r in caplog.records)
            assert "reason=mismatch" in logged
            # Still no credential in it, on any of these paths (7.10).
            assert INTAKE_WEBHOOK_PASSWORD not in logged and "wrong" not in logged


# -- The IP allowlist is corroboration, never a refusal (D-155) -------------


@requires_database
@requires_email_intake_schema
def test_an_address_off_the_allowlist_is_logged_and_still_processed(client, monkeypatch, caplog):
    """
    An allowlist enforced before the real addresses are confirmed would drop
    customers' purchase orders. Until the RUNBOOK section 2 procedure confirms
    them, it only ever writes a log line.
    """
    monkeypatch.setenv("POSTMARK_INBOUND_IP_ALLOWLIST", "203.0.113.7,203.0.113.8")
    get_settings.cache_clear()
    with _TestIntakeTenant("Acme Test Distributor") as tenant:
        with caplog.at_level(logging.WARNING):
            response = _post(client, tenant.token, headers=intake_webhook_headers())
        assert response.status_code == 200, response.text
        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert "source_not_on_allowlist" in logged
