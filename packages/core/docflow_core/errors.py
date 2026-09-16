"""
The error catalog (CLAUDE.md Section 7.16.5) -- the one source for every
user-facing failure message. UI, email, API responses, and intake
auto-replies all render from this catalog. No user-facing string describing
a failure is allowed to exist outside it.

Entries are added here as each phase introduces failures that can reach a
user (Phase 1 adds the file-parsing and quarantine codes, Phase 4 adds
export codes, etc.) -- this file starts small on purpose and grows with the
build, per CLAUDE.md Section 7.16.5's own instruction to "extend for every
failure."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Audience = Literal["tenant", "founder", "both"]
Severity = Literal["info", "warning", "high", "critical"]


@dataclass(frozen=True)
class ErrorCatalogEntry:
    code: str
    title: str
    message: str
    action: str
    severity: Severity
    audience: Audience


# Populated incrementally. Codes are stable once introduced -- never reused
# for a different meaning, never removed once a customer could have seen it.
CATALOG: dict[str, ErrorCatalogEntry] = {
    "AUTH-001": ErrorCatalogEntry(
        code="AUTH-001",
        title="This link has expired",
        message="Your invite link is no longer valid.",
        action="Ask your account owner to resend the invite.",
        severity="warning",
        audience="tenant",
    ),
}


def get_error(code: str) -> ErrorCatalogEntry:
    if code not in CATALOG:
        raise KeyError(
            f"Unknown error catalog code '{code}'. Every user-facing failure must be "
            "added to docflow_core.errors.CATALOG before it can be raised "
            "(CLAUDE.md Section 7.16.5)."
        )
    return CATALOG[code]
