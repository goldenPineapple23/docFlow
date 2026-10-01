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
openpyxl.load_workbook call in apps/worker or packages/core, except the one
named read-back in exports.parse_xlsx (founder, 2026-10-01, Q11).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PRODUCT_DIRS = [REPO / "apps" / "worker" / "app", REPO / "packages" / "core" / "docflow_core"]
XML_GUARD_DIRS = PRODUCT_DIRS  # the worker and core (founder, Q11)

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
# in the worker's or core's product code may import lxml directly, or read a
# workbook with openpyxl, with one exception named by file AND function.
#
# The exception: exports.parse_xlsx reads back the .xlsx that build_export
# rendered a moment before in the same process -- Section 7.4's round-trip
# check (EXP-004). Exactly these uses are allowed there, so a second
# load_workbook call, here or anywhere else, fails the build.
# test_parse_xlsx_reads_only_the_bytes_just_rendered (below) and
# packages/core/tests/test_exports.py check what it is handed.
XML_READ_BACK_EXCEPTION = {
    ("packages/core/docflow_core/exports.py", "parse_xlsx"): [
        "from openpyxl import load_workbook",
        "load_workbook(...)",
    ],
}


def _xml_reader_uses(path: Path) -> list[tuple[str, str]]:
    """Every lxml import and workbook read in a file, as (function, use);
    the function is the innermost enclosing def ("<module>" at top level)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, str]] = []

    def visit(node: ast.AST, where: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            where = node.name
        if isinstance(node, ast.Import):
            found.extend((where, f"import {a.name}") for a in node.names if a.name.split(".")[0] == "lxml")
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top == "lxml":
                found.append((where, f"from {node.module} import ..."))
            if top == "openpyxl" and any(a.name == "load_workbook" for a in node.names):
                found.append((where, f"from {node.module} import load_workbook"))
        elif isinstance(node, ast.Attribute) and node.attr == "load_workbook":
            found.append((where, ".load_workbook"))
        elif isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == "load_workbook":
                found.append((where, "load_workbook(...)"))
            if name in ("import_module", "__import__") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    if first.value.split(".")[0] == "lxml":
                        found.append((where, f"{name}({first.value!r})"))
        for child in ast.iter_child_nodes(node):
            visit(child, where)

    visit(tree, "<module>")
    return found


def test_no_direct_lxml_import_and_no_workbook_reading():
    offenders: dict[str, list[tuple[str, str]]] = {}
    allowed_seen: dict[tuple[str, str], list[str]] = {key: [] for key in XML_READ_BACK_EXCEPTION}
    files = [p for d in XML_GUARD_DIRS for p in d.rglob("*.py")]
    assert len(files) > 50, "the scan is looking in the wrong place"
    for path in files:
        relative = path.relative_to(REPO).as_posix()
        for where, use in _xml_reader_uses(path):
            if (relative, where) in allowed_seen:
                allowed_seen[(relative, where)].append(use)
            else:
                offenders.setdefault(relative, []).append((where, use))
    assert offenders == {}, (
        "lxml is here only for openpyxl's .xlsx writer; nothing may import it "
        f"or read a workbook: {offenders}"
    )
    # The exception holds exactly its uses: one more (or one fewer) fails.
    assert allowed_seen == XML_READ_BACK_EXCEPTION


def test_parse_xlsx_reads_only_the_bytes_just_rendered():
    """The exception's input, checked in the code: parse_xlsx hands
    load_workbook an in-memory copy of its own `content` argument (never a
    path or a file object), and the only call to it is build_export's
    PARSERS[fmt](content), where `content` is what render() returned in the
    same function. Nothing else in the worker or core calls it."""
    exports_py = REPO / "packages" / "core" / "docflow_core" / "exports.py"
    tree = ast.parse(exports_py.read_text(encoding="utf-8"))
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    parse_xlsx = functions["parse_xlsx"]
    assert [a.arg for a in parse_xlsx.args.args] == ["content"]
    calls = [
        n
        for n in ast.walk(parse_xlsx)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "load_workbook"
    ]
    assert len(calls) == 1
    first = calls[0].args[0]
    assert ast.unparse(first) == "io.BytesIO(content)", ast.unparse(first)

    build_export = functions["build_export"]
    assigned = [
        ast.unparse(n.value)
        for n in ast.walk(build_export)
        if isinstance(n, ast.Assign) and [ast.unparse(t) for t in n.targets] == ["content"]
    ]
    assert assigned == ["render(snapshot, snapshot_hash, fmt)"], assigned
    parser_calls = [
        ast.unparse(n)
        for n in ast.walk(build_export)
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "PARSERS[fmt]"
    ]
    assert parser_calls == ["PARSERS[fmt](content)"], parser_calls

    # parse_xlsx and PARSERS are used nowhere but exports.py itself.
    elsewhere = []
    for path in (p for d in PRODUCT_DIRS for p in d.rglob("*.py")):
        if path == exports_py:
            continue
        source = path.read_text(encoding="utf-8")
        if "parse_xlsx" in source or "PARSERS" in source:
            elsewhere.append(path.relative_to(REPO).as_posix())
    assert elsewhere == []
    # Inside exports.py: defined once, listed in PARSERS, called nowhere directly.
    names = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "parse_xlsx"]
    assert len(names) == 1  # the PARSERS entry


def test_the_xml_guard_would_catch_each_form(tmp_path):
    """A guard that cannot fail is not a guard: each planted form is found,
    with the function it sits in."""
    planted = tmp_path / "planted.py"
    planted.write_text(
        "import lxml.etree\n"
        "from lxml import html\n"
        "import openpyxl\n"
        "def parse_xlsx(content):\n"
        "    from openpyxl import load_workbook as lw\n"
        "    openpyxl.load_workbook('x')\n"
        "import importlib\n"
        "importlib.import_module('lxml.etree')\n",
        encoding="utf-8",
    )
    found = _xml_reader_uses(planted)
    assert ("<module>", "import lxml.etree") in found
    assert ("<module>", "from lxml import ...") in found
    assert ("parse_xlsx", "from openpyxl import load_workbook") in found
    assert ("parse_xlsx", ".load_workbook") in found
    assert ("parse_xlsx", "load_workbook(...)") in found
    assert ("<module>", "import_module('lxml.etree')") in found
