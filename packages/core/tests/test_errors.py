"""
The error catalog's own rules (CLAUDE.md Section 7.16.5):

  - "a test enumerates every throw/reject site that can reach a user and
    asserts it references a catalog code"
  - "a snapshot test renders every catalog entry so a wording change is a
    visible diff"
  - "the action field is non-empty for every tenant-audience entry"

plus the tone rules ("never say 'error occurred' or 'something went wrong'
without the what/why/next", titles of eight words or fewer).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from docflow_core.errors import CATALOG, get_error

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "error_catalog.txt"

# Every place a code can be raised from. apps/web is deliberately absent:
# the UI renders catalog entries the API hands it, it never invents one.
SOURCE_DIRECTORIES = (
    REPO_ROOT / "packages" / "core" / "docflow_core",
    REPO_ROOT / "apps" / "api" / "app",
    REPO_ROOT / "apps" / "worker" / "app",
)

CODE_PATTERN = re.compile(r"\b(?:DOC|INT|AUTH|LIM|EXP|VAL|REV|CON)-\d{3}\b")


def _render_catalog() -> str:
    lines = []
    for code in sorted(CATALOG):
        entry = CATALOG[code]
        lines.append(f"{entry.code} [{entry.severity}/{entry.audience}] {entry.title}")
        lines.append(f"  what+why: {entry.message}")
        lines.append(f"  next:     {entry.action}")
        lines.append("")
    return "\n".join(lines)


def test_every_entry_is_keyed_by_its_own_code():
    for code, entry in CATALOG.items():
        assert entry.code == code


def test_titles_are_eight_words_or_fewer():
    for entry in CATALOG.values():
        assert len(entry.title.split()) <= 8, entry.code


def test_every_entry_says_what_why_and_what_next():
    for entry in CATALOG.values():
        assert entry.message.strip(), entry.code
        assert entry.action.strip(), entry.code


def test_tenant_facing_entries_have_an_action():
    for entry in CATALOG.values():
        if entry.audience in ("tenant", "both"):
            assert entry.action.strip(), entry.code


def test_no_entry_uses_a_banned_empty_phrase():
    banned = ("something went wrong", "an error occurred", "unexpected error")
    for entry in CATALOG.values():
        blob = f"{entry.title} {entry.message}".lower()
        for phrase in banned:
            assert phrase not in blob, f"{entry.code} uses '{phrase}'"


def test_no_entry_blames_the_user():
    for entry in CATALOG.values():
        blob = f"{entry.title} {entry.message}".lower()
        assert "you uploaded an invalid" not in blob
        assert "you sent an invalid" not in blob


def test_every_code_referenced_in_source_exists_in_the_catalog():
    referenced: dict[str, list[str]] = {}
    for directory in SOURCE_DIRECTORIES:
        for path in directory.rglob("*.py"):
            for code in CODE_PATTERN.findall(path.read_text(encoding="utf-8")):
                referenced.setdefault(code, []).append(str(path.relative_to(REPO_ROOT)))
    assert referenced, "no error codes found in source -- the scan is broken, not the code"
    missing = {code: files for code, files in referenced.items() if code not in CATALOG}
    assert not missing, f"codes raised in source but absent from the catalog: {missing}"


def test_unknown_code_is_a_loud_failure_not_a_silent_default():
    with pytest.raises(KeyError):
        get_error("DOC-999")


def test_catalog_snapshot_matches():
    """
    A rendered snapshot of every entry, so a wording change shows up as a
    reviewable diff rather than slipping past. Regenerate deliberately (and
    read the diff) when an entry legitimately changes.
    """
    rendered = _render_catalog()
    assert SNAPSHOT_PATH.exists(), (
        f"Catalog snapshot missing at {SNAPSHOT_PATH}. Write the current rendering there "
        "only after reviewing it."
    )
    assert rendered == SNAPSHOT_PATH.read_text(encoding="utf-8")
