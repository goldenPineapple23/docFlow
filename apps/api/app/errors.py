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
