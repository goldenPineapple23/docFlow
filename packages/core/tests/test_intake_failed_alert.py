"""
`founder_alerts.alert_intake_failed` without a database (Phase 5.5 Stage 5,
blocker 4; DECISIONS.md D-210): what the alert carries and its key, and
that it never raises. The API's database tests prove the real write.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

import pytest

from docflow_core import db, faults, founder_alerts
from docflow_core.errors import CATALOG

SECRET = "Acme Test secret value 5512"


def _fault_of_ours() -> RuntimeError:
    try:
        raise RuntimeError(f"our bug: {SECRET}")
    except RuntimeError as exc:
        return exc


@pytest.fixture
def written(monkeypatch) -> list[dict]:
    """Every `raise_alert` call, in a fake tenant session."""
    calls: list[dict] = []

    @contextmanager
    def fake_session(tenant_id):
        yield object()

    def fake_raise(session, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(db, "tenant_session", fake_session)
    monkeypatch.setattr(founder_alerts, "raise_alert", fake_raise)
    monkeypatch.delenv("FLY_IMAGE_REF", raising=False)
    return calls


def test_the_alert_is_the_fault_only_once_per_state(written):
    tenant_id = uuid4()
    exc = _fault_of_ours()

    assert founder_alerts.alert_intake_failed(tenant_id, exc) is True

    (call,) = written
    fault = faults.describe(exc)
    assert call["alert_type"] == "intake_failed" and call["severity"] == "high"
    assert call["tenant_id"] == tenant_id
    assert call["payload"] == {
        "step": "email_intake_webhook",
        "exception_type": "RuntimeError",
        "location": fault.location,
        "release": faults.UNVERSIONED,
        "error_code": "INT-011",
    }
    assert call["state_key"] == (
        f"fault:intake_failed:{tenant_id}:email_intake_webhook:RuntimeError:"
        f"{fault.module}:{fault.function}:{fault.line}:{faults.UNVERSIONED}"
    )
    assert SECRET not in str(call)


def test_it_never_raises_when_the_alert_cant_be_written(monkeypatch, caplog):
    @contextmanager
    def broken_session(tenant_id):
        raise RuntimeError("no database")
        yield  # pragma: no cover

    monkeypatch.setattr(db, "tenant_session", broken_session)
    with caplog.at_level("ERROR"):
        assert founder_alerts.alert_intake_failed(uuid4(), _fault_of_ours()) is False
    assert "founder_alert_not_raised" in caplog.text and "alert=intake_failed" in caplog.text
    assert SECRET not in caplog.text


def test_the_type_and_its_catalog_entry_are_registered():
    assert "intake_failed" in founder_alerts.ALERT_TYPES
    entry = CATALOG[founder_alerts.INTAKE_FAILED_CODE]
    assert entry.audience == "founder" and entry.action.strip()
