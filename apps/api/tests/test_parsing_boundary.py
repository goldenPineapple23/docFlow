"""
CLAUDE.md Section 7.11 / Section 10: parsing never runs in the web process.

    "Parsing never runs in the web process. All document parsing (PDF text
     extraction, DOCX/XLSX/RTF reading, image decoding) runs in an isolated
     worker with a hard memory limit, a hard CPU/time limit per file, and no
     network access. A worker that dies takes one document to `failed`,
     never the app."

Section 10 states the same thing as a prohibition: "Parse or convert any
uploaded file in the web process."

This is a dependency-graph test, not a code review comment. It fails CI if
the boundary is ever crossed -- which is the point: the rule is easy to
break by accident, because pulling in a parser to render something is always
the shortest path to a working screen. It was nearly broken exactly that way
when the document viewer needed to show TIFF and Word files; the previews
are produced in the worker instead (DECISIONS.md D-092).

The reason is worth restating, since the cost of obeying it is real: these
files arrive from a distributor's buyers, through a public intake address,
from people DocFlow has no relationship with. A malformed TIFF that hangs or
exhausts memory inside the API process takes down the whole application for
every tenant. The same file inside the worker takes one document to `failed`.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

# Modules that decode, parse, convert or render a document's bytes. Importing
# any of these into the web process is the thing Section 7.11 forbids.
FORBIDDEN_MODULES = {
    "PIL",
    "docx",
    "openpyxl",
    "pypdf",
    "pdfplumber",
    "fitz",
    "extract_msg",
    "striprtf",
    "defusedxml",
    "zipfile",
}

# `docflow_core` modules whose whole job is parsing.
FORBIDDEN_CORE_MODULES = {"previews", "parsing", "conversion", "exports"}


def _offending_imports(py_file: Path) -> list[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    found: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in FORBIDDEN_MODULES:
                    found.append(alias.name)
                if top == "docflow_core":
                    tail = alias.name.split(".")[-1]
                    if tail in FORBIDDEN_CORE_MODULES:
                        found.append(alias.name)

        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            top = module.split(".")[0]
            if top in FORBIDDEN_MODULES:
                found.append(module)
            if top == "docflow_core":
                tail = module.split(".")[-1]
                if tail in FORBIDDEN_CORE_MODULES:
                    found.append(module)
                for alias in node.names:
                    if alias.name in FORBIDDEN_CORE_MODULES:
                        found.append(f"{module}.{alias.name}")

    return found


def test_the_web_process_never_imports_a_document_parser():
    violations: dict[str, list[str]] = {}
    for py_file in APP_ROOT.rglob("*.py"):
        offending = _offending_imports(py_file)
        if offending:
            violations[str(py_file.relative_to(APP_ROOT.parent))] = offending

    assert not violations, (
        "Section 7.11: parsing never runs in the web process. These files in apps/api/app "
        f"import a parser: {violations}. Produce whatever is needed in the worker and have "
        "the API serve the stored result."
    )


def test_the_scan_would_actually_catch_something():
    """
    A guard that cannot fail is not a guard. This proves the AST walk finds
    a forbidden import when one is present, so a green result above means
    "nothing found" rather than "nothing looked at".
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        sample = Path(tmp) / "offender.py"
        sample.write_text(
            "from PIL import Image\nfrom docflow_core.previews import build_preview\n",
            encoding="utf-8",
        )
        found = _offending_imports(sample)

    assert "PIL" in found
    assert any("previews" in item for item in found)
