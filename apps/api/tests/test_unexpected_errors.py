"""
A failure nobody anticipated (D-136): the API answers SYS-001 in the catalog's
words, with the CORS headers a browser needs to read it, and logs where it
failed without the failure's own text.

Before this, Starlette's last-resort 500 was sent from outside the CORS
middleware; the browser discarded it and every screen said "We couldn't reach
DocFlow" when DocFlow had been reached and had failed (found in D-133).
All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import logging

import pytest
from docflow_core import tenant_home
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import requires_database
from tests.test_console_api import _environment  # noqa: F401
from tests.test_quarantine_api import _Tenant

ORIGIN = "http://localhost:3000"
SECRET = "Acme Test secret value 4471"


@pytest.fixture
def lenient_client() -> TestClient:
    # The test client normally re-raises a server error into the test; this
    # one returns the response, as a browser would receive it.
    return TestClient(app, raise_server_exceptions=False)


@requires_database
def test_a_crash_answers_in_catalog_words_that_a_browser_can_read(lenient_client, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError(f"database said: {SECRET}")

    monkeypatch.setattr(tenant_home, "activity_page", broken)
    with _Tenant("Acme Test Crash") as t, caplog.at_level(logging.ERROR, logger="docflow.api"):
        response = lenient_client.get("/activity", headers={**t.headers(), "Origin": ORIGIN})

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "SYS-001"
    # Without this header the browser throws the answer away.
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    # Nothing internal reaches the person...
    assert SECRET not in response.text and "RuntimeError" not in response.text
    # ...and the log says where it failed, but not the failure's text (7.10).
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "unhandled_error" in logged and "RuntimeError" in logged and "/activity" in logged
    assert SECRET not in logged


def test_an_anticipated_refusal_is_untouched(lenient_client):
    # A signed-out request still gets its own answer, not SYS-001.
    response = lenient_client.get("/team", headers={"Origin": ORIGIN})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH-005"
