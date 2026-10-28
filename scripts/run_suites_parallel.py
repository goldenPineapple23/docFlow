"""
The core, worker and API suites together, against one database (D-211;
RUNBOOK 1.4).

Each suite runs as it would alone (`pytest -q -rs --durations=25`, its log
in the folder given), with DOCFLOW_TEST_PARALLEL_RUN=1 so its run lock takes
the shared key in shared mode: the three may overlap, and nothing else may
start meanwhile. While they overlap, a test that touches the database's
global state holds `shared_state` (tests/suite_lock.py).

Each suite's own leftover count says "counted by the parallel runner"; this
script reads the same six counts before it starts the suites and after the
last one ends, and fails on anything but `+0`, the same rule over the
combined run. It also reads, read-only: the database's size before and after,
and its connections every few seconds during the run (the peak is printed).

It opens connections of its own (the counts and the samples, as
docflow_admin), so it applies the suites' database guard first: a database
off this machine needs DOCFLOW_STAGING_RUN=1, which a staging run under the
founder's grant sets and nothing else does. It blocks no outside service
itself: start it from the same environment a single suite's run would have.

A failed parallel run is read before anything is re-run (RUNBOOK 1.4).

    apps/api/.venv/Scripts/python scripts/run_suites_parallel.py <log folder>

Exit status: 0 when all three suites passed and every count reads +0.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# (suite, folder it runs in, its virtual environment). Core's is built as
# CI's core job builds it: the worker's lock and dev file (D-225).
SUITES = (
    ("core", "packages/core", "packages/core/.venv"),
    ("worker", "apps/worker", "apps/worker/.venv"),
    ("api", "apps/api", "apps/api/.venv"),
)
SAMPLE_SECONDS = 5


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The suites' own guard and their own six counts: one definition of each.
database_guard = _load("database_guard", REPO / "apps" / "api" / "tests" / "database_guard.py")
leftover_count = _load("leftover_count", REPO / "apps" / "api" / "tests" / "leftover_count.py")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _python(venv: str) -> str:
    for candidate in (REPO / venv / "Scripts" / "python.exe", REPO / venv / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    raise SystemExit(f"no Python in {venv}")


def read_counts() -> dict[str, int]:
    """The six counts, in a read-only transaction. Counts only, never a row."""
    from docflow_core.db import platform_session
    from sqlalchemy import text

    with platform_session() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        counts = {label: int(session.execute(text(query)).scalar_one()) for label, query in leftover_count.COUNTS}
        session.rollback()
    return counts


def read_size() -> int:
    from docflow_core.db import platform_session
    from sqlalchemy import text

    with platform_session() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        size = int(session.execute(text("SELECT pg_database_size(current_database())")).scalar_one())
        session.rollback()
    return size


def read_connections() -> tuple[int, dict[str, int]]:
    """Connections to this database now, in total and by login, as
    pg_stat_activity shows them to docflow_admin. Read-only."""
    from docflow_core.db import platform_session
    from sqlalchemy import text

    with platform_session() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        rows = session.execute(
            text(
                "SELECT coalesce(usename, '(not shown)'), count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() GROUP BY 1"
            )
        ).all()
        session.rollback()
    by_login = {str(name): int(n) for name, n in rows}
    return sum(by_login.values()), by_login


class Sampler(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.peak = 0
        self.peak_at = ""
        self.peak_by_login: dict[str, int] = {}
        self.samples = 0
        self.unread = 0

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                total, by_login = read_connections()
                self.samples += 1
                if total > self.peak:
                    self.peak, self.peak_at, self.peak_by_login = total, _now(), by_login
            except Exception:  # noqa: BLE001 -- a missed sample is counted, never fatal
                self.unread += 1
            self.stop.wait(SAMPLE_SECONDS)


def last_line(log: Path) -> str:
    lines = [line for line in log.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    return lines[-1] if lines else "(empty log)"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    logs = Path(argv[0]).resolve()
    if REPO in logs.parents or logs == REPO:
        print("the logs go outside the repository")
        return 2
    logs.mkdir(parents=True, exist_ok=True)

    database_guard.check()  # before this script opens any connection
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()

    before, size_before = read_counts(), read_size()
    print(f"head {head}; started {_now()}; database size before: {size_before} bytes")

    env = {**os.environ, "DOCFLOW_TEST_PARALLEL_RUN": "1"}
    sampler = Sampler()
    sampler.start()
    runs = {}
    for suite, folder, venv in SUITES:
        log = logs / f"{suite}.log"
        handle = log.open("w", encoding="utf-8")
        process = subprocess.Popen(
            [_python(venv), "-m", "pytest", "-q", "-rs", "--durations=25"],
            cwd=REPO / folder,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        runs[suite] = (process, handle, log, time.monotonic())
        print(f"{suite}: started {_now()} (pid {process.pid}), log {log}")

    results = {}
    for suite, (process, handle, log, started) in runs.items():
        code = process.wait()
        handle.close()
        results[suite] = (code, time.monotonic() - started, log)
    sampler.stop.set()
    sampler.join(timeout=SAMPLE_SECONDS * 3)

    after, size_after = read_counts(), read_size()
    print(f"ended {_now()}")
    for suite, (code, seconds, log) in results.items():
        print(f"{suite}: exit {code}, {seconds / 60:.1f} min: {last_line(log)}")
    print(f"database size after: {size_after} bytes ({size_after - size_before:+d})")
    by_login = ", ".join(f"{name} {n}" for name, n in sorted(sampler.peak_by_login.items()))
    print(
        f"connections: peak {sampler.peak} at {sampler.peak_at} ({by_login}); "
        f"{sampler.samples} samples every {SAMPLE_SECONDS} s, {sampler.unread} not read"
    )
    moved = [label for label, _query in leftover_count.COUNTS if after[label] != before[label]]
    parts = [f"{label} {before[label]} -> {after[label]} ({after[label] - before[label]:+d})"
             for label, _query in leftover_count.COUNTS]
    print("left behind by the three suites (before -> after): " + ", ".join(parts))
    failed = [suite for suite, (code, _s, _l) in results.items() if code != 0]
    if moved:
        print("FAILED: the run did not leave the database as it found it: " + ", ".join(moved))
    if failed:
        print("FAILED: " + ", ".join(failed) + " did not pass; read the log before re-running anything")
    return 1 if moved or failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
