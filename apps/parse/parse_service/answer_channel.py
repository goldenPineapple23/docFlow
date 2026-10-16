"""
The job's answer channel (D-204, PR 1b-i).

The supervisor reads exactly one JSON answer from the job's stdout. Anything
else written there corrupts it, and the job ends `crashed`
(`invalid_output`): xlrd, for one, prints a warning to stdout on a short
`.xls`, which the mutation corpus found in CI's real image. That bug was in
the job since 3c.

So the job keeps its real stdout for the answer alone (founder, 2026-10-16):
at its start, before anything else runs, the real stdout descriptor is
saved (`os.dup(1)`, not inherited by any child), descriptor 1 is pointed at
stderr (`os.dup2(2, 1)`), and `sys.stdout` follows. Whatever a library
prints -- with `print()`, from C code with `write(1, ...)`, or from a child
process such as LibreOffice that inherits descriptor 1 -- goes to stderr.
The answer is written only to the saved descriptor.

A module of its own because the job runs as `__main__`: the self-tests
import this one, not the job, to reach the channel (B7 floods it).
"""

from __future__ import annotations

import os
import sys

_answer_fd: int | None = None


def keep_stdout_for_the_answer() -> None:
    """Run once, first thing in the job. Not undone: the job ends after."""
    global _answer_fd
    if _answer_fd is not None:
        return
    sys.stdout.flush()
    _answer_fd = os.dup(1)  # non-inheritable (PEP 446): no child process gets it
    os.dup2(2, 1)
    sys.stdout = sys.stderr


def answer_fd() -> int:
    """Where the answer goes: the saved descriptor, or 1 before the switch."""
    return _answer_fd if _answer_fd is not None else 1


def write(data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(answer_fd(), view)
        view = view[written:]
