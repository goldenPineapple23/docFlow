"""
Whose fault an exception is, inside the parse job (Stage 4, D-203, PR 1b-i).

A parser that meets a malformed file raises; that is the file's problem and
keeps its catalog code (DOC-005, DOC-017, IMP-004). Anything else that
reaches a parsing block is ours: the job answers `error` and the document
fails with DOC-030, which never blames the customer's file.

How a block tells them apart (founder, 2026-10-16, option (b)):
- a library's own exception types (a block's `specific` tuple) count as the
  input's wherever they are raised;
- the generic types below count as the input's only when the innermost
  frame that raised them is the library's, not ours. The mutation corpus
  saw pillow_heif, PIL, pdfplumber and xlrd raise these on broken files,
  each from the library's own frame. Raised in our code, they are a bug.

"Ours" is decided by the file's path: the parse service's and docflow_core's
package directories. Not "outside site-packages": the standard library is
outside it too, and a `zipfile` frame is the library's.

No SQLAlchemy in the parse image, so this does not use `docflow_core.faults`;
`describe` gives the same `module:function:line` as its namesake there.
"""

from __future__ import annotations

import os
import re
import struct
import traceback
from pathlib import Path

import docflow_core

import parse_service

OWN_DIRS = (Path(parse_service.__file__).resolve().parent, Path(docflow_core.__file__).resolve().parent)

# Generic types a parser raises on a broken file: those the corpus saw
# (TypeError, ValueError, OSError, AssertionError, struct.error) and three
# more a reader of truncated data raises (EOFError, IndexError, KeyError).
GENERIC: tuple[type[BaseException], ...] = (
    TypeError,
    ValueError,
    OSError,
    AssertionError,
    struct.error,
    EOFError,
    IndexError,
    KeyError,
)

UNVERSIONED = "unversioned"

# What an `error` answer may carry; the supervisor refuses anything else as
# an unreadable answer (the worker's parse_client checks the same).
ERROR_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,99}$")
LOCATION = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,149}:[A-Za-z_<>][A-Za-z0-9_.<>]{0,149}:\d{1,6}$")


def is_error_answer(answer: dict) -> bool:
    return (
        set(answer) == {"outcome", "error_type", "location"}
        and isinstance(answer["error_type"], str)
        and bool(ERROR_TYPE.match(answer["error_type"]))
        and isinstance(answer["location"], str)
        and bool(LOCATION.match(answer["location"]))
    )


def is_ours(filename: str) -> bool:
    try:
        path = Path(filename).resolve()
    except (OSError, ValueError):
        return False
    return any(path.is_relative_to(own) for own in OWN_DIRS)


def raised_in_library(exc: BaseException) -> bool:
    """True when the innermost frame of the traceback is not in our code.
    An exception with no frame at all is treated as ours."""
    frames = traceback.extract_tb(exc.__traceback__)
    return bool(frames) and not is_ours(frames[-1].filename)


def catchable(specific: tuple[type[BaseException], ...]) -> tuple[type[BaseException], ...]:
    """What a parsing block catches: its library's own types and the generic
    ones. Never `Exception`: anything else passes through to the job, which
    answers `error`."""
    return (*specific, *GENERIC)


def from_input(exc: BaseException, specific: tuple[type[BaseException], ...]) -> bool:
    if isinstance(exc, specific):
        return True
    return isinstance(exc, GENERIC) and raised_in_library(exc)


def describe(exc: BaseException) -> dict:
    """The exception's type and the innermost frame of ours it passed
    through: `module:function:line`. Never its message."""
    module, function, line = "unknown", "unknown", 0
    tb = exc.__traceback__
    while tb is not None:
        if is_ours(tb.tb_frame.f_code.co_filename):
            module = str(tb.tb_frame.f_globals.get("__name__") or "unknown")
            function, line = tb.tb_frame.f_code.co_qualname, tb.tb_lineno
        tb = tb.tb_next
    return {"error_type": type(exc).__name__, "location": f"{module}:{function}:{line}"}


def release() -> str:
    """The parse app's release: the tag of the image Fly started, as
    `docflow_core.faults.release` reads it. Read by the supervisor, which
    has Fly's environment; a job's sandbox does not."""
    ref = os.environ.get("FLY_IMAGE_REF") or ""
    tag = ref.rsplit(":", 1)[-1].rsplit("/", 1)[-1]
    return tag or UNVERSIONED
