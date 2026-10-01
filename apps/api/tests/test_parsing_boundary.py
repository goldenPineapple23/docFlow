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
are produced elsewhere instead (DECISIONS.md D-092) -- since Stage 3c, in the
parse service's sandboxed jobs (apps/parse).

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
REPO = APP_ROOT.parents[2]
XML_GUARD_DIR = APP_ROOT

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
    # Stage 3c: the parse service and the rest of its libraries.
    "parse_service",
    "pillow_heif",
    "olefile",
    "xlrd",
    # LibreOffice's Python bindings.
    "uno",
    "unohelper",
}

# `docflow_core` modules whose whole job is parsing.
FORBIDDEN_CORE_MODULES = {"previews", "parsing", "conversion", "exports", "catalog_parsing"}


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


# The XML guard (founder, 2026-10-01). lxml is installed in this environment
# because openpyxl writes the .xlsx export through it, and the export's bytes
# depend on it (BUILD-STATUS "3c build"). It is never handed a file: nothing
# here may import lxml directly, or read a workbook with openpyxl.
def _xml_reader_uses(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(f"import {a.name}" for a in node.names if a.name.split(".")[0] == "lxml")
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top == "lxml":
                found.append(f"from {node.module} import ...")
            if top == "openpyxl" and any(a.name == "load_workbook" for a in node.names):
                found.append(f"from {node.module} import load_workbook")
        elif isinstance(node, ast.Attribute) and node.attr == "load_workbook":
            found.append(".load_workbook")
        elif isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == "load_workbook":
                found.append("load_workbook(...)")
            if name in ("import_module", "__import__") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    if first.value.split(".")[0] == "lxml":
                        found.append(f"{name}({first.value!r})")
    return found


def test_no_direct_lxml_import_and_no_workbook_reading():
    offenders = {}
    files = list(XML_GUARD_DIR.rglob("*.py"))
    assert len(files) > 10, "the scan is looking in the wrong place"
    for path in files:
        uses = _xml_reader_uses(path)
        if uses:
            offenders[path.relative_to(REPO).as_posix()] = uses
    assert offenders == {}, (
        "lxml is here only for openpyxl's .xlsx writer; nothing may import it "
        f"or read a workbook: {offenders}"
    )


def test_the_xml_guard_would_catch_each_form(tmp_path):
    """A guard that cannot fail is not a guard: each planted form is found."""
    planted = tmp_path / "planted.py"
    planted.write_text(
        "import lxml.etree\n"
        "from lxml import html\n"
        "import openpyxl\n"
        "from openpyxl import load_workbook as lw\n"
        "openpyxl.load_workbook('x')\n"
        "import importlib\n"
        "importlib.import_module('lxml.etree')\n",
        encoding="utf-8",
    )
    found = _xml_reader_uses(planted)
    assert "import lxml.etree" in found
    assert "from lxml import ..." in found
    assert "from openpyxl import load_workbook" in found
    assert ".load_workbook" in found
    assert "import_module('lxml.etree')" in found
