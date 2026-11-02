"""
What the API does without the signing secret, and how a download is named
(D-228). None of this reaches a database: a token is refused or answered
before any session opens, so these also run with no database at all.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4

import pytest
from docflow_core.config import get_settings

from app.downloads import attachment_headers, safe_filename_part
from app.main import document_signing_startup_check
from app.routers.review import _download_filename


@pytest.fixture
def no_signing_secret(monkeypatch):
    monkeypatch.setenv("DOCUMENT_URL_SIGNING_SECRET", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _well_formed_token() -> str:
    """Gets past the token's shape, so the route has to sign to check it."""
    return f"{uuid4()}.{int(time.time()) + 60}.signature"


@pytest.mark.parametrize(
    "path",
    [
        f"/review/documents/{uuid4()}/original/content",
        f"/review/exports/{uuid4()}/download",
    ],
)
def test_a_route_that_checks_a_link_answers_sys_002_and_logs_it(client, no_signing_secret, path, caplog):
    """A 500 without CORS headers reads in the browser as "We couldn't reach
    DocFlow" (D-133), so the headers are part of what this proves. SYS-002
    says it was logged, so that is too."""
    origin = "http://localhost:3000"

    with caplog.at_level(logging.ERROR, logger="docflow.api"):
        response = client.get(path, params={"token": _well_formed_token()}, headers={"Origin": origin})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "SYS-002"
    assert response.headers["access-control-allow-origin"] == origin
    assert "document_signing_secret_unset" in caplog.text


def test_sys_002_promises_nothing_that_isnt_so():
    """It says what's been done, as Section 7.16.5 asks, and no more: nothing
    clears this by itself and nobody is alerted (founder, 2026-11-02)."""
    from docflow_core.errors import get_error

    entry = get_error("SYS-002")
    assert entry.action == "DocFlow has logged this. If it keeps happening, contact DocFlow support."
    wording = f"{entry.message} {entry.action}".lower()
    for promise in ("alerted", "try again", "a few minutes", "already on it"):
        assert promise not in wording


def test_startup_warns_with_no_signing_secret_and_is_quiet_with_one(no_signing_secret, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="docflow.api"):
        assert document_signing_startup_check() is False
    assert "document_signing_secret_unset" in caplog.text

    caplog.clear()
    monkeypatch.setenv("DOCUMENT_URL_SIGNING_SECRET", "test-only-signing-secret-not-a-real-one")
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING, logger="docflow.api"):
        assert document_signing_startup_check() is True
    assert "document_signing_secret_unset" not in caplog.text


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("po.pdf", "po.pdf"),
        ('../../po "copy"\r\nX-Evil: 1.pdf', "po-copy-X-Evil-1.pdf"),
        ("Bestellung für März.pdf", "Bestellung-f-r-M-rz.pdf"),
        ("", "original-document"),
        (None, "original-document"),
        ("...", "original-document"),
    ],
)
def test_a_download_is_named_from_what_a_stranger_sent_safely(original, expected):
    assert _download_filename(original) == expected


def test_a_long_filename_keeps_its_extension():
    named = _download_filename("x" * 300 + ".pdf")
    assert len(named) == 120
    assert named.endswith(".pdf")


def test_nothing_can_break_out_of_the_header():
    name = safe_filename_part('a"; filename="evil.exe\r\nX: y')
    headers = attachment_headers(name)
    assert headers["Content-Disposition"].count('"') == 2
    assert "\r" not in headers["Content-Disposition"]
    assert "\n" not in headers["Content-Disposition"]
    assert headers["X-Content-Type-Options"] == "nosniff"
