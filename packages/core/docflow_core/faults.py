"""
Where one of our own steps failed, said without the exception's message
(Phase 5.5 Stage 4, PR 1a; DECISIONS.md D-203; CLAUDE.md Section 7.10).

A broad `except` on the document path catches our own bugs and passing
faults alike. What the founder needs from either is the exception's TYPE and
the place in OUR code it came through -- never its message. A database
error's text carries the statement's bound values, and those can be customer
data.

- `describe(exc)`: the type and the innermost frame of our own code.
- `for_log(exc)`: the same for a log line, followed by the traceback's
  frames (file, line, function and the source line) and no message.
- `state_key(...)`: the key an alert is raised under ONCE per state
  (`founder_alerts.state_key`, migration 0037), and that every document the
  state hits carries (`documents.fault_keys`). `note_on_document` writes it
  there.
- `database_unavailable(exc)`: whether the database was out of reach (Q14,
  D-206): a document then waits instead of failing.
"""

from __future__ import annotations

import os
import traceback
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

# The top-level packages that are ours: core, the API's and the worker's
# `app`, and the parse service. A frame anywhere else is a library's.
OWN_PACKAGES: tuple[str, ...] = ("docflow_core", "app", "parse_service")

# The database out of reach (Q14; founder, 2026-10-16; D-206), by the
# SQLSTATE the server or the pooler sent: every connection exception (class
# 08), the server shutting down, crashing or starting up (57P01, 57P02,
# 57P03) and too many connections (53300). Anything else with a SQLSTATE --
# a deadlock, a serialization failure, a statement or lock timeout, a bad
# login -- is not an outage, and fails as before.
UNAVAILABLE_SQLSTATE_CLASSES: frozenset[str] = frozenset({"08"})
UNAVAILABLE_SQLSTATES: frozenset[str] = frozenset({"57P01", "57P02", "57P03", "53300"})

# The release when the process isn't on Fly (a developer's machine, CI).
UNVERSIONED = "unversioned"


@dataclass(frozen=True)
class Fault:
    """An exception's type and where it passed through our own code."""

    exception_type: str
    module: str
    function: str
    line: int
    # The driver's SQLSTATE, for a database error that carries one (Q14):
    # part of the key, so a deadlock and a timeout are separate alerts.
    sqlstate: str | None = None

    @property
    def location(self) -> str:
        return f"{self.module}:{self.function}:{self.line}"


def describe(exc: BaseException, *, own: tuple[str, ...] = OWN_PACKAGES) -> Fault:
    """
    The exception's type and the innermost frame of its traceback that is
    in our own code: the line of ours that raised it, or that called the
    library that did. Never the message. With no frame of ours at all (not
    expected), the location says so instead of guessing.
    """
    module, function, line = "unknown", "unknown", 0
    tb = exc.__traceback__
    while tb is not None:
        name = str(tb.tb_frame.f_globals.get("__name__") or "")
        if name.split(".", 1)[0] in own:
            # The qualified name: `Class.method`, as the guard test keys a handler.
            module, function, line = name, tb.tb_frame.f_code.co_qualname, tb.tb_lineno
        tb = tb.tb_next
    return Fault(
        exception_type=type(exc).__name__, module=module, function=function, line=line, sqlstate=sqlstate(exc)
    )


def _driver_error(exc: BaseException) -> BaseException:
    """The driver's own exception: SQLAlchemy wraps it as `.orig`."""
    from sqlalchemy.exc import DBAPIError

    if isinstance(exc, DBAPIError) and exc.orig is not None:
        return exc.orig
    return exc


def sqlstate(exc: BaseException) -> str | None:
    """The SQLSTATE the server sent, read from the driver's original
    exception (psycopg's `sqlstate`). None when there is none: not a database
    error, or one raised before any server answered."""
    value = getattr(_driver_error(exc), "sqlstate", None)
    return value if isinstance(value, str) and value else None


def database_unavailable(exc: BaseException) -> bool:
    """
    Whether the database was out of reach, so the step should wait and try
    again rather than fail (Q14; founder, 2026-10-16; D-206). Exactly:
    - SQLAlchemy found the connection closed or broken
      (`connection_invalidated`; this covers an InterfaceError on a closed
      connection);
    - psycopg's ConnectionTimeout (no connection in time; also what a
      refused connection ends as);
    - a connect that failed before any server answered: psycopg's base
      OperationalError with no SQLSTATE (DNS, refused, the pooler out of
      reach; founder's answer, 2026-10-16). Its subclasses without a
      SQLSTATE (PipelineAborted, CancellationTimeout) are not this;
    - a SQLSTATE in class 08, or 57P01, 57P02, 57P03 or 53300.
    Everything else is not an outage and fails as before.
    """
    import psycopg
    from sqlalchemy.exc import DBAPIError

    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    driver = _driver_error(exc)
    if isinstance(driver, psycopg.errors.ConnectionTimeout):
        return True
    state = sqlstate(exc)
    if type(driver) is psycopg.OperationalError and state is None:
        return True
    return state is not None and (state[:2] in UNAVAILABLE_SQLSTATE_CLASSES or state in UNAVAILABLE_SQLSTATES)


def frames(exc: BaseException) -> str:
    """The traceback's frames as Python prints them -- file, line, function
    and the source line of each -- and nothing of the exception itself: no
    message, no chained exception, no local values."""
    return "".join(traceback.format_tb(exc.__traceback__))


def for_log(exc: BaseException) -> str:
    """`error_type=... location=...`, a newline, then the frames. For a log
    line that must say where something failed and must not carry a value."""
    fault = describe(exc)
    return f"error_type={fault.exception_type} location={fault.location}\n{frames(exc)}"


def release() -> str:
    """The release this process runs: the tag of the image Fly started
    (`FLY_IMAGE_REF`, e.g. `registry.fly.io/app:deployment-01ABC`)."""
    ref = os.environ.get("FLY_IMAGE_REF") or ""
    tag = ref.rsplit(":", 1)[-1].rsplit("/", 1)[-1]
    return tag or UNVERSIONED


def state_key(
    session: Session,
    *,
    alert_type: str,
    tenant_id: UUID,
    step: str,
    fault: Fault,
    release_tag: str | None = None,
) -> str:
    """
    The key of one state: this alert type, for this tenant, at this step,
    with this exception type at this place in our code, in this release. A
    different value in any of them is a different state, so it is raised
    again; the same key is raised once, acknowledged or not.

    `release_tag` names the release the fault happened in when that is not
    this process's: a parse job's fault is the parse app's (D-203, PR 1b-i).

    A database error also carries its SQLSTATE, so a deadlock and a timeout
    at the same place are separate states (Q14, D-206). The UTC date that
    connection errors carried until the database retry existed is gone: an
    outage now waits instead of failing (founder, 2026-10-13 and
    2026-10-16). `session` reads nothing; it stays for the callers.
    """
    parts = [
        "fault",
        alert_type,
        str(tenant_id),
        step,
        fault.exception_type,
        fault.module,
        fault.function,
        str(fault.line),
        release_tag or release(),
    ]
    if fault.sqlstate:
        parts.append(fault.sqlstate)
    return ":".join(parts)


def note_on_document(session: Session, document_id: UUID, key: str) -> None:
    """Add the state's key to the document it hit, once. Runs in the
    document's own tenant session, in the transaction that records the
    document's outcome; it reads and writes no alert."""
    session.execute(
        text(
            """
            UPDATE documents
            SET fault_keys = array_append(coalesce(fault_keys, CAST('{}' AS text[])), CAST(:key AS text))
            WHERE id = :id
              AND NOT (coalesce(fault_keys, CAST('{}' AS text[])) @> ARRAY[CAST(:key AS text)])
            """
        ),
        {"id": str(document_id), "key": key},
    )
