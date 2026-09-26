"""
The proof of concept's buyer name stays out of the code (D-159).

The golden fixture used to name a buyer that could belong to a real café,
with a street address and a real-looking domain. CLAUDE.md Section 0 rule 4:
test data must be unmistakably fake. It was renamed to "Acme's Test Coffee
House" in Phase 5.5 Stage 1c; this fails the build if the old name comes back
into the code, the tests or the scripts. `docs/` (the founder's source files)
and the decision/checkpoint logs keep it as history and are not scanned.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCANNED = ("apps", "packages", "scripts", "supabase")
SKIPPED_DIRS = {"node_modules", ".venv", ".next", "__pycache__", ".pytest_cache", ".mypy_cache",
                ".ruff_cache", "test-results", "playwright-report", "storage"}
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".json", ".txt", ".sql", ".md", ".csv", ".html", ".toml"}
# Built from parts so this file doesn't match itself.
OLD_NAME = "bel" + "la"


def _files():
    for top in SCANNED:
        for path in (REPO / top).rglob("*"):
            if path.suffix in SUFFIXES and not SKIPPED_DIRS.intersection(path.parts) and path.is_file():
                yield path


def test_the_proof_of_concept_buyer_name_is_not_in_the_code():
    found = [
        str(path.relative_to(REPO))
        for path in _files()
        if OLD_NAME in path.read_text(encoding="utf-8", errors="ignore").lower()
    ]
    assert not found, f"use an unmistakably fake name (Section 0 rule 4, D-159): {found}"
