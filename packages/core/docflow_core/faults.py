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

# A dropped database connection is not a standing state of our code: it
# starts and stops. Until the database retry exists (migration 0038, Q14),
# these two also carry the UTC date in their key, so a second outage in the
# same release is raised again (founder, 2026-10-13). By class name: the
# names SQLAlchemy and the driver both use.
CONNECTION_ERRORS: frozenset[str] = frozenset({"OperationalError", "InterfaceError"})

# The release when the process isn't on Fly (a developer's machine, CI).
UNVERSIONED = "unversioned"


@dataclass(frozen=True)
class Fault:
    """An exception's type and where it passed through our own code."""

    exception_type: str
    module: str
    function: str
    line: int

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
    return Fault(exception_type=type(exc).__name__, module=module, function=function, line=line)


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


def state_key(session: Session, *, alert_type: str, tenant_id: UUID, step: str, fault: Fault) -> str:
    """
    The key of one state: this alert type, for this tenant, at this step,
    with this exception type at this place in our code, in this release. A
    different value in any of them is a different state, so it is raised
    again; the same key is raised once, acknowledged or not.

    A connection error also carries today's UTC date, by the database's
    clock, the one that stamps the alert's row (D-170).
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
        release(),
    ]
    if fault.exception_type in CONNECTION_ERRORS:
        day = session.execute(text("SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD')")).scalar_one()
        parts.append(str(day))
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
