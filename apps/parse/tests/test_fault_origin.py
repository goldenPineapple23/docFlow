"""
Whose fault an exception is, in the parse job (D-203, PR 1b-i).

The rule (founder, 2026-10-16, option (b)): a library's own exception types
are the file's wherever they are raised; the generic types are the file's
only when the library's own frame raised them; anything else -- and a
generic type raised in our code -- is ours, and the job answers `error`.

Each narrowed block is checked three ways, by replacing its library call
with one that raises: a generic type from a library frame keeps the block's
catalog code; the same type from a frame of ours, and a type no parser
raises, answer `error` with our code location and never the message.

"Ours" is a path: the synthetic raisers here are compiled with a filename
inside the parse service's package directory (ours) or a temporary one
(the library's). Nothing is written to disk.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from parse_service import fault_origin
from parse_service.job import _parse
from parse_service.launcher import _classify
from tests.conftest import FIXTURES
from tests.fixture_builders import build_msg, build_ods, build_tiff, build_xls

SECRET = "a value from the file"
OURS_FILE = str(fault_origin.OWN_DIRS[0] / "_synthetic_ours.py")
LIBRARY_FILE = str(Path(tempfile.gettempdir()) / "_synthetic_library.py")


def _raiser(exc_type: type[BaseException], where: str):
    """A function that raises `exc_type`, whose frame is ours or the library's."""
    filename = OURS_FILE if where == "ours" else LIBRARY_FILE
    module = "parse_service._synthetic" if where == "ours" else "somelibrary._synthetic"
    source = f"def boom(*args, **kwargs):\n    raise exc_type({SECRET!r})\n"
    namespace: dict = {"__name__": module, "exc_type": exc_type}
    exec(compile(source, filename, "exec"), namespace)
    return namespace["boom"]


def _caught(fn) -> BaseException:
    try:
        fn()
    except BaseException as exc:  # noqa: BLE001 -- the test's own capture
        return exc
    raise AssertionError("did not raise")


# ── the rule itself ──────────────────────────────────────────────────────────


def test_a_librarys_own_type_is_the_files_wherever_it_is_raised():
    class LibraryError(Exception):
        pass

    for where in ("ours", "library"):
        assert fault_origin.from_input(_caught(_raiser(LibraryError, where)), (LibraryError,))


def test_a_generic_type_is_the_files_only_from_the_librarys_frame():
    for exc_type in fault_origin.GENERIC:
        assert fault_origin.from_input(_caught(_raiser(exc_type, "library")), ())
        assert not fault_origin.from_input(_caught(_raiser(exc_type, "ours")), ())


def test_an_exception_with_no_frame_is_ours():
    assert not fault_origin.from_input(TypeError("never raised"), ())


def test_a_type_no_parser_raises_is_never_the_files():
    assert not fault_origin.from_input(_caught(_raiser(RuntimeError, "library")), ())


def test_a_block_never_catches_everything():
    caught = fault_origin.catchable((KeyError,))
    assert Exception not in caught and BaseException not in caught
    assert MemoryError not in caught and not any(issubclass(MemoryError, t) for t in caught)


def test_ours_is_a_path_in_our_packages_not_outside_site_packages():
    assert fault_origin.is_ours(OURS_FILE)
    assert not fault_origin.is_ours(LIBRARY_FILE)
    import zipfile

    assert not fault_origin.is_ours(zipfile.__file__)  # the standard library is the library's


def test_describe_names_the_type_and_our_innermost_frame_never_the_message():
    described = fault_origin.describe(_caught(_raiser(TypeError, "ours")))
    assert described == {"error_type": "TypeError", "location": "parse_service._synthetic:boom:2"}
    assert SECRET not in json.dumps(described)


def test_the_release_is_the_tag_of_flys_image(monkeypatch):
    monkeypatch.setenv("FLY_IMAGE_REF", "registry.fly.io/docflow-parse-staging:deployment-01ABC")
    assert fault_origin.release() == "deployment-01ABC"
    monkeypatch.delenv("FLY_IMAGE_REF")
    assert fault_origin.release() == "unversioned"


# ── the supervisor reads an `error` answer strictly ──────────────────────────


def _classified(answer: dict):
    return _classify(0, None, json.dumps(answer).encode())


def test_the_supervisor_passes_a_well_formed_error_answer():
    answer = {"outcome": "error", "error_type": "TypeError", "location": "parse_service.job:_parse:7"}
    result = _classified(answer)
    assert (result.outcome, result.answer) == ("error", answer)


@pytest.mark.parametrize(
    "answer",
    [
        {"outcome": "error", "error_type": "TypeError"},
        {"outcome": "error", "error_type": "TypeError", "location": "parse_service.job:_parse:7", "message": SECRET},
        {"outcome": "error", "error_type": "Type Error!", "location": "parse_service.job:_parse:7"},
        {"outcome": "error", "error_type": "TypeError", "location": "a value from the file"},
        {"outcome": "error", "error_type": 7, "location": "parse_service.job:_parse:7"},
    ],
)
def test_the_supervisor_refuses_a_malformed_error_answer_as_unreadable(answer):
    result = _classified(answer)
    assert (result.outcome, result.cause) == ("crashed", "invalid_output")


# ── each narrowed block ──────────────────────────────────────────────────────


def _blocks():
    """(name, job kind, extension, content, module, attribute, catalog code)."""
    return [
        ("_open_image", "document", ".tif", build_tiff(), "PIL.Image", "open", "rejected DOC-017"),
        ("convert_xls", "document", ".xls", build_xls(), "xlrd", "open_workbook", "rejected DOC-017"),
        ("convert_odf 2", "document", ".ods", build_ods(), "defusedxml.ElementTree", "fromstring", "rejected DOC-017"),
        ("unwrap_msg", "document", ".msg", build_msg(), "olefile", "OleFileIO", "rejected DOC-017"),
        (
            "unwrap_eml",
            "document",
            ".eml",
            (FIXTURES / "positive" / "po.eml").read_bytes(),
            "email",
            "message_from_bytes",
            "rejected DOC-017",
        ),
        (
            "documents.parse",
            "document",
            ".pdf",
            (FIXTURES / "positive" / "po.pdf").read_bytes(),
            "pdfplumber",
            "open",
            "rejected DOC-005",
        ),
        (
            "tables.parse_table",
            "table",
            ".xlsx",
            (FIXTURES / "tables" / "catalog.xlsx").read_bytes(),
            "openpyxl",
            "load_workbook",
            "rejected IMP-004",
        ),
    ]


def _answer_with(monkeypatch, block, raiser) -> dict:
    _, kind, extension, content, module, attribute, _ = block
    import importlib

    monkeypatch.setattr(importlib.import_module(module), attribute, raiser)
    return _parse(kind, content, f"upload{extension}")


def _short(answer: dict) -> str:
    return f"{answer['outcome']} {answer.get('code')}"


@pytest.mark.parametrize("block", _blocks(), ids=lambda b: b[0])
def test_a_generic_type_from_the_library_keeps_the_blocks_code(monkeypatch, block):
    answer = _answer_with(monkeypatch, block, _raiser(TypeError, "library"))
    assert _short(answer) == block[6]


@pytest.mark.parametrize("block", _blocks(), ids=lambda b: b[0])
def test_a_generic_type_from_our_code_is_an_error_not_the_files(monkeypatch, block):
    answer = _answer_with(monkeypatch, block, _raiser(TypeError, "ours"))
    assert answer == {"outcome": "error", "error_type": "TypeError", "location": "parse_service._synthetic:boom:2"}


@pytest.mark.parametrize("block", _blocks(), ids=lambda b: b[0])
def test_a_type_no_parser_raises_is_an_error_at_our_calling_line(monkeypatch, block):
    answer = _answer_with(monkeypatch, block, _raiser(RuntimeError, "library"))
    assert answer["outcome"] == "error" and answer["error_type"] == "RuntimeError"
    assert answer["location"].startswith("parse_service.parsing.")
    assert SECRET not in json.dumps(answer)


def test_the_odf_zip_read_is_narrowed_too(monkeypatch):
    """`convert_odf`'s first block: reading `content.xml` (validation reads
    the archive another way, so only this read is replaced)."""
    import zipfile

    real_read = zipfile.ZipFile.read

    def read(self, name, *args, **kwargs):
        if name == "content.xml":
            raise RuntimeError(SECRET)
        return real_read(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "read", read)
    answer = _parse("document", build_ods(), "upload.ods")
    assert answer["outcome"] == "error"
    assert answer["location"].startswith("parse_service.parsing.conversion:convert_odf:")


def test_a_file_type_no_reader_handles_is_ours(monkeypatch):
    """UnhandledFileTypeError: the allowlist took a type no builder covers,
    which is our gap, not the file -- DOC-030, never DOC-005."""
    from parse_service.parsing import documents

    monkeypatch.setattr(documents, "artifact_parts", _raiser(documents.UnhandledFileTypeError, "ours"))
    answer = _parse("document", (FIXTURES / "positive" / "po.txt").read_bytes(), "upload.txt")
    assert (answer["outcome"], answer["error_type"]) == ("error", "UnhandledFileTypeError")


def test_memory_error_still_reaches_the_job_as_a_limit(monkeypatch):
    block = _blocks()[1]
    with pytest.raises(MemoryError):
        _answer_with(monkeypatch, block, _raiser(MemoryError, "library"))
