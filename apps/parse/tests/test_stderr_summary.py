"""
No job's stderr text in any log line (CLAUDE.md 7.10; D-204 item 4a, PR 1b-i).

A job reads a hostile file, and whatever a library prints lands on its
stderr (since the answer channel). The service logs a rejected job's stderr
and an isolation failure's as length, a short hash and a known category,
never as text; a crashed job's tail is collected, never logged.

The two service tests run the real supervisor (its HTTP handler, in this
process) and a real job process whose stderr carries a fake document line,
and read every log record the service wrote.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from parse_service import config, launcher, stderr_summary
from parse_service import server as srv
from tests import conftest

PARSE_ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token-for-the-in-process-service-only"
DOCUMENT_LINE = b"PO-12345 HEX BOLT M8"
XLRD_WARNING = b"WARNING *** file size (5631) not 512 + multiple of sector size (512)"
SETTINGS = config.Settings(production=False, isolation=False, token=TOKEN, port=0, libreoffice_path="", test_only=())


# ── The summary ────────────────────────────────────────────────────────────


def test_the_summary_is_length_hash_and_category_never_text():
    raw = XLRD_WARNING + b"\n" + DOCUMENT_LINE + b"\n"
    summary = stderr_summary.summarize(raw)
    assert summary == {
        "bytes": len(raw),
        "truncated": False,
        "sha256": hashlib.sha256(raw).hexdigest()[:12],
        "category": "xlrd_sector_size",
    }
    line = stderr_summary.for_log(summary)
    assert DOCUMENT_LINE.decode() not in line and "WARNING" not in line
    assert line == f"stderr_bytes={len(raw)} stderr_sha256={summary['sha256']} stderr_category=xlrd_sector_size"


def test_the_same_stderr_gives_the_same_hash_and_another_does_not():
    assert stderr_summary.summarize(DOCUMENT_LINE)["sha256"] == stderr_summary.summarize(DOCUMENT_LINE)["sha256"]
    assert stderr_summary.summarize(DOCUMENT_LINE)["sha256"] != stderr_summary.summarize(DOCUMENT_LINE + b"!")["sha256"]


def test_stderr_matching_no_known_pattern_has_no_category():
    assert stderr_summary.summarize(DOCUMENT_LINE)["category"] == "none"
    assert stderr_summary.summarize(b"")["category"] == "none"


def test_a_truncated_stderr_says_so_in_the_log():
    assert stderr_summary.for_log(stderr_summary.summarize(b"x" * 16384, truncated=True)).startswith(
        "stderr_bytes=16384+ "
    )


# ── The service's log, with a real job ────────────────────────────────────


def _job_script(stderr: bytes, answer: dict) -> str:
    """The real job's main(), its parse replaced by one that writes `stderr` and answers."""
    return "\n".join(
        [
            "import os, sys",
            f"sys.path.insert(0, {str(PARSE_ROOT)!r})",
            "from parse_service import job",
            "def noisy(kind, content, filename):",
            f"    os.write(2, {stderr!r})",
            f"    return {answer!r}",
            "job._parse = noisy",
            "sys.exit(job.main(['job', 'document']))",
        ]
    )


@pytest.fixture
def service_running(monkeypatch):
    """A dev-mode service in this process; each job runs `script` in its own process."""
    real_popen = subprocess.Popen
    script: dict[str, str] = {}

    def the_job(_argv, **kwargs):
        return real_popen([sys.executable, "-c", script["text"]], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", the_job)
    handler = type("H", (srv.Handler,), {"service": srv.Service(SETTINGS, None)})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    saved = conftest.SERVICE_URL
    conftest.SERVICE_URL = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield script
    finally:
        conftest.SERVICE_URL = saved
        httpd.shutdown()


def _no_stderr_text_in(caplog, capfd) -> str:
    """Every log record the service wrote, as text, checked for the document line."""
    logged = "\n".join(record.getMessage() for record in caplog.records)
    out, err = capfd.readouterr()
    for text in (logged, caplog.text, out, err):
        assert DOCUMENT_LINE.decode() not in text
        assert "WARNING ***" not in text
    return logged


def test_a_rejected_jobs_stderr_is_logged_as_length_hash_and_category(service_running, caplog, capfd):
    stderr = XLRD_WARNING + b"\n" + DOCUMENT_LINE + b"\n"
    service_running["text"] = _job_script(stderr, {"outcome": "rejected", "code": "DOC-017"})
    with caplog.at_level(logging.DEBUG, logger="parse_service"):
        status, body = conftest.post("document", b"not really a document", ".xls", token=TOKEN)
    assert (status, body) == (200, {"outcome": "rejected", "code": "DOC-017"})
    logged = _no_stderr_text_in(caplog, capfd)
    expected = (
        f"job_rejected kind=document code=DOC-017 stderr_bytes={len(stderr)} "
        f"stderr_sha256={hashlib.sha256(stderr).hexdigest()[:12]} stderr_category=xlrd_sector_size"
    )
    assert expected in logged, logged


def test_an_isolation_failures_evidence_carries_the_summary_not_the_text(service_running, caplog, capfd, monkeypatch):
    """The job really runs and writes to stderr; its end is classified as an
    isolation failure (a sandbox this test cannot have), so the service logs
    its evidence the way it would on Fly."""
    stderr = b"job hardening failed: " + DOCUMENT_LINE + b"\n"
    service_running["text"] = _job_script(stderr, {"outcome": "ok"})
    monkeypatch.setattr(
        launcher, "_classify", lambda *_a, **_k: launcher.JobResult("isolation_failed", cause="not_hardened")
    )
    with caplog.at_level(logging.DEBUG, logger="parse_service"):
        status, body = conftest.post("document", b"not really a document", ".pdf", token=TOKEN)
    assert (status, body) == (503, {"error": "isolation_failed"})
    logged = _no_stderr_text_in(caplog, capfd)
    record = next(r for r in caplog.records if r.getMessage().startswith("isolation_failed "))
    assert record.args[1]["stderr"] == {
        "bytes": len(stderr),
        "truncated": False,
        "sha256": hashlib.sha256(stderr).hexdigest()[:12],
        "category": "none",
    }
    assert "stderr_tail" not in record.args[1]
    assert "'sha256'" in logged


def test_a_crashed_jobs_tail_is_collected_and_not_logged(service_running, caplog, capfd):
    stderr = DOCUMENT_LINE + b"\n"
    service_running["text"] = _job_script(stderr, {"outcome": "not an outcome"})
    with caplog.at_level(logging.DEBUG, logger="parse_service"):
        status, body = conftest.post("document", b"not really a document", ".pdf", token=TOKEN)
    assert status == 200 and (body["outcome"], body["cause"]) == ("crashed", "invalid_output")
    _no_stderr_text_in(caplog, capfd)
    result = launcher.run_job("document", {}, b"", slot=0, settings=SETTINGS, cgroups=None)
    assert result.outcome == "crashed" and DOCUMENT_LINE.decode() in result.evidence["stderr_tail"]
