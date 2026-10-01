"""
Test F2 of the 3c test table: since Stage 3c the worker does not parse.

Every file a stranger sends is opened only by the parse service
(apps/parse), inside a sandboxed job. The worker holds the database login,
the Storage key and the model key, so a parser running in it would put all
three one exploit away from a hostile file. This test fails the build if
the worker's or core's product code imports a file-reading library (PDF,
image, Word, legacy Excel, LibreOffice's bindings) or the parse service's
own code, or if one reappears in the worker's or the API's requirements. It
reads import statements; it does not check what is installed.

The one exception is openpyxl, which writes `.xlsx` exports (and reads back
the bytes it wrote, for the round-trip check): allowed in
docflow_core/exports.py only. The API's own boundary test
(apps/api/tests/test_parsing_boundary.py) covers the web process.

lxml is installed (openpyxl writes the export through it) but is never handed
a file: the XML guard below fails on any direct lxml import or any
openpyxl.load_workbook call in apps/worker (founder, 2026-10-01).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PRODUCT_DIRS = [REPO / "apps" / "worker" / "app", REPO / "packages" / "core" / "docflow_core"]
XML_GUARD_DIR = REPO / "apps" / "worker" / "app"

FORBIDDEN = {
    "PIL",
    "docx",
    "pdfplumber",
    "pdfminer",
    "pypdfium2",
    "pillow_heif",
    "olefile",
    "xlrd",
    "xlwt",
    "defusedxml",
    "fitz",
    "pypdf",
    "parse_service",
    "seccomp",
    # LibreOffice's Python bindings.
    "uno",
    "unohelper",
}
FORBIDDEN_CORE = {"conversion", "catalog_parsing", "parsing"}
OPENPYXL_ALLOWED = {"packages/core/docflow_core/exports.py"}
REQUIREMENT_NAMES = re.compile(
    r"^(pdfplumber|pdfminer\.six|pypdfium2|python-docx|pillow|pillow[-_]heif|olefile|xlrd|xlwt|defusedxml)\b",
    re.IGNORECASE,
)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_no_parser_is_importable_from_worker_or_core_product_code():
    offenders = []
    files = [p for d in PRODUCT_DIRS for p in d.rglob("*.py")]
    assert len(files) > 50, "the scan is looking in the wrong place"
    for path in files:
        relative = path.relative_to(REPO).as_posix()
        for name in _imports(path):
            top = name.split(".")[0]
            if top in FORBIDDEN:
                offenders.append(f"{relative}: {name}")
            if top == "openpyxl" and relative not in OPENPYXL_ALLOWED:
                offenders.append(f"{relative}: {name}")
            if top == "docflow_core" and name.split(".")[-1] in FORBIDDEN_CORE:
                offenders.append(f"{relative}: {name}")
    assert offenders == []


def test_the_moved_modules_are_gone_from_worker_and_core():
    assert not (REPO / "apps" / "worker" / "app" / "conversion.py").exists()
    assert not (REPO / "packages" / "core" / "docflow_core" / "catalog_parsing.py").exists()


def test_no_parsing_library_is_in_the_worker_or_api_requirements():
    offenders = []
    for app in ("worker", "api"):
        for name in ("requirements.txt", "requirements.lock.txt"):
            for line in (REPO / "apps" / app / name).read_text(encoding="utf-8").splitlines():
                if REQUIREMENT_NAMES.match(line.strip()):
                    offenders.append(f"apps/{app}/{name}: {line.strip()}")
    assert offenders == []


def test_the_check_would_catch_a_parser_import(tmp_path):
    """The scan itself works: a planted import is found."""
    planted = tmp_path / "planted.py"
    planted.write_text("from PIL import Image\nimport docflow_core.catalog_parsing\n", encoding="utf-8")
    names = _imports(planted)
    assert "PIL" in names and "docflow_core.catalog_parsing" in names


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
