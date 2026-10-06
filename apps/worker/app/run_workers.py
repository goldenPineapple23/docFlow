"""
The worker machine's one command (Stage 3d; founder's condition on the extra
process, 2026-10-01). It starts two Celery workers and keeps them together:

  documents -- DISPATCH_IN_FLIGHT_TARGET processes, reading `interactive` and
               `bulk`: every document task, exports, imports and the sweeps.
  dispatch  -- ONE process, reading only the `dispatch` queue: the dispatch
               pass, so a long order can never hold up the heartbeat (gap 1).
               It never claims a document (parse_and_extract hands one that
               arrives here back to `interactive`).

If either worker exits, the other is stopped and this exits non-zero, so Fly
restarts the machine (its default `on-failure` policy, set explicitly in
fly.toml): one never runs without the other. A stop signal (Fly sends SIGINT)
is passed to both, and this exits 0 once both have ended.

Before either starts (Stage 3e): it refuses to start holding another
service's database URL or connecting as the wrong login, and it records the
start, raising `worker_restarting` at 3 starts in an hour
(docflow_core.worker_starts).

    python -m app.run_workers
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence

from docflow_core import db, worker_starts
from docflow_core.config import get_settings
from docflow_core.constants import DISPATCH_QUEUE

# How long a worker gets to finish after the other has died, before it is killed.
PARTNER_STOP_SECONDS = 30
POLL_SECONDS = 0.5


def commands(app: str = "app.celery_app") -> dict[str, list[str]]:
    """The two workers' command lines. Their node names differ, so Celery
    sees two workers, not one started twice."""
    celery = [sys.executable, "-m", "celery", "-A", app, "worker", "--loglevel=info"]
    return {
        "documents": [
            *celery,
            "-Q", "interactive,bulk",
            f"--concurrency={get_settings().dispatch_in_flight_target}",
            "-n", "documents@%h",
        ],
        "dispatch": [*celery, "-Q", DISPATCH_QUEUE, "--concurrency=1", "-n", "dispatch@%h"],
    }


def supervise(cmds: Mapping[str, Sequence[str]]) -> int:
    """Run every command; return when they have all ended. 0 if a stop signal
    ended them, 1 if one of them ended by itself."""
    children: dict[str, subprocess.Popen] = {}
    stopping = False

    def stop(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        for child in children.values():
            if child.poll() is None:
                child.send_signal(signum)

    # Before the first child starts, so no stop signal can be missed.
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    for name, cmd in cmds.items():
        children[name] = subprocess.Popen(list(cmd))
        if stopping:  # a stop arrived while starting: this one never saw it
            children[name].send_signal(signal.SIGTERM)

    while all(child.poll() is None for child in children.values()):
        time.sleep(POLL_SECONDS)

    if not stopping:
        ended = [name for name, child in children.items() if child.poll() is not None]
        print(f"run_workers: {', '.join(ended)} exited; stopping the other", file=sys.stderr, flush=True)
        for child in children.values():
            if child.poll() is None:
                child.send_signal(signal.SIGTERM)
    deadline = time.monotonic() + PARTNER_STOP_SECONDS
    for child in children.values():
        try:
            child.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    return 0 if stopping else 1


def foreign_login_refusal() -> str | None:
    """Why the worker must not start, or None (Stage 3e, A4). The worker holds
    only its own login; the admin and Stripe logins live on the API, so either
    URL here means a secret was put on the wrong app."""
    settings = get_settings()
    held = [
        name
        for name, value in (
            ("ADMIN_DATABASE_URL", settings.admin_database_url),
            ("STRIPE_DATABASE_URL", settings.stripe_database_url),
            ("API_DATABASE_URL", settings.api_database_url),
        )
        if value
    ]
    if held:
        return (
            f"{', '.join(held)} set on the worker. The worker holds only its own login "
            "(docflow_worker, in DATABASE_URL); remove the others from this app (RUNBOOK 10)."
        )
    return None


def main() -> int:
    """The worker machine's start: refuse a wrong configuration, record the
    start (3e, part B), then run the two workers."""
    refusal = foreign_login_refusal()
    if refusal is not None:
        print(f"run_workers refused to start: {refusal}", file=sys.stderr, flush=True)
        return 1
    db.use_own_login("worker")
    try:
        checked = db.verify_logins(("worker",))
    except db.WrongLoginError as exc:
        print(f"run_workers refused to start: {exc}", file=sys.stderr, flush=True)
        return 1
    except Exception as exc:  # noqa: BLE001 -- unreachable, not wrong: start anyway
        print(
            f"run_workers: database unreachable at start ({type(exc).__name__})", file=sys.stderr, flush=True
        )
    else:
        # The role only, never the URL. The first deploy's check (RUNBOOK 9.7,
        # step 7) reads this line; before D-194 a pass printed nothing.
        for login in checked:
            print(f"run_workers: login check passed as docflow_{login}", file=sys.stderr, flush=True)
    worker_starts.record_start()
    return supervise(commands())


if __name__ == "__main__":
    sys.exit(main())
