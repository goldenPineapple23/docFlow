"""
No test run reaches a database off this machine unless it says so (founder,
2026-10-19; D-211).

On 2026-10-19 two local runs that were meant to have no database reached
docflow-staging's pooler anyway. The shell "blanked" DATABASE_URL with
PowerShell's `$env:DATABASE_URL = ""`, which deletes the variable, and the
settings then read the staging URL from the root `.env`. The run lock took
one connection each time. A block made in the shell can fail like that
without anyone seeing it, so the suites now check for themselves.

`check()` runs first thing in each suite's conftest, before anything there
can open a connection: the run lock, the leftover count, the schema checks
behind the `requires_*` marks, the admin login, tenant sessions. It reads
every database URL the way the suite will read it -- the environment first,
then the root `.env`, through the same settings class -- and refuses the run
unless each one is blank or on `localhost`, `127.0.0.1` or `db.invalid`.

The one way past it is DOCFLOW_STAGING_RUN=1 in the process's own
environment. A staging run under the founder's grant sets it; nothing else
does. It is read from the environment only, never from `.env`, so a line
left in a file can't open the door.

apps/worker/tests/database_guard.py and packages/core/tests/database_guard.py
are copies of this file (each suite's tests are their own package);
test_database_guard.py fails if they differ.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from docflow_core.config import Settings

# Every database URL a suite, or a script a suite starts, can read.
URL_SETTINGS: tuple[str, ...] = (
    "DATABASE_URL",
    "API_DATABASE_URL",
    "WORKER_DATABASE_URL",
    "ADMIN_DATABASE_URL",
    "STRIPE_DATABASE_URL",
    "SUPERUSER_DATABASE_URL",
)
ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "db.invalid"})
STAGING_RUN_FLAG = "DOCFLOW_STAGING_RUN"


class StagingDatabaseRefused(Exception):
    """A database URL points off this machine and the run doesn't carry the flag."""


class _Urls(Settings):
    """The suite's own settings, plus the one URL only scripts read."""

    superuser_database_url: str = ""


def resolved_urls(env_file: str | None = None) -> dict[str, str]:
    """Each database URL as the suite will read it: environment, then `.env`
    (the root one, unless a test names another)."""
    urls = _Urls(_env_file=env_file) if env_file else _Urls()
    return {name: getattr(urls, name.lower()) for name in URL_SETTINGS}


def _host(url: str) -> str | None:
    # Imported here so this file sorts the same in all three suites (in core,
    # docflow_core is first-party).
    from sqlalchemy.engine import make_url

    try:
        return make_url(url).host
    except Exception:  # noqa: BLE001 -- an unreadable URL is refused, never passed
        return None


def refusals(urls: Mapping[str, str], environ: Mapping[str, str]) -> list[str]:
    """What is wrong, one line per URL; empty when the run may go ahead.
    Names the setting and the host, never the URL (it carries the password)."""
    if environ.get(STAGING_RUN_FLAG) == "1":
        return []
    out = []
    for name in URL_SETTINGS:
        url = urls.get(name, "")
        if not url:
            continue  # no database for this login: nothing to reach
        host = _host(url)
        if host is None:
            out.append(f"{name}: its host can't be read, so it can't be shown to be on this machine")
        elif host.lower() not in ALLOWED_HOSTS:
            source = "the environment" if name in environ else "the root .env"
            out.append(f"{name} (from {source}) points at {host}")
    return out


def check() -> None:
    """Refuse the run before any connection is opened (conftest, first thing)."""
    found = refusals(resolved_urls(), os.environ)
    if found:
        raise StagingDatabaseRefused(
            "This test run would reach a database that isn't on this machine:\n  "
            + "\n  ".join(found)
            + f"\nOnly localhost, 127.0.0.1 and db.invalid are allowed without {STAGING_RUN_FLAG}=1, "
            "which a staging run under the founder's grant sets and nothing else does. To run "
            "without a database, set each URL to postgresql://nobody:nothing@db.invalid:5432/none "
            "(a blank in PowerShell deletes the variable, and the root .env is read instead)."
        )
