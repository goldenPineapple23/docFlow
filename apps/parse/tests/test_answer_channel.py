"""
The job's stdout carries its answer and nothing else (D-204, PR 1b-i).

Found by the mutation corpus in CI's real image: xlrd prints a warning to
stdout on a short `.xls`, which corrupted the job's JSON answer, so the job
ended `crashed` (`invalid_output`) -- a bug present since 3c. The job now
saves its real stdout for the answer and points descriptor 1 at stderr
before anything else runs (`answer_channel`).

Each test runs the real job (`parse_service.job.main`) in its own process,
isolation off, with a parser that writes to stdout one of three ways: with
print(), with write(1, ...) on the descriptor, and from a child process
that inherits it. The answer must come out alone and valid; the noise goes
to stderr. In the real sandbox the corpus's two `.xls` cut95 cases prove
the same through the image (CI's HTTP step).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PARSE_ROOT = Path(__file__).resolve().parents[1]
ANSWER = {"outcome": "rejected", "code": "DOC-005"}

NOISES = {
    "print": "print('NOISE-FROM-PRINT', flush=True)",
    "descriptor": "os.write(1, b'NOISE-FROM-FD-1')",
    "child": (
        "subprocess.run([sys.executable, '-c', \"import os; os.write(1, b'NOISE-FROM-CHILD')\"], check=True)"
    ),
}


def _run_job_with(noise: str) -> subprocess.CompletedProcess:
    script = "\n".join(
        [
            "import os, subprocess, sys",
            "from parse_service.parsing import documents",
            "def noisy(content, filename):",
            f"    {noise}",
            f"    return {ANSWER!r}",
            "documents.parse = noisy",
            "from parse_service import job",
            "sys.exit(job.main(['job', 'document']))",
        ]
    )
    stdin = json.dumps({"filename": "upload.pdf"}).encode() + b"\n" + b"%PDF-1.4 not really"
    return subprocess.run(
        [sys.executable, "-c", script],
        input=stdin,
        capture_output=True,
        cwd=PARSE_ROOT,
        env={**os.environ, "PARSE_ISOLATION": "off"},
        timeout=60,
    )


@pytest.mark.parametrize("how", sorted(NOISES))
def test_whatever_a_parser_writes_to_stdout_never_reaches_the_answer(how):
    done = _run_job_with(NOISES[how])
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    assert json.loads(done.stdout) == ANSWER, done.stdout
    assert b"NOISE-FROM" not in done.stdout
    assert b"NOISE-FROM" in done.stderr, "the noise went to stderr, not nowhere"
