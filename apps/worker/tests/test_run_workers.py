"""
app.run_workers keeps the document worker and the dispatch worker together
(founder's condition on the extra process, 2026-10-01). Real processes and
real signals; the children are small Python programs standing in for the two
Celery workers, so what is proven is the supervision itself. The Celery
command lines are checked in test_celery_app.py, and the dispatch worker's
real behaviour in test_dispatch_real_worker.py.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

WORKER_ROOT = Path(__file__).resolve().parents[1]

SHORT = [sys.executable, "-c", "import time; time.sleep(1)"]
LONG = [sys.executable, "-c", "import time; time.sleep(120)"]


def _supervisor(children: dict[str, list[str]]) -> subprocess.Popen:
    program = (
        "import sys\n"
        "from app.run_workers import supervise\n"
        f"sys.exit(supervise({children!r}))\n"
    )
    return subprocess.Popen([sys.executable, "-c", program], cwd=WORKER_ROOT)


def test_when_one_worker_exits_the_other_is_stopped_and_the_launcher_fails():
    """So Fly restarts the machine (`on-failure`) instead of leaving the
    document worker running with no dispatch process, or the other way round."""
    started = time.monotonic()
    supervisor = _supervisor({"dispatch": SHORT, "documents": LONG})
    code = supervisor.wait(timeout=60)
    took = time.monotonic() - started
    assert code == 1
    # The partner would have slept 120 s: it was stopped, not waited for.
    assert took < 45, took


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals; Fly sends SIGINT to stop a machine")
def test_a_stop_signal_reaches_both_workers_and_the_launcher_exits_cleanly():
    supervisor = _supervisor({"dispatch": LONG, "documents": LONG})
    time.sleep(2)  # both children started
    assert supervisor.poll() is None
    started = time.monotonic()
    supervisor.send_signal(signal.SIGINT)
    code = supervisor.wait(timeout=60)
    assert code == 0  # a requested stop: Fly does not restart it
    assert time.monotonic() - started < 10  # both ended on the signal, not on the 30 s kill
