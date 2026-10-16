"""
What the service log may say about a job's stderr (CLAUDE.md 7.10; D-204
item 4a, PR 1b-i): its length, a short hash and a known category, never its
text.

A job's stderr is not ours to log. The job reads a hostile file, and since
the answer channel (D-204 item 4a) whatever a library prints lands there
too: xlrd's size warning, pdfminer's own log lines quoting raw bytes of the
file, LibreOffice's words about a document. So a rejected job's line and an
isolation failure's evidence carry this summary instead of a tail. The hash
lets two jobs with the same stderr be matched; a category names a pattern we
know. A crashed job's stderr is collected and not logged (unchanged).
"""

from __future__ import annotations

import hashlib
import re

# A pattern we know, by a fixed name. Adding one is one line and a test.
KNOWN: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    (
        "xlrd_sector_size",
        re.compile(rb"WARNING \*\*\* file size \(\d+\) not 512 \+ multiple of sector size \(512\)"),
    ),
)
NO_CATEGORY = "none"
HASH_CHARS = 12


def summarize(raw: bytes, *, truncated: bool = False) -> dict:
    """`truncated`: the job wrote more than the supervisor keeps (the length is what it kept)."""
    return {
        "bytes": len(raw),
        "truncated": truncated,
        "sha256": hashlib.sha256(raw).hexdigest()[:HASH_CHARS],
        "category": next((name for name, pattern in KNOWN if pattern.search(raw)), NO_CATEGORY),
    }


def for_log(summary: dict | None) -> str:
    if not summary:
        return "stderr_bytes=0 stderr_sha256=- stderr_category=none"
    size = f"{summary['bytes']}+" if summary.get("truncated") else str(summary["bytes"])
    return f"stderr_bytes={size} stderr_sha256={summary['sha256']} stderr_category={summary['category']}"
