"""
What a file sent as a download carries, shared by the routes that send one.

A filename here came from a stranger, directly or by way of a PO number, so
everything but letters, digits, dot, dash and underscore goes (Section 7.11:
filenames are untrusted).
"""

from __future__ import annotations

import re

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename_part(value: str) -> str:
    return _UNSAFE_FILENAME_CHARS.sub("-", value).strip(".-")


def attachment_headers(filename: str) -> dict[str, str]:
    """`filename` must already be safe. The browser saves the file and stays
    on the page, whatever it makes of the type."""
    return {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        # Never rendered, whatever a browser thinks of the type.
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
