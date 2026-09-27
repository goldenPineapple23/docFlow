"""
Named system actors and the idle-transaction cap, against the real database
(migration 0028; D-159, D-163, D-165).

  * the three system actors exist with the ids the code uses, and are inert:
    no tenant, no sign-in id, inactive, least role -- they can never sign in;
  * the app role (docflow_app, what every test and every request connects as)
    can't create, edit or delete one, nor turn a person into one;
  * no lifecycle event has a blank actor, and a new one without an actor is
    refused;
  * the app role's idle transactions are capped at 5 minutes.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from docflow_core import system_actors
from docflow_core.db import platform_session
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import requires_database

pytestmark = [requires_database]

INSUFFICIENT_PRIVILEGE = "42501"
NOT_NULL_VIOLATION = "23502"


def _sqlstate(excinfo) -> str | None:
    return getattr(excinfo.value.orig, "sqlstate", None)


def test_the_system_actors_exist_with_the_codes_ids_and_can_never_sign_in():
    with platform_session() as session:
        rows = session.execute(
            text(
                "SELECT id, system_actor, tenant_id, auth_user_id, is_active, role "
                "FROM users WHERE system_actor IS NOT NULL ORDER BY system_actor"
            )
        ).mappings().all()
    assert {r["system_actor"]: r["id"] for r in rows} == system_actors.ALL
    for row in rows:
        assert (row["tenant_id"], row["auth_user_id"], row["is_active"], row["role"]) == (
            None, None, False, "viewer"
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE users SET full_name = 'renamed' WHERE id = :id",
        "UPDATE users SET is_active = true WHERE id = :id",
        "DELETE FROM users WHERE id = :id",
    ],
)
def test_the_app_role_cannot_edit_or_delete_a_system_actor(statement):
    with pytest.raises(DBAPIError) as excinfo:
        with platform_session() as session:
            session.execute(text(statement), {"id": str(system_actors.LIFECYCLE_SWEEP)})
    assert _sqlstate(excinfo) == INSUFFICIENT_PRIVILEGE


def test_the_app_role_cannot_create_a_system_actor():
    with pytest.raises(DBAPIError) as excinfo:
        with platform_session() as session:
            session.execute(
                text(
                    "INSERT INTO users (id, tenant_id, email, role, is_active, system_actor) "
                    "VALUES (:id, NULL, 'fake@system.docflow.invalid', 'viewer', false, 'fake-actor')"
                ),
                {"id": str(uuid4())},
            )
    assert _sqlstate(excinfo) == INSUFFICIENT_PRIVILEGE


def test_the_app_role_cannot_turn_a_person_into_a_system_actor():
    person = uuid4()
    with platform_session() as session:
        session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, role, is_active) "
                "VALUES (:id, NULL, 'acme-test-person@example.test', 'viewer', false)"
            ),
            {"id": str(person)},
        )
    try:
        with pytest.raises(DBAPIError) as excinfo:
            with platform_session() as session:
                session.execute(
                    text("UPDATE users SET system_actor = 'lifecycle-sweep-2' WHERE id = :id"),
                    {"id": str(person)},
                )
        assert _sqlstate(excinfo) == INSUFFICIENT_PRIVILEGE
    finally:
        with platform_session() as session:
            session.execute(text("DELETE FROM users WHERE id = :id"), {"id": str(person)})


def test_no_lifecycle_event_has_a_blank_actor():
    with platform_session() as session:
        blank = session.execute(
            text("SELECT count(*) FROM tenant_lifecycle_events WHERE actor_user_id IS NULL")
        ).scalar_one()
    assert blank == 0


def test_a_lifecycle_event_without_an_actor_is_refused():
    with pytest.raises(DBAPIError) as excinfo:
        with platform_session() as session:
            tenant_id = session.execute(text("SELECT id FROM tenants LIMIT 1")).scalar_one()
            session.execute(
                text(
                    "INSERT INTO tenant_lifecycle_events (tenant_id, event_type, actor_user_id, payload) "
                    "VALUES (:t, 'acme_test_blank_actor', NULL, '{}'::jsonb)"
                ),
                {"t": str(tenant_id)},
            )
    assert _sqlstate(excinfo) == NOT_NULL_VIOLATION


def test_the_app_roles_idle_transactions_are_capped_at_five_minutes():
    """Set on the role, so every environment gets it from the migration (or,
    in CI, from create_app_role.py) and every new connection starts with it."""
    with platform_session() as session:
        settings = session.execute(
            text(
                "SELECT s.setconfig FROM pg_db_role_setting s "
                "JOIN pg_roles r ON r.oid = s.setrole WHERE r.rolname = current_user AND s.setdatabase = 0"
            )
        ).scalar()
    assert "idle_in_transaction_session_timeout=5min" in (settings or [])
