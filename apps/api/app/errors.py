"""
Rendering the error catalog as HTTP responses (CLAUDE.md Section 7.16.5).

One place, so every route answers a failure the same way and no route
invents a string of its own. The catalog holds the what / why / what-next;
this decides the status code and the response shape.

Section 7.16.5's "never expose internals" is structural here rather than a
rule someone has to remember: the response body is built from catalog fields
only. A stack trace, a library name, an SQL fragment or a model-provider
error cannot reach a tenant through this function, because it never reads
anything but the entry.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from docflow_core.errors import get_error
from fastapi import HTTPException


def catalog_detail(code: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """The response body for a catalog code: what happened, why, what next."""
    entry = get_error(code)
    detail: dict[str, Any] = {
        "code": entry.code,
        "title": entry.title,
        "message": entry.message,
        "action": entry.action,
    }
    if extra:
        # Machine-readable specifics the UI can use to anchor the message --
        # which field, which line, which warnings. Never prose: the prose is
        # the catalog's job, and a second source of wording is exactly what
        # Section 7.16.5 forbids.
        detail["detail"] = extra
    return detail


def catalog_error(code: str, status_code: int, extra: dict[str, Any] | None = None) -> HTTPException:
    return HTTPException(status_code=status_code, detail=catalog_detail(code, extra))


# Every request field typed EmailStr. tests/test_validation_catalog.py walks
# the routes and fails if this list and the code ever disagree (D-241).
EMAIL_FIELDS = frozenset({"owner_email", "contact_email"})

# A field name the answer may repeat: a model's own attribute name or a list
# index. Anything else in an error's location came from the request itself
# (an unexpected key, say), and the answer never repeats what was sent.
_FIELD_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def validation_refusal(errors: Sequence[Mapping[str, Any]]) -> tuple[str, list[str]]:
    """
    A request refused before its route ran, in the catalog's words (D-240
    item 9; D-241): REQ-002 when every failing field is an e-mail address,
    REQ-001 otherwise, with the failing fields' names. Built from each
    error's location only, never from its message or input, so neither the
    value sent nor the validation library's wording reaches the response.
    """
    fields: list[str] = []
    email_only = bool(errors)
    for error in errors:
        loc = tuple(error.get("loc", ()))
        parts = loc[1:] if loc and loc[0] in ("body", "query", "path", "header", "cookie") else loc
        # A field starts with its name; a number there is where a body that
        # isn't JSON stopped parsing, not a field. Later numbers are list indexes.
        named = bool(parts) and isinstance(parts[0], str) and _FIELD_PART.match(parts[0])
        if not named or not all(isinstance(p, int) or _FIELD_PART.match(str(p)) for p in parts):
            email_only = False
            continue
        name = ".".join(str(p) for p in parts)
        email_only = email_only and name in EMAIL_FIELDS
        if name not in fields:
            fields.append(name)
    return ("REQ-002" if email_only else "REQ-001"), fields
