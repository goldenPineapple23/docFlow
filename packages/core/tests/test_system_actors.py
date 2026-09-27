"""
Named system actors (D-159, D-165): the code, migration 0028 and the CI role
script agree, and no code writes a lifecycle event without an actor.
"""

import re
from pathlib import Path

from docflow_core import system_actors

REPO = Path(__file__).resolve().parents[3]
MIGRATION = REPO / "supabase" / "migrations" / "0028_system_actors.sql"
CI_ROLE = REPO / "scripts" / "ci" / "create_app_role.py"
CORE = REPO / "packages" / "core" / "docflow_core"


def test_every_system_actor_in_code_is_seeded_by_the_migration_with_the_same_id():
    sql = MIGRATION.read_text(encoding="utf-8")
    seeded = dict(
        (key, uuid)
        for uuid, key in re.findall(
            r"\('([0-9a-f-]{36})', null, null,\s*'[^']+', '[^']+', 'viewer', false, '([a-z-]+)'\)", sql
        )
    )
    assert seeded == {key: str(uuid) for key, uuid in system_actors.ALL.items()}


def test_the_idle_transaction_timeout_is_the_same_in_the_migration_and_in_ci():
    value = re.compile(r"idle_in_transaction_session_timeout = '+(\w+)'+")
    assert value.findall(MIGRATION.read_text(encoding="utf-8")) == ["5min"]
    assert value.findall(CI_ROLE.read_text(encoding="utf-8")) == ["5min"]


def test_no_code_writes_a_lifecycle_event_without_an_actor():
    offenders = [
        f"{path.name}:{number}"
        for path in CORE.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"actor_user_id\s*=\s*None|SET actor_user_id = NULL", line)
    ]
    assert not offenders, f"name the actor (docflow_core.system_actors): {offenders}"
