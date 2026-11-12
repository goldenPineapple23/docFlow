"""
The worker machine's one command (Stage 3d; founder's condition on the extra
process, 2026-10-01). It starts three Celery workers and keeps them together:

  documents -- DISPATCH_IN_FLIGHT_TARGET processes, reading `interactive` and
               `bulk`: every document task, exports, imports and the sweeps.
  dispatch  -- ONE process, reading only the `dispatch` queue: the dispatch
               pass, so a long order can never hold up the heartbeat (gap 1).
               It never claims a document (parse_and_extract hands one that
               arrives here back to `interactive`).
  pages     -- ONE process, reading only the `pages` queue: a document's page
               images (viewer A3; founder, 2026-11-03; D-232), so a long
               render never takes a document's slot, and the parse service's
               second slot (PARSE_SLOTS = 2) is the most it ever uses.

If any worker exits, the others are stopped and this exits non-zero, so Fly
restarts the machine (its default `on-failure` policy, set explicitly in
fly.toml): none runs without the others. A stop signal (Fly sends SIGINT)
is passed to all, and this exits 0 once all have ended.

Before any starts (Stage 3e): it refuses to start holding another
service's database URL or connecting as the wrong login, and it records the
start, raising `worker_restarting` at 3 starts in an hour
(docflow_core.worker_starts). Before those (A6; founder, 2026-11-11;
D-236): it refuses a DISPATCH_IN_FLIGHT_TARGET other than the one the parse
service's slots were sized for, and prints the value it starts with.

    python -m app.run_workers
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence

from docflow_core import db, worker_starts
from docflow_core.config import Settings, get_settings, model_ids_line, settings_refusal
from docflow_core.constants import DISPATCH_QUEUE, PAGES_QUEUE
from pydantic import ValidationError

# How long a worker gets to finish after the other has died, before it is killed.
PARTNER_STOP_SECONDS = 30
POLL_SECONDS = 0.5


def commands(app: str = "app.celery_app") -> dict[str, list[str]]:
    """The three workers' command lines. Their node names differ, so Celery
    sees three workers, not one started three times.

    Both start without mingle and without gossip (D-212). For about
    BROKER_POLL_SECONDS after every start a worker received nothing, a job
    already waiting included (four starts of four on Fly staging, D-197),
    read as mingle, Celery's start-up search for neighbours, holding a
    blocking read open. Nothing in DocFlow uses either: mingle shares revoked
    tasks, and DocFlow revokes none; gossip tracks the other workers, and
    nothing reads it. Remote control stays on: the Console's health strip
    pings the workers through it (apps/api/app/routers/admin.py)."""
    celery = [
        sys.executable, "-m", "celery", "-A", app, "worker", "--loglevel=info",
        "--without-mingle", "--without-gossip",
    ]
    return {
        "documents": [
            *celery,
            "-Q", "interactive,bulk",
            f"--concurrency={get_settings().dispatch_in_flight_target}",
            "-n", "documents@%h",
        ],
        "dispatch": [*celery, "-Q", DISPATCH_QUEUE, "--concurrency=1", "-n", "dispatch@%h"],
        "pages": [*celery, "-Q", PAGES_QUEUE, "--concurrency=1", "-n", "pages@%h"],
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


def in_flight_target_refusal() -> str | None:
    """Why the worker must not start, or None (A6; founder, 2026-11-11;
    D-236). DISPATCH_IN_FLIGHT_TARGET is the worker's document slots. The
    parse service's PARSE_SLOTS = 2 was sized for the setting's default: one
    document job plus one page job (D-232). The CI guard holds the two
    together (test_ci_guards.py) but can't see a value set as a Fly secret,
    so the worker checks the value it actually got: a whole number of at
    least 1, equal to that default. Raising capacity means changing the
    default and PARSE_SLOTS together (RUNBOOK 9.2)."""
    expected = Settings.model_fields["dispatch_in_flight_target"].default
    try:
        got: object = get_settings().dispatch_in_flight_target
    except ValidationError as exc:
        bad = [error for error in exc.errors() if error.get("loc") == ("dispatch_in_flight_target",)]
        if not bad:
            raise
        got = bad[0].get("input")
    if type(got) is int and got >= 1 and got == expected:
        return None
    return (
        f"DISPATCH_IN_FLIGHT_TARGET is {got!r}; expected {expected!r}. It must be a whole number of "
        f"at least 1 and equal {expected!r}, the value the parse service's PARSE_SLOTS was sized "
        "for (D-232). To change it, change the setting's default and PARSE_SLOTS together "
        "(RUNBOOK 9.2)."
    )


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
    start (3e, part B), then run the three workers."""
    try:
        refusal = in_flight_target_refusal()
    except ValidationError:
        # Settings refused something other than the in-flight target, such as
        # a model not priced for its role (D-239): one line, not a traceback.
        refusal = settings_refusal()
    if refusal is not None:
        print(f"run_workers refused to start: {refusal}", file=sys.stderr, flush=True)
        return 1
    # The deploy's check reads this line from `fly logs` (RUNBOOK 9.2).
    print(
        f"run_workers: dispatch_in_flight_target={get_settings().dispatch_in_flight_target}",
        file=sys.stderr,
        flush=True,
    )
    # Which models the workers' code calls (D-239; RUNBOOK 9.2).
    print(f"run_workers: {model_ids_line()}", file=sys.stderr, flush=True)
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
