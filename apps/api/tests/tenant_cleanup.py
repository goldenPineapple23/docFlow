"""
One purge for the API suite's throwaway tenants (the Stage 3 checkpoint's
list; D-197, founder 2026-10-08 and 2026-10-09).

Before this, nine tenant helpers and four cleanup functions each kept a delete
list of their own, and only some of them removed a tenant's founder alerts,
e-mails and scheduled jobs, so a finished run still left rows behind on
docflow-staging. Every helper now ends in `purge_test_tenant`, which removes
the tenant's files, every row that points at the tenant, and the tenant.

Two rules (founder, 2026-10-09):

* **It acts only on a tenant created in this run.** Each helper registers the
  id when it creates the tenant (`register_test_tenant`). An id that was not
  registered in this process is refused with `NotThisRunsTenant`, whatever the
  tenant is called: staging holds tenants no test may ever remove.
* **Test code only.** Nothing under `app/` or `docflow_core/` imports this
  module (`test_tenant_cleanup.py` checks). The product's own delete is
  `admin_data_access.delete_tenant`, which keeps the tenant row, its
  lifecycle log and the founder's audit trail; a test tenant keeps nothing.

The order of the deletes is read from the live schema, children before
parents, so a table added later is purged without anyone editing a list. A
table with no `tenant_id` column that points at a tenant must be named in
`_OUTSIDE_LINKS`; one that isn't stops the purge with its name.

**The one exception: the staging sweep** (D-224; founder, 2026-10-27).
`purge_swept_tenant` removes an old test tenant that no run registered, for
`scripts/sweep_test_tenants.py` only (the boundary test names that one
file). It refuses unless the name is typed exactly, starts "Acme Test", is
older than a day and carries no mark (live, deleted or pending deletion by
the product, named in the records, a seed script's name), and it backs up
every row and file before it removes anything. The script holds the suites'
run lock on the same transaction.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

from docflow_core.config import get_settings
from docflow_core.db import _reset_rls_settings, platform_session
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from tests.suite_lock import session_mode_url


class NotThisRunsTenant(RuntimeError):
    """The purge was asked to remove a tenant this run did not create."""


_registered: set[str] = set()
_purged: set[str] = set()

# Tables with no tenant_id column whose rows point at a tenant, and what the
# purge does with those rows. "delete": the Console's audit rows about a test
# tenant go with it. "unlink": the row outlives the tenant (the test that made
# an intake removes it itself).
_OUTSIDE_LINKS: dict[tuple[str, str], str] = {
    ("admin_actions", "target_tenant_id"): "delete",
    ("onboarding_intakes", "linked_tenant_id"): "unlink",
}


def register_test_tenant(tenant_id: Any) -> None:
    """Called by a helper at the moment it creates a tenant."""
    _registered.add(str(UUID(str(tenant_id))))


def is_registered(tenant_id: Any) -> bool:
    return str(UUID(str(tenant_id))) in _registered


def tenant_tables(session: Session) -> list[str]:
    """Every table in `public` that carries a `tenant_id`."""
    return [
        row[0]
        for row in session.execute(
            text(
                """
                SELECT c.relname FROM pg_class c
                JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
                WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
                ORDER BY c.relname
                """
            )
        )
    ]


def _foreign_keys(session: Session) -> list[tuple[str, str, str, str]]:
    """(child table, child column, parent table, on-delete action) for every
    single-column foreign key in `public`."""
    return [
        (row[0], row[1], row[2], row[3])
        for row in session.execute(
            text(
                """
                SELECT child.relname, a.attname, parent.relname, c.confdeltype::text
                FROM pg_constraint c
                JOIN pg_class child ON child.oid = c.conrelid
                JOIN pg_class parent ON parent.oid = c.confrelid
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
                WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
                """
            )
        )
    ]


def purge_order(session: Session) -> list[str]:
    """
    The tenant tables, each before every table it points at.

    A link that cascades needs no order: the parent's delete takes the child.
    Every other link does, SET NULL included -- a parent deleted first would
    blank the child's column, and a row left half-blank can break a CHECK
    (a line's matched item and its match method go together).
    """
    tables = tenant_tables(session)
    known = set(tables)
    must_go_first: dict[str, set[str]] = {table: set() for table in tables}
    for child, _column, parent, on_delete in _foreign_keys(session):
        if child in known and parent in known and child != parent and on_delete != "c":
            must_go_first[parent].add(child)
    order: list[str] = []
    while must_go_first:
        ready = sorted(table for table, before in must_go_first.items() if not before)
        if not ready:
            raise RuntimeError(
                f"the foreign keys among {sorted(must_go_first)} form a loop; the test purge "
                "(tests/tenant_cleanup.py) needs a rule for it"
            )
        for table in ready:
            order.append(table)
            del must_go_first[table]
        for before in must_go_first.values():
            before.difference_update(ready)
    return order


def outside_links(session: Session) -> list[tuple[str, str, str]]:
    """(table, column, what to do) for every table with no tenant_id that
    points at `tenants`. Raises for one `_OUTSIDE_LINKS` doesn't name."""
    known = set(tenant_tables(session))
    found: list[tuple[str, str, str]] = []
    for child, column, parent, _on_delete in _foreign_keys(session):
        if parent != "tenants" or child in known or child == "tenants":
            continue
        action = _OUTSIDE_LINKS.get((child, column))
        if action is None:
            raise RuntimeError(
                f"{child}.{column} points at a tenant and the test purge (tests/tenant_cleanup.py) "
                "has no rule for it: add it to _OUTSIDE_LINKS"
            )
        found.append((child, column, action))
    return sorted(found)


_bucket: Any = None


def _real_bucket_is_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.storage_s3_endpoint
        and settings.storage_s3_region
        and settings.storage_s3_access_key_id
        and settings.storage_s3_secret_access_key
    )


def _remove_files(tenant_id: UUID) -> None:
    """
    Everything under `tenants/{id}/` in the real bucket. The backend is built
    here, not taken from `storage._get_backend()`: a test may have swapped
    that for the in-memory fake (the outage tests leave it failing while their
    tenant is cleaned up), and a fake holds nothing that outlives the test.
    On a machine with no Storage settings nothing can have been written, so
    there is nothing to remove.
    """
    if not _real_bucket_is_configured():
        return
    from docflow_core.storage import S3Backend, StorageUnavailableError

    global _bucket
    if _bucket is None:
        _bucket = S3Backend()
    backend = _bucket
    prefix = f"tenants/{tenant_id}/"
    keys = backend.list_keys(prefix)
    if keys:
        backend.delete_keys(keys)
        remaining = backend.list_keys(prefix)
        if remaining:
            raise StorageUnavailableError(f"{len(remaining)} objects still under {prefix}")


def purge_test_tenant(tenant_id: Any) -> None:
    """
    Remove a tenant this run created: its files, every row that points at it,
    and the tenant row. Safe to call twice (a cleanup function and then the
    helper's own exit both call it).
    """
    tid = str(UUID(str(tenant_id)))
    if tid not in _registered:
        raise NotThisRunsTenant(
            f"tenant {tid} was not created by this test run, so the test purge will not touch it "
            "(tests/tenant_cleanup.py: register_test_tenant at creation)"
        )
    if tid in _purged:
        return
    _remove_files(UUID(tid))
    with platform_session() as session:
        # One round trip for the whole purge: some thirty statements a tenant,
        # several hundred tenants a run, each a network hop on staging. A
        # statement sent with no parameters may hold several commands, so the
        # id is written into the text -- it is the output of UUID() above,
        # hex and dashes only, and the names come from the database's catalog.
        session.connection().exec_driver_sql(";\n".join(_statements(session, tid)))
    _purged.add(tid)


_plan: tuple[list[tuple[str, str, str]], list[str]] | None = None


def _statements(session: Session, tid: str) -> list[str]:
    global _plan
    if _plan is None:  # the schema does not change during a run
        _plan = (outside_links(session), purge_order(session))
    outside, order = _plan
    literal = f"'{UUID(tid)}'::uuid"
    statements = [
        f'DELETE FROM "{table}" WHERE "{column}" = {literal}'
        if action == "delete"
        else f'UPDATE "{table}" SET "{column}" = NULL WHERE "{column}" = {literal}'
        for table, column, action in outside
    ]
    # A creation's request row is committed before its tenant exists, so it
    # names the tenant only by target_id (D-220).
    statements.append(
        f"DELETE FROM admin_actions WHERE target_tenant_id IS NULL AND target_type = 'tenant' "
        f"AND target_id = {literal}"
    )
    statements += [f'DELETE FROM "{table}" WHERE tenant_id = {literal}' for table in order]
    statements.append(f"DELETE FROM tenants WHERE id = {literal}")
    return statements


def rows_left(session: Session, tenant_id: Any) -> dict[str, int]:
    """
    Every place a row of this tenant could still be: each table's `tenant_id`,
    every other column in `public` whose name ends in `tenant_id`, and the
    tenant row itself. Table -> rows found (only the non-zero ones).
    """
    tid = str(UUID(str(tenant_id)))
    columns = session.execute(
        text(
            """
            SELECT c.relname, a.attname FROM pg_class c
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname LIKE '%tenant\\_id' AND NOT a.attisdropped
            WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
            ORDER BY 1, 2
            """
        )
    ).all()
    left: dict[str, int] = {}
    for table, column in columns:
        count = session.execute(
            text(f"SELECT count(*) FROM {table} WHERE {column} = :t"), {"t": tid}
        ).scalar_one()
        if count:
            left[f"{table}.{column}"] = int(count)
    if session.execute(text("SELECT count(*) FROM tenants WHERE id = :t"), {"t": tid}).scalar_one():
        left["tenants.id"] = 1
    return left


# ── The staging sweep (D-224; founder, 2026-10-27) ──────────────────────────
# Old test tenants on docflow-staging that no run registered: listed by
# scripts/sweep_test_tenants.py, and removed one at a time under the
# founder's Grant naming the tenant. Everything that decides whether a tenant
# may go lives here, so no caller can skip it.

SWEEP_NAME_PREFIX = "Acme Test"
SWEEP_MIN_AGE_DAYS = 1
_REPO = Path(__file__).resolve().parents[3]
# A creation's request row names its tenant only by target_id (D-220).
_CREATION_ROWS = "target_tenant_id IS NULL AND target_type = 'tenant' AND target_id = CAST(:t AS uuid)"


class SweepRefused(RuntimeError):
    """purge_swept_tenant won't remove this tenant. Nothing was removed."""


@lru_cache(maxsize=1)
def _record_lines() -> tuple[tuple[str, int, str], ...]:
    """Every line of every Markdown file git tracks: DECISIONS, BUILD-STATUS,
    status-history, RUNBOOK, CHECKPOINTS, the designs. Fails closed: with no
    records to read, no tenant can be shown to be unrecorded."""
    listed = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files", "-z", "--", "*.md"], capture_output=True, check=True
    )
    files = sorted(name for name in listed.stdout.decode("utf-8").split("\0") if name)
    if "DECISIONS.md" not in files:
        raise SweepRefused("the records (DECISIONS.md and the rest) can't be read, so no tenant can be swept")
    lines: list[tuple[str, int, str]] = []
    for name in files:
        content = (_REPO / name).read_text(encoding="utf-8", errors="replace")
        lines.extend((name, number, line) for number, line in enumerate(content.splitlines(), 1))
    return tuple(lines)


def records_naming(tenant_id: Any, name: str) -> list[str]:
    """`file:line` for every record line that names the tenant: its id (the
    first eight characters are how the records quote one) or its exact name."""
    tid = str(UUID(str(tenant_id)))
    by_id = re.compile(rf"\b{tid[:8]}")
    return [
        f"{file}:{number}"
        for file, number, line in _record_lines()
        if by_id.search(line.lower()) or (name and name in line)
    ]


@lru_cache(maxsize=1)
def seed_names() -> tuple[frozenset[str], frozenset[str]]:
    """The tenant names the seed scripts create: exact names, and the fixed
    start of the names they build with an f-string."""
    exact: set[str] = set()
    starts: set[str] = set()
    for path in sorted((_REPO / "scripts").glob("seed_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.JoinedStr) and node.values:
                first = node.values[0]
                if isinstance(first, ast.Constant) and str(first.value).startswith(SWEEP_NAME_PREFIX):
                    starts.add(str(first.value))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.startswith(SWEEP_NAME_PREFIX):
                    exact.add(node.value)
    if not exact:
        raise SweepRefused("no seed script names a tenant any more, so the seed-name mark can't be checked")
    return frozenset(exact), frozenset(starts)


def tenant_marks(tenant: Mapping[str, Any]) -> list[str]:
    """What keeps a tenant from being swept, whatever else is true of it. A
    marked tenant is listed but always refused (founder, 2026-10-27)."""
    marks = []
    if tenant["onboarding_status"] == "live" or tenant["went_live_at"] is not None:
        marks.append("LIVE")
    if tenant["status"] == "deleted":
        marks.append("DELETED BY THE PRODUCT")
    if tenant["status"] == "pending_deletion":
        marks.append("PENDING DELETION")
    named = records_naming(tenant["id"], tenant["name"])
    if named:
        more = f" and {len(named) - 3} more" if len(named) > 3 else ""
        marks.append(f"NAMED IN RECORDS ({', '.join(named[:3])}{more})")
    exact, starts = seed_names()
    if tenant["name"] in exact or any(tenant["name"].startswith(start) for start in starts):
        marks.append("SEED NAME")
    return marks


_TENANT_ROW = """
    SELECT id, name, status, onboarding_status, went_live_at, created_at,
           created_at < now() - make_interval(days => :days) AS old_enough
      FROM tenants
"""


def _holdings(session: Session, tid: str) -> dict[str, int]:
    """Every row the purge would remove or unlink, by `table.column`."""
    held = rows_left(session, tid)
    creation = session.execute(text(f"SELECT count(*) FROM admin_actions WHERE {_CREATION_ROWS}"), {"t": tid})
    if count := int(creation.scalar_one()):
        held["admin_actions.target_id"] = count
    return held


def _emails(session: Session, tid: str) -> list[str]:
    """The tenant's users' addresses: their Supabase Auth accounts stay behind
    (out of scope; founder, 2026-10-27), so the output names them."""
    query = text(
        "SELECT email FROM users WHERE tenant_id = CAST(:t AS uuid) AND email IS NOT NULL ORDER BY email"
    )
    return [str(email) for email in session.execute(query, {"t": tid}).scalars()]


def describe_tenant(session: Session, tenant: Mapping[str, Any], bucket: Any) -> dict[str, Any]:
    """What a tenant holds and what marks it: the listing, and what a delete
    of it would lose."""
    tid = str(tenant["id"])
    return {
        "id": tid,
        "name": tenant["name"],
        "created_at": tenant["created_at"],
        "status": tenant["status"],
        "onboarding_status": tenant["onboarding_status"],
        "went_live_at": tenant["went_live_at"],
        "rows": _holdings(session, tid),
        "objects": len(bucket.list_keys(f"tenants/{tid}/")),
        "auth_emails": _emails(session, tid),
        "marks": tenant_marks(tenant),
    }


def sweep_listing(session: Session, bucket: Any) -> list[dict[str, Any]]:
    """Every "Acme Test" tenant older than a day, marked or not, oldest first."""
    tenants = session.execute(
        text(_TENANT_ROW + " WHERE name LIKE :prefix AND created_at < now() - make_interval(days => :days) "
             "ORDER BY created_at, id"),
        {"prefix": SWEEP_NAME_PREFIX + "%", "days": SWEEP_MIN_AGE_DAYS},
    ).mappings()
    return [describe_tenant(session, tenant, bucket) for tenant in tenants.all()]


def find_tenant(session: Session, tenant_id: Any) -> Mapping[str, Any] | None:
    return session.execute(
        text(_TENANT_ROW + " WHERE id = CAST(:t AS uuid)"),
        {"t": str(UUID(str(tenant_id))), "days": SWEEP_MIN_AGE_DAYS},
    ).mappings().one_or_none()


@contextmanager
def sweep_session(application_name: str, *, read_only: bool = False) -> Iterator[Session]:
    """
    One connection as docflow_admin, through the pooler's session mode (as the
    run lock's own connection is: suite_lock.py), and one REPEATABLE READ
    transaction on it. The sweep's lock, its checks, its backup and its deletes
    all run in this one transaction, so they see one snapshot, and a dropped
    connection rolls the deletes back and releases the lock together (founder,
    2026-10-27). Commits on a clean exit unless read-only.
    """
    admin_url = get_settings().admin_database_url
    if not admin_url:
        raise SweepRefused("ADMIN_DATABASE_URL is not set, so there is no docflow_admin login to sweep with")
    url = make_url(session_mode_url(admin_url)).set(drivername="postgresql+psycopg")
    engine = create_engine(url, poolclass=NullPool, connect_args={"prepare_threshold": None})
    try:
        with engine.connect() as connection:
            connection = connection.execution_options(
                isolation_level="REPEATABLE READ", postgresql_readonly=read_only
            )
            session = Session(bind=connection)
            try:
                _reset_rls_settings(session)
                # Session-level, so a suite refused while the sweep holds the
                # lock names it (suite_lock._holder reads application_name).
                session.execute(
                    text("SELECT set_config('application_name', :name, false)"),
                    {"name": application_name[:63]},
                )
                session.execute(text("SELECT set_config('app.is_platform_admin', 'true', true)"))
                yield session
                if read_only:
                    session.rollback()
                else:
                    session.commit()
            except BaseException:
                session.rollback()
                raise
            finally:
                session.close()
    finally:
        engine.dispose()


@dataclass
class SweepResult:
    tenant_id: str
    name: str
    rows: dict[str, int]
    objects: list[str]
    auth_emails: list[str]
    backup_folder: Path
    files: list[tuple[str, str]] = field(default_factory=list)  # (path in the folder, SHA-256)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _backup_folder(backup_folder: Any) -> Path:
    folder = Path(backup_folder).resolve()
    if folder == _REPO or _REPO in folder.parents:
        raise SweepRefused(f"the backup folder {folder} is inside the repository; use one outside it")
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise SweepRefused(f"the backup folder {folder} already holds something; give an empty or new one")
    return folder


def _unbacked_links(session: Session) -> list[str]:
    """Links from a table without a tenant_id into a tenant's rows that would
    delete or blank a row the backup doesn't hold. Today there are none: every
    such link is NO ACTION, so a missed one fails the delete and rolls back."""
    known = set(tenant_tables(session))
    return sorted(
        f"{child}.{column} -> {parent} (on delete {on_delete})"
        for child, column, parent, on_delete in _foreign_keys(session)
        if child not in known
        and child != "tenants"
        and (parent in known or parent == "tenants")
        and on_delete in ("c", "n", "d")
        and (child, column) not in _OUTSIDE_LINKS
    )


def purge_swept_tenant(
    session: Session, tenant_id: Any, confirmed_name: str, backup_folder: Any, bucket: Any
) -> SweepResult:
    """
    Remove one old, unmarked test tenant that no run registered (D-224), on
    `session`: a `sweep_session`, whose transaction the caller commits.

    In order: every check (any failure refuses, and nothing is touched); the
    backup of every row and file into `backup_folder`, outside the repository,
    each file read back against its SHA-256 and the rows counted against the
    database; the deletes; the check that nothing points at the tenant any
    more; then its files under `tenants/{id}/`, after the rows and before the
    commit. A connection lost after the files go leaves the rows and the
    backup; one lost before leaves everything.
    """
    try:
        tid = str(UUID(str(tenant_id)))
    except ValueError:
        raise SweepRefused(f"{tenant_id!r} is not a tenant id") from None
    isolation = session.execute(text("SHOW transaction_isolation")).scalar_one()
    if isolation != "repeatable read":
        raise SweepRefused(
            f"the sweep needs one REPEATABLE READ transaction (sweep_session); this is {isolation}"
        )

    tenant = session.execute(
        text(_TENANT_ROW + " WHERE id = CAST(:t AS uuid) FOR UPDATE"), {"t": tid, "days": SWEEP_MIN_AGE_DAYS}
    ).mappings().one_or_none()
    if tenant is None:
        raise SweepRefused(f"no tenant {tid}")
    reasons = []
    if tenant["name"] != confirmed_name:
        reasons.append(f"the name typed, {confirmed_name!r}, is not the tenant's name, {tenant['name']!r}")
    if not tenant["name"].startswith(SWEEP_NAME_PREFIX):
        reasons.append(f"its name doesn't start {SWEEP_NAME_PREFIX!r}")
    if not tenant["old_enough"]:
        reasons.append(f"it is less than {SWEEP_MIN_AGE_DAYS} day old (created {tenant['created_at']})")
    reasons += [f"it is marked {mark}" for mark in tenant_marks(tenant)]
    unbacked = _unbacked_links(session)
    if unbacked:
        reasons.append(f"a delete would change rows the backup doesn't hold: {unbacked}")
    if reasons:
        raise SweepRefused(f"tenant {tid} is not swept: " + "; ".join(reasons))
    folder = _backup_folder(backup_folder)

    # Each set of rows the deletes below touch, with the statement that removes
    # (or, for an "unlink" table, detaches) it.
    targets: list[tuple[str, str, str]] = []
    for table, column, action in outside_links(session):
        where = f'"{column}" = CAST(:t AS uuid)'
        change = f'DELETE FROM "{table}"' if action == "delete" else f'UPDATE "{table}" SET "{column}" = NULL'
        targets.append((f"{table}.{column}", f'"{table}" WHERE {where}', f"{change} WHERE {where}"))
    creation = f"admin_actions WHERE {_CREATION_ROWS}"
    targets.append(("admin_actions.target_id", creation, f"DELETE FROM {creation}"))
    for table in purge_order(session):
        source = f'"{table}" WHERE tenant_id = CAST(:t AS uuid)'
        targets.append((f"{table}.tenant_id", source, f"DELETE FROM {source}"))
    tenant_row = "tenants WHERE id = CAST(:t AS uuid)"
    targets.append(("tenants.id", tenant_row, f"DELETE FROM {tenant_row}"))

    # The rows, as Postgres writes them (to_jsonb: money stays exact text).
    backed_up: dict[str, list[str]] = {}
    for label, source, _change in targets:
        aliased = source.replace(" WHERE", " x WHERE", 1)
        rows = session.execute(text(f"SELECT to_jsonb(x)::text FROM {aliased}"), {"t": tid})
        found = [str(row) for row in rows.scalars()]
        if found:
            backed_up[label] = found
    counts = {label: len(rows) for label, rows in backed_up.items()}
    tables = ",\n".join(
        json.dumps(label) + ": [\n" + ",\n".join(rows) + "\n]" for label, rows in backed_up.items()
    )
    rows_json = (
        '{\n"tenant_id": ' + json.dumps(tid) + ',\n"name": ' + json.dumps(tenant["name"])
        + ',\n"tables": {\n' + tables + "\n}\n}\n"
    ).encode("utf-8")

    prefix = f"tenants/{tid}/"
    from docflow_core.storage import check_tenant_path

    keys = sorted(bucket.list_keys(prefix))
    objects: dict[str, bytes] = {key: bucket.get(check_tenant_path(UUID(tid), key)) for key in keys}
    emails = _emails(session, tid)

    folder.mkdir(parents=True, exist_ok=True)
    written: dict[str, bytes] = {"rows.json": rows_json}
    written.update({f"storage/{key}": data for key, data in objects.items()})
    manifest = {
        "tenant_id": tid,
        "name": tenant["name"],
        "created_at": str(tenant["created_at"]),
        "rows": counts,
        "objects": [{"key": key, "file": f"storage/{key}", "sha256": _sha256(data), "bytes": len(data)}
                    for key, data in objects.items()],
        "orphaned_auth_emails": emails,
        "files": {name: _sha256(data) for name, data in written.items()},
    }
    written["manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    for name, data in written.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    # Read back before anything goes: every file as written, every row in the
    # file, and the file's rows still what the database holds.
    files = []
    for name, data in written.items():
        on_disk = (folder / name).read_bytes()
        if _sha256(on_disk) != _sha256(data):
            raise SweepRefused(f"the backup file {name} doesn't read back as written; nothing was removed")
        files.append((name, _sha256(on_disk)))
    parsed = json.loads((folder / "rows.json").read_text(encoding="utf-8"), parse_float=Decimal)
    if {label: len(rows) for label, rows in parsed["tables"].items()} != counts:
        raise SweepRefused("the backup's rows don't match what was read; nothing was removed")
    for label, source, _change in targets:
        now = int(session.execute(text(f"SELECT count(*) FROM {source}"), {"t": tid}).scalar_one())
        if now != counts.get(label, 0):
            raise SweepRefused(
                f"{label} holds {now} rows but the backup has {counts.get(label, 0)}; nothing was removed"
            )

    for _label, _source, change in targets:
        session.execute(text(change), {"t": tid})
    left = _holdings(session, tid)
    if left:
        raise RuntimeError(f"rows of tenant {tid} are still there after the deletes: {left}")

    # Storage isn't transactional: its files go after the rows, before the commit.
    if sorted(bucket.list_keys(prefix)) != keys:
        raise SweepRefused(f"the files under {prefix} changed after the backup; nothing was removed")
    if keys:
        bucket.delete_keys(keys)
        remaining = bucket.list_keys(prefix)
        if remaining:
            from docflow_core.storage import StorageUnavailableError

            raise StorageUnavailableError(f"{len(remaining)} objects still under {prefix}")
    return SweepResult(
        tenant_id=tid,
        name=tenant["name"],
        rows=counts,
        objects=keys,
        auth_emails=emails,
        backup_folder=folder,
        files=files,
    )
