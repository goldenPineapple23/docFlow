"""
Who can switch on a narrow RLS flag (DECISIONS.md D-158 follow-up).

Several policies open a table when a transaction carries a flag:
`app.is_platform_admin`, `app.rollup`, `app.lifecycle`, `app.scheduler`,
`app.stripe_webhook`, `app.pipeline_sweep`, plus the lookups by intake token
and sign-in id. Postgres lets any connected role set a custom `app.*` setting,
so the database alone does not decide who carries a flag -- this code does:

- Only `docflow_core/db.py` writes SQL that sets an `app.*` flag, each inside
  one named session helper that first clears every flag
  (`_reset_rls_settings`).
- Each narrow helper is used only by the one module it exists for. A tenant
  request gets `tenant_session()` and nothing else.
- No migration defines a database function that sets a flag, so Supabase's
  public API (anon key, or a signed-in user's token) has no way to set one:
  it can't run SET, and set_config is not exposed to it.

The real-database half (a tenant session sees only its own tenant, even on a
connection left carrying the flag) is in
apps/worker/tests/test_pipeline_integrity_db.py.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
APPLICATION_CODE = [
    REPO / "packages" / "core" / "docflow_core",
    REPO / "apps" / "api" / "app",
    REPO / "apps" / "worker" / "app",
    REPO / "scripts",
]
DB_MODULE = REPO / "packages" / "core" / "docflow_core" / "db.py"
MIGRATIONS = REPO / "supabase" / "migrations"

SETS_A_FLAG = re.compile(r"set_config\s*\(\s*'app\.|\bset\s+(local\s+)?app\.", re.IGNORECASE)

# Each narrow session -> the only files that may use it. `platform_session`
# (the Section 7.15.1 admin path) is also used by founder-run scripts under
# scripts/, which no request can reach.
ALLOWED_USERS = {
    "pipeline_sweep_session": {"packages/core/docflow_core/stuck_documents.py"},
    "rollup_session": {"packages/core/docflow_core/metrics.py"},
    "lifecycle_session": {
        "packages/core/docflow_core/lifecycle.py",
        "apps/worker/app/tasks/lifecycle_sweep.py",
    },
    "scheduler_session": {"packages/core/docflow_core/scheduled_jobs.py"},
    "stripe_webhook_session": {"packages/core/docflow_core/billing_webhooks.py"},
    "token_lookup_session": {"packages/core/docflow_core/email_intake.py"},
    "identity_lookup_session": {"apps/api/app/deps.py"},
    "platform_session": {"packages/core/docflow_core/admin_data_access.py"},
}


def _application_files():
    for root in APPLICATION_CODE:
        for path in root.rglob("*.py"):
            if path != DB_MODULE:
                yield path


def test_only_db_py_sets_an_rls_flag():
    offenders = [
        f"{path.relative_to(REPO)}:{number}"
        for path in _application_files()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if SETS_A_FLAG.search(line)
    ]
    assert offenders == [], f"Only docflow_core/db.py may set an app.* RLS flag: {offenders}"


def test_every_narrow_session_is_used_only_where_it_belongs():
    offenders = []
    for path in _application_files():
        relative = path.relative_to(REPO).as_posix()
        source = path.read_text(encoding="utf-8")
        for helper, allowed in ALLOWED_USERS.items():
            if not re.search(rf"\b{helper}\b", source):
                continue
            if relative in allowed:
                continue
            if helper == "platform_session" and relative.startswith("scripts/"):
                continue
            offenders.append(f"{relative} uses {helper}")
    assert offenders == [], offenders


def test_the_stuck_order_sweep_is_reachable_only_from_the_worker():
    importers = sorted(
        path.relative_to(REPO).as_posix()
        for path in _application_files()
        if re.search(r"\bstuck_documents\b", path.read_text(encoding="utf-8"))
        and path.name != "stuck_documents.py"
    )
    assert importers == ["apps/worker/app/tasks/stuck_sweep.py"]


def test_no_migration_gives_the_database_a_way_to_set_a_flag():
    offenders = [
        f"{path.name}:{number}"
        for path in sorted(MIGRATIONS.glob("*.sql"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if SETS_A_FLAG.search(line) and not line.lstrip().startswith("--")
    ]
    assert offenders == [], f"A database function that sets an app.* flag would be callable: {offenders}"
