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

CODE_PATTERN = re.compile(
    r"\b(?:DOC|INT|AUTH|LIM|QUA|EXP|VAL|REV|CON|IMP|CAT|BUY|TEAM|SYS|BIL|REQ|APP)-\d{3}\b"
)


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


def test_no_entry_spells_email_with_a_hyphen():
    """
    The catalog spells it "email" (founder, 2026-11-17; docs/designs/b1.md
    section 12; D-245). Checked in every entry's title, message and action,
    ignoring case, so "E-mail" fails too. Only the catalog: screen text
    outside it changes with B1's slice 5.
    """
    offenders = [
        f"{entry.code}.{part}"
        for entry in CATALOG.values()
        for part, value in (("title", entry.title), ("message", entry.message), ("action", entry.action))
        if "e-mail" in value.lower()
    ]
    assert not offenders, f'catalog text spelling "e-mail": {offenders}'


# Matches an optional schema and quoted names: document_warnings,
# public.document_warnings, "document_warnings", "public"."document_warnings".
WARNING_INSERT = re.compile(
    r'INSERT\s+INTO\s+(?:"?\w+"?\s*\.\s*)?"?document_warnings"?(?![\w"])', re.IGNORECASE
)


def test_no_path_that_writes_document_warnings_names_val_020():
    """
    VAL-020 is shown in the email panel only and is never a warning row on a
    document (founder, 2026-11-17; docs/designs/b1.md section 12; D-245).

    Every module that inserts into document_warnings is found by its SQL, so
    a new writer anywhere in core, the API or the worker is caught here and
    must be read before this list grows. Today that is validation.py alone,
    which writes only the codes in _ALL_CODES (its rules) and PARSE_STEP_CODES
    (the parse step's). B1's slice 4 or 5 adds a database check that no
    document has a VAL-020 warning.
    """
    from docflow_core import validation

    writers = sorted(
        str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        for directory in SOURCE_DIRECTORIES
        for path in directory.rglob("*.py")
        if WARNING_INSERT.search(path.read_text(encoding="utf-8"))
    )
    assert writers == ["packages/core/docflow_core/validation.py"], (
        f"modules that write document warnings changed: {writers}; "
        "check each for VAL-020, then update this test"
    )
    for writer in writers:
        assert "VAL-020" not in (REPO_ROOT / writer).read_text(encoding="utf-8"), writer
    assert "VAL-020" not in validation._ALL_CODES
    assert "VAL-020" not in validation.PARSE_STEP_CODES


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


def test_the_web_apps_mirrored_entries_are_word_for_word_the_catalogs():
    """
    Failures that happen in the browser -- a wrong authenticator code, which
    Supabase refuses before our API sees anything -- are rendered from a copy
    of their catalog entry in apps/web/src/lib/catalogMirror.ts (D-177). A
    copy can drift; this fails the build the moment it does, so the wording is
    changed in the catalog and copied, never edited in one place only.
    """
    mirror = (REPO_ROOT / "apps" / "web" / "src" / "lib" / "catalogMirror.ts").read_text(encoding="utf-8")
    blocks = re.findall(r"export const ((?:AUTH|APP)_\d{3}): CatalogError = \{(.*?)\n\};", mirror, re.DOTALL)
    assert blocks, "no mirrored entries found in catalogMirror.ts"
    # The web app's fallbacks (D-241) are rendered only from this copy, so
    # their absence would mean the page shows a string outside the catalog.
    mirrored = {name for name, _ in blocks}
    assert {"AUTH_008", "AUTH_009", "APP_000", "APP_001"} <= mirrored, mirrored
    for name, body in blocks:
        fields = {
            key: re.sub(r'"\s*\+\s*"', "", value)
            for key, value in re.findall(r'(\w+):\s*"((?:[^"\\]|\\.)*)"', body)
        }
        entry = get_error(name.replace("_", "-"))
        assert fields == {
            "code": entry.code,
            "title": entry.title,
            "message": entry.message,
            "action": entry.action,
        }, name
