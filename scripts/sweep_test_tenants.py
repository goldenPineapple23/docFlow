"""
List the old test tenants on docflow-staging, or remove one (D-224; founder,
2026-10-27). Staging holds "Acme Test ..." tenants that earlier test runs
left behind; this finds them and, one at a time and only under the founder's
Grant naming the tenant, removes one.

    python scripts/sweep_test_tenants.py list
    python scripts/sweep_test_tenants.py delete <tenant id> --name "<exact name>" [--backup-folder <dir>]

Run it from the repo root with `apps/api/.venv`'s Python. It refuses to start
unless the process environment (never `.env`) carries DOCFLOW_STAGING_RUN=1
and DOCFLOW_ENV=staging, and both the admin database URL and the Storage
endpoint belong to staging's project. No flag or setting turns that off.

`list` is read-only: every "Acme Test" tenant older than a day, what it holds
(rows by table, Storage objects, the users' e-mail addresses) and its marks.
A marked tenant -- live, deleted or pending deletion by the product, named in
the records, a seed script's name -- is always refused.

`delete` removes one unmarked tenant whose name is typed exactly. In one
transaction on one connection it takes the suites' run lock (so it can't
overlap an API or worker suite, and a suite starting meanwhile refuses,
naming this sweep), backs up every row and Storage object to a folder outside
the repository, checks the backup, deletes the rows, confirms nothing points
at the tenant, removes its files, and commits. The checks and the backup are
`purge_swept_tenant` in apps/api/tests/tenant_cleanup.py; this script holds
the guard and the lock. The Supabase Auth accounts of the tenant's users are
left as they are; their addresses are printed and kept in the backup.

Nothing from apps/api is imported until the guard has passed.
"""

from __future__ import annotations

import argparse
import importlib
import os
import socket
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

STAGING_PROJECT_REF = "ovntvsmhekqefsrpjtih"
STAGING_RUN_FLAG = "DOCFLOW_STAGING_RUN"
ENVIRONMENT_FLAG = "DOCFLOW_ENV"
STORAGE_SETTINGS = ("storage_s3_endpoint", "storage_s3_region", "storage_s3_access_key_id",
                    "storage_s3_secret_access_key")
_API = Path(__file__).resolve().parents[1] / "apps" / "api"

EXIT_REFUSED_ENVIRONMENT = 2
EXIT_LOCK_HELD = 3
EXIT_REFUSED_TENANT = 4


class SuiteLockHeld(Exception):
    """A test suite (or another sweep) holds the run lock."""


def _database_problem(url: str) -> str | None:
    from sqlalchemy.engine import make_url

    if not url:
        return "ADMIN_DATABASE_URL is not set"
    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001 -- an unreadable URL is refused, never passed
        return "ADMIN_DATABASE_URL can't be read"
    host = (parsed.host or "").lower()
    user = (parsed.username or "").lower()
    if STAGING_PROJECT_REF in host.split("."):
        return None  # the direct host, db.<ref>.supabase.co
    if host.endswith(".pooler.supabase.com") and user.endswith("." + STAGING_PROJECT_REF):
        return None  # the pooler names the project in the user, <login>.<ref>
    return f"ADMIN_DATABASE_URL points at {host or 'no host'}, not staging's project"


def _storage_problem(endpoint: str) -> str | None:
    if not endpoint:
        return "STORAGE_S3_ENDPOINT is not set"
    host = (urlsplit(endpoint).hostname or "").lower()
    if STAGING_PROJECT_REF in host.split("."):
        return None  # <ref>.storage.supabase.co
    return f"STORAGE_S3_ENDPOINT points at {host or 'no host'}, not staging's project"


def check_environment(
    environ: Mapping[str, str], admin_database_url: str, storage_endpoint: str
) -> list[str]:
    """Why this process may not sweep, one line each; empty when it may. The
    two flags are read from `environ`, which the script passes as the process
    environment itself, so a line in `.env` can't open the door."""
    problems = []
    if environ.get(STAGING_RUN_FLAG) != "1":
        problems.append(f"{STAGING_RUN_FLAG}=1 is not in the process environment")
    if environ.get(ENVIRONMENT_FLAG) != "staging":
        problems.append(f"{ENVIRONMENT_FLAG}=staging is not in the process environment")
    for problem in (_database_problem(admin_database_url), _storage_problem(storage_endpoint)):
        if problem:
            problems.append(problem)
    return problems


def _settings() -> Any:
    from docflow_core.config import Settings

    return Settings()


def _load_purge() -> Any:
    """apps/api/tests/tenant_cleanup.py, only once the guard has passed."""
    if str(_API) not in sys.path:
        sys.path.insert(0, str(_API))
    return importlib.import_module("tests.tenant_cleanup")


def application_name(mode: str) -> str:
    started = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    return f"docflow-sweep {mode} pid={os.getpid()} host={socket.gethostname()} started={started}"[:63]


def take_lock(session: Any) -> None:
    """The suites' shared key, exclusively, for this transaction only: a
    commit, a rollback or a dropped connection releases it. Refuses while any
    API or worker suite holds the key, shared or not (suite_lock.py)."""
    from sqlalchemy import text
    from tests.suite_lock import SUITE_LOCK_KEY

    if session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": SUITE_LOCK_KEY}).scalar_one():
        return
    holders = session.execute(
        text(
            """
            SELECT coalesce(a.application_name, 'an unnamed connection')
              FROM pg_locks l
              JOIN pg_stat_activity a ON a.pid = l.pid
             WHERE l.locktype = 'advisory' AND l.granted
               AND ((l.classid::bigint << 32) | l.objid::bigint) = :k
             ORDER BY 1
            """
        ),
        {"k": SUITE_LOCK_KEY},
    ).scalars().all()
    holder = "; ".join(holders) or "a test run (its details aren't visible to this login)"
    raise SuiteLockHeld(
        f"a test run holds the suites' run lock: {holder}. Wait for it to finish, then sweep."
    )


def _print_tenant(described: Mapping[str, Any]) -> None:
    print(f"{described['id']}  {described['name']}")
    print(
        f"  created {described['created_at']:%Y-%m-%d}  status {described['status']}  "
        f"onboarding {described['onboarding_status']}  went live {described['went_live_at'] or '-'}"
    )
    rows = described["rows"]
    by_table = ", ".join(f"{k} {v}" for k, v in sorted(rows.items())) or "none"
    print(f"  rows: {sum(rows.values())} -- {by_table}")
    print(f"  Storage objects: {described['objects']}")
    print(f"  Auth accounts left behind: {', '.join(described['auth_emails']) or 'none'}")
    print(f"  marks: {'; '.join(described['marks']) or 'none'}")


def _list(cleanup: Any, bucket: Any) -> int:
    with cleanup.sweep_session(application_name("list"), read_only=True) as session:
        listing = cleanup.sweep_listing(session, bucket)
    for described in listing:
        _print_tenant(described)
    unmarked = sum(1 for described in listing if not described["marks"])
    prefix = cleanup.SWEEP_NAME_PREFIX
    print(f"{len(listing)} tenants older than a day named {prefix!r}; {unmarked} unmarked.")
    return 0


def _delete(cleanup: Any, bucket: Any, tenant_id: str, name: str, backup_folder: str | None) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    default = Path.home() / "docflow-sweep-backups" / f"{tenant_id}-{stamp}"
    folder = Path(backup_folder) if backup_folder else default
    with cleanup.sweep_session(application_name("delete")) as session:
        take_lock(session)
        tenant = cleanup.find_tenant(session, tenant_id)
        if tenant is not None:
            print("What this delete removes:")
            _print_tenant(cleanup.describe_tenant(session, tenant, bucket))
        result = cleanup.purge_swept_tenant(session, tenant_id, name, folder, bucket)
    removed = f"{sum(result.rows.values())} rows, {len(result.objects)} objects"
    print(f"Deleted tenant {result.tenant_id} ({removed}).")
    print(f"Backup in {result.backup_folder}:")
    for file, sha in result.files:
        print(f"  {sha}  {file}")
    print(f"Auth accounts left behind: {', '.join(result.auth_emails) or 'none'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="read-only: the old test tenants, what they hold, their marks")
    delete = commands.add_parser("delete", help="remove one unmarked tenant, under the founder's Grant")
    delete.add_argument("tenant_id")
    delete.add_argument("--name", required=True, help="the tenant's exact name")
    delete.add_argument("--backup-folder", help="a new or empty folder outside the repository")
    args = parser.parse_args(argv)

    settings = _settings()
    problems = check_environment(os.environ, settings.admin_database_url, settings.storage_s3_endpoint)
    missing = [name.upper() for name in STORAGE_SETTINGS if not getattr(settings, name)]
    if missing:
        problems.append(f"Storage isn't fully configured: {', '.join(missing)} not set")
    if problems:
        listed = "\n  ".join(problems)
        print(f"Refused: this sweep runs only against docflow-staging.\n  {listed}", file=sys.stderr)
        return EXIT_REFUSED_ENVIRONMENT

    cleanup = _load_purge()
    from docflow_core.storage import S3Backend

    bucket = S3Backend()
    try:
        if args.command == "list":
            return _list(cleanup, bucket)
        return _delete(cleanup, bucket, args.tenant_id, args.name, args.backup_folder)
    except SuiteLockHeld as held:
        print(f"Refused: {held}", file=sys.stderr)
        return EXIT_LOCK_HELD
    except cleanup.SweepRefused as refused:
        print(f"Refused: {refused}", file=sys.stderr)
        return EXIT_REFUSED_TENANT


if __name__ == "__main__":
    sys.exit(main())
