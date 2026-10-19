# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
An alert raised once per state, and the documents a state has hit, against
the real database and RLS (Phase 5.5 Stage 4, PR 1a; DECISIONS.md D-203;
migration 0037).

What this proves:
  * the defect D-198 named does not exist under a state key: an alert the
    founder acknowledged is NOT raised again by the same state;
  * a tenant session still inserts an alert and never reads one -- the
    database refuses the duplicate, nothing in the tenant's session looks;
  * the Console counts the documents that carry the alert's key, before and
    after the acknowledgement, and lists an acknowledged alert whose state
    is still hitting documents under "Acknowledged, still happening" until
    ALERT_STILL_HAPPENING_DAYS pass with no new one;
  * all of that is one Console read with one admin action.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from docflow_core import faults, founder_alerts
from docflow_core.constants import ALERT_STILL_HAPPENING_DAYS
from docflow_core.db import platform_session, tenant_session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.conftest import requires_console_schema
from tests.test_console_api import _Console, _environment, stripe  # noqa: F401

FAULT = faults.Fault("TypeError", "docflow_core.matching", "resolve_line", 412)


def _key(tenant_id: UUID, *, step: str = "matching", fault: faults.Fault = FAULT) -> str:
    with tenant_session(tenant_id) as session:
        return faults.state_key(
            session, alert_type="pipeline_step_failed", tenant_id=tenant_id, step=step, fault=fault
        )


def _raise(tenant_id: UUID, key: str, *, step: str = "matching", document_id: UUID | None = None) -> bool:
    """`step` must be the step the key was made for (`_key`'s), as the
    worker raises them."""
    with tenant_session(tenant_id) as session:
        return founder_alerts.raise_fault_alert(
            session,
            alert_type="pipeline_step_failed",
            severity="high",
            tenant_id=tenant_id,
            document_id=document_id or uuid4(),
            step=step,
            fault=FAULT,
            state_key=key,
        )


def _document(tenant_id: UUID) -> UUID:
    """A document row of the tenant's. No file: nothing here reads one."""
    document_id = uuid4()
    with platform_session() as session:
        session.execute(
            text(
                "INSERT INTO documents (id, tenant_id, original_filename, storage_path, source, "
                "status, content_sha256, created_at) "
                "VALUES (:id, :tid, 'po.txt', :path, 'upload', 'pending', :sha, now())"
            ),
            {
                "id": str(document_id),
                "tid": str(tenant_id),
                "path": f"tenants/{tenant_id}/uploads/{document_id}.txt",
                "sha": uuid4().hex + uuid4().hex,
            },
        )
    return document_id


def _hit(tenant_id: UUID, key: str, *, at: str = "now()") -> UUID:
    """A document the state hit: the key written by the real writer, in the
    tenant's own session, and the time the document reached its outcome."""
    document_id = _document(tenant_id)
    with tenant_session(tenant_id) as session:
        faults.note_on_document(session, document_id, key)
    with platform_session() as session:
        session.execute(
            text(f"UPDATE documents SET processed_at = {at} WHERE id = :id"), {"id": str(document_id)}
        )
    return document_id


def _scalar(sql: str, **params):
    with platform_session() as session:
        return session.execute(text(sql), params).scalar_one()


def _console_lists(client, console) -> dict:
    response = client.get("/admin/alerts", headers=console.headers())
    assert response.status_code == 200, response.text
    return response.json()


def _mine(alerts: list[dict], tenant_id: UUID) -> list[dict]:
    return [a for a in alerts if a["tenant_id"] == str(tenant_id)]


# ── once per state, acknowledged or not ──────────────────────────────────────


@requires_console_schema
def test_an_acknowledged_alert_is_never_raised_again_for_the_same_state(client, stripe):
    with _Console() as console:
        tenant_id = UUID(console.create_tenant(client).json()["tenant_id"])
        key = _key(tenant_id)

        assert _raise(tenant_id, key) is True
        assert _raise(tenant_id, key) is False, "a repeat while the first is open"

        (alert,) = _mine(_console_lists(client, console)["alerts"], tenant_id)
        ack = client.post(f"/admin/alerts/{alert['id']}/acknowledge", headers=console.headers())
        assert ack.json()["acknowledged"] is True

        # The defect (D-198): under the open-row rule this raised a new alert.
        assert _raise(tenant_id, key) is False, "an acknowledged alert was raised again"
        assert _scalar("SELECT count(*) FROM founder_alerts WHERE state_key = :k", k=key) == 1
        assert _mine(_console_lists(client, console)["alerts"], tenant_id) == []
        # And no second e-mail was queued for it.
        assert (
            _scalar(
                "SELECT count(*) FROM email_outbox WHERE tenant_id = :t AND template = 'founder_alert'",
                t=str(tenant_id),
            )
            == 1
        )


@requires_console_schema
def test_a_changed_state_is_raised(client, stripe):
    with _Console() as console:
        tenant_id = UUID(console.create_tenant(client).json()["tenant_id"])
        assert _raise(tenant_id, _key(tenant_id)) is True
        other_place = faults.Fault("TypeError", "docflow_core.matching", "resolve_line", 413)
        assert _raise(tenant_id, _key(tenant_id, fault=other_place)) is True
        other_step = _key(tenant_id, step="duplicate_detection")
        assert _raise(tenant_id, other_step, step="duplicate_detection") is True
        assert len(_mine(_console_lists(client, console)["alerts"], tenant_id)) == 3


@requires_console_schema
def test_the_tenant_session_that_raises_still_cannot_read_an_alert(client, stripe):
    """`tenant_raise` stays insert-only (0011): the duplicate is refused by
    the database's index, not found by a read."""
    with _Console() as console:
        tenant_id = UUID(console.create_tenant(client).json()["tenant_id"])
        key = _key(tenant_id)
        assert _raise(tenant_id, key) is True
        with tenant_session(tenant_id) as session:
            visible = session.execute(text("SELECT count(*) FROM founder_alerts")).scalar_one()
            again = founder_alerts.raise_fault_alert(
                session,
                alert_type="pipeline_step_failed",
                severity="high",
                tenant_id=tenant_id,
                document_id=uuid4(),
                step="matching",
                fault=FAULT,
                state_key=key,
            )
        assert visible == 0 and again is False


@requires_console_schema
def test_an_integrity_failure_that_is_not_a_duplicate_still_surfaces(client, stripe):
    """Only the two dedupe indexes mean "already raised". An alert for a
    tenant that doesn't exist is a bug, and must not read as a duplicate.

    Rolled back whatever happens (the refusal rule, D-211): were the database
    ever to accept the row, it would stay behind under a tenant that doesn't
    exist, where no cleanup looks."""

    class _RollBack(Exception):
        pass

    with pytest.raises(_RollBack):
        with platform_session() as session:
            with pytest.raises(IntegrityError):
                founder_alerts.raise_alert(
                    session,
                    alert_type="pipeline_step_failed",
                    severity="high",
                    tenant_id=uuid4(),
                    payload={},
                    state_key=f"fault:test:{uuid4()}",
                )
            # platform_session rolls back on this; a failed check above is
            # never committed either (the session closes without a commit).
            raise _RollBack


def test_a_state_key_and_a_dedupe_key_are_never_given_together():
    for extra in ({"dedupe_key": "x"}, {"dedupe_per_utc_day": True}, {"dedupe_per_utc_hour": True}):
        with pytest.raises(ValueError, match="state key"):
            founder_alerts.raise_alert(
                None,  # refused before the session is used
                alert_type="pipeline_step_failed",
                severity="high",
                tenant_id=uuid4(),
                state_key="fault:x",
                **extra,
            )


# ── the documents a state has hit ────────────────────────────────────────────


@requires_console_schema
def test_the_console_counts_the_documents_that_carry_the_alerts_key(client, stripe):
    with _Console() as console:
        tenant_id = UUID(console.create_tenant(client).json()["tenant_id"])
        key, other = _key(tenant_id), _key(tenant_id, step="duplicate_detection")
        assert _raise(tenant_id, key) is True
        assert _raise(tenant_id, other, step="duplicate_detection") is True
        _hit(tenant_id, key)
        _hit(tenant_id, key)
        _hit(tenant_id, other)
        _document(tenant_id)  # a document nothing hit

        lists = _console_lists(client, console)
        mine = _mine(lists["alerts"], tenant_id)
        assert len(mine) == 2, f"expected the tenant's two alerts, got {len(mine)}"
        # Each alert found by its own state key, never by a payload field two
        # alerts could share.
        by_id = {a["id"]: a for a in mine}
        matching = by_id[str(_scalar("SELECT id FROM founder_alerts WHERE state_key = :k", k=key))]
        duplicates = by_id[str(_scalar("SELECT id FROM founder_alerts WHERE state_key = :k", k=other))]
        # Each names its own step: the Console says which step failed.
        assert matching["payload"]["step"] == "matching"
        assert duplicates["payload"]["step"] == "duplicate_detection"
        assert matching["affected_documents"]["total"] == 2
        assert matching["affected_documents"]["since_acknowledged"] == 0
        assert matching["affected_documents"]["latest_at"] is not None
        assert duplicates["affected_documents"]["total"] == 1
        assert duplicates["affected_documents"]["since_acknowledged"] == 0
        assert _mine(lists["still_happening"], tenant_id) == []


@requires_console_schema
def test_a_key_is_written_on_a_document_once(client, stripe):
    with _Console() as console:
        tenant_id = UUID(console.create_tenant(client).json()["tenant_id"])
        key = _key(tenant_id)
        document_id = _document(tenant_id)
        for _ in range(2):
            with tenant_session(tenant_id) as session:
                faults.note_on_document(session, document_id, key)
        with tenant_session(tenant_id) as session:
            faults.note_on_document(session, document_id, key + ":another")
        stored = _scalar("SELECT fault_keys FROM documents WHERE id = :id", id=str(document_id))
        assert stored == [key, key + ":another"]


@requires_console_schema
def test_an_alert_with_no_state_key_shows_no_count(client, stripe):
    with _Console() as console:
        tenant_id = UUID(console.create_tenant(client).json()["tenant_id"])
        with tenant_session(tenant_id) as session:
            founder_alerts.raise_alert(
                session,
                alert_type="export_integrity_failure",
                severity="high",
                tenant_id=tenant_id,
                payload={"export_id": "e1"},
                dedupe_key=f"export_integrity_failure:{uuid4()}",
            )
        (alert,) = _mine(_console_lists(client, console)["alerts"], tenant_id)
        assert alert["affected_documents"] is None


@requires_console_schema
def test_an_acknowledged_alert_whose_state_keeps_hitting_documents_is_listed_as_still_happening(
    client, stripe
):
    with _Console() as console:
        tenant_id = UUID(console.create_tenant(client).json()["tenant_id"])
        key = _key(tenant_id)
        assert _raise(tenant_id, key) is True
        _hit(tenant_id, key)

        (alert,) = _mine(_console_lists(client, console)["alerts"], tenant_id)
        client.post(f"/admin/alerts/{alert['id']}/acknowledge", headers=console.headers())
        # Acknowledged, and nothing new since: it is quiet everywhere.
        lists = _console_lists(client, console)
        assert _mine(lists["alerts"], tenant_id) == [] and _mine(lists["still_happening"], tenant_id) == []

        # The same state hits another document. No alert is raised for it...
        assert _raise(tenant_id, key) is False
        _hit(tenant_id, key)
        lists = _console_lists(client, console)
        assert _mine(lists["alerts"], tenant_id) == []
        # ...and the Console shows that it has not stopped.
        (still,) = _mine(lists["still_happening"], tenant_id)
        assert still["id"] == alert["id"] and still["acknowledged_at"] is not None
        assert still["affected_documents"]["total"] == 2
        assert still["affected_documents"]["since_acknowledged"] == 1


@requires_console_schema
def test_it_leaves_the_list_when_the_window_passes_with_no_new_document(client, stripe):
    """ALERT_STILL_HAPPENING_DAYS, by the database's clock: acknowledged ten
    days ago, last hit eight days ago -- after the acknowledgement, but
    outside the window."""
    assert ALERT_STILL_HAPPENING_DAYS == 7
    with _Console() as console:
        tenant_id = UUID(console.create_tenant(client).json()["tenant_id"])
        key = _key(tenant_id)
        assert _raise(tenant_id, key) is True
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE founder_alerts SET acknowledged_at = now() - interval '10 days' "
                    "WHERE state_key = :k"
                ),
                {"k": key},
            )
        _hit(tenant_id, key, at="now() - interval '8 days'")
        assert _mine(_console_lists(client, console)["still_happening"], tenant_id) == []

        # One more, six days ago: inside the window, so it is listed again.
        _hit(tenant_id, key, at="now() - interval '6 days'")
        (still,) = _mine(_console_lists(client, console)["still_happening"], tenant_id)
        assert still["affected_documents"]["since_acknowledged"] == 2


@requires_console_schema
def test_both_lists_are_one_read_with_one_admin_action(client, stripe):
    with _Console() as console:
        before = _scalar(
            "SELECT count(*) FROM admin_actions WHERE platform_admin_user_id = :u AND target_type = 'alerts'",
            u=str(console.user_id),
        )
        lists = _console_lists(client, console)
        assert set(lists) == {"alerts", "still_happening"}
        after = _scalar(
            "SELECT count(*) FROM admin_actions WHERE platform_admin_user_id = :u AND target_type = 'alerts'",
            u=str(console.user_id),
        )
        assert after == before + 1
