"""
Runs one parse job (Stage 3c, item 3): one new process per file, never
reused.

With isolation on, the chain is

    supervisor (root, holds the token)
      -> /bin/sh: writes its own PID into the job's cgroups, then exec
      -> unshare --net --mount --pid --ipc --uts --fork --mount-proc --kill-child
      -> python -m parse_service.sandbox_init   (root, PID 1 of the job)
      -> setpriv ... python -m parse_service.job (the slot's user)

so every process of the job is born inside its cgroups. The chain's
environment is built here from nothing: the token never reaches it.

While the job runs, the supervisor reads the job's cgroups every
WATCH_INTERVAL_SECONDS and kills the whole job (every process in its
cgroups) when:
- the out-of-memory count rises (v1 kills one process at a time; this
  ends the rest) -> stopped: memory;
- the job's processes together pass the CPU budget -> stopped: cpu;
- the wall clock passes -> stopped: wall_clock;
- the answer passes its cap -> stopped: output_too_large.

With isolation off (dev on Windows; refused in production), the same job
runs as a plain subprocess with the same wall clock and answer cap, and
nothing else.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from parse_service import config
from parse_service.cgroups import CgroupError, Cgroups, JobCgroup

logger = logging.getLogger("parse_service.launcher")

EXIT_ISOLATION_FAILED = 70
EXIT_MEMORY = 71

_JOIN_CGROUPS = 'while [ "$1" != "--" ]; do echo $$ > "$1" || exit 70; shift; done; shift; exec "$@"'


@dataclass(frozen=True)
class Limits:
    """A job's limits. Callers may only lower them (`lowered`), never raise."""

    wall_seconds: float = config.PARSE_JOB_WALL_SECONDS
    memory_bytes: int = config.JOB_MEMORY_BYTES
    cpu_budget_seconds: float = config.JOB_CPU_BUDGET_SECONDS
    cpu_quota_cpus: float = config.JOB_CPU_QUOTA_CPUS
    pids_max: int = config.JOB_PIDS_MAX
    answer_cap_bytes: int = config.ANSWER_CAP_BYTES

    def lowered(self, **changes: float) -> Limits:
        for name, value in changes.items():
            if value > getattr(self, name):
                raise ValueError(f"a job limit may only be lowered: {name}")
        return Limits(**{**self.__dict__, **changes})


@dataclass
class JobResult:
    # ok | rejected | stopped | crashed | isolation_failed
    outcome: str
    answer: dict | None = None
    cause: str | None = None
    exit_status: int | None = None
    seconds: float = 0.0
    evidence: dict = field(default_factory=dict)


def _reader(stream, cap: int, sink: bytearray, overflow: threading.Event) -> None:
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            if len(sink) + len(chunk) > cap:
                overflow.set()
                return
            sink.extend(chunk)
    except (OSError, ValueError):
        return


def _writer(stream, header: bytes, body: bytes) -> None:
    try:
        stream.write(header)
        view = memoryview(body)
        for start in range(0, len(view), 1 << 20):
            stream.write(view[start : start + (1 << 20)])
        stream.close()
    except (BrokenPipeError, OSError, ValueError):
        pass


def _isolated_argv(slot: int, kind: str, job: JobCgroup, status_fd: int) -> list[str]:
    return [
        "/bin/sh",
        "-c",
        _JOIN_CGROUPS,
        "sh",
        *[str(p) for p in job.procs_files],
        "--",
        "/usr/bin/unshare",
        "--net",
        "--mount",
        "--pid",
        "--ipc",
        "--uts",
        "--fork",
        "--mount-proc",
        "--kill-child",
        sys.executable,
        "-I",
        "-m",
        "parse_service.sandbox_init",
        str(slot),
        kind,
        str(status_fd),
    ]


def _chain_env(settings: config.Settings, *, isolation: bool, seccomp: bool) -> dict[str, str]:
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LIBREOFFICE_PATH": settings.libreoffice_path,
        "PARSE_ISOLATION": "on" if (isolation and seccomp) else "off",
    }
    if not isolation and os.name == "nt":
        # Dev on Windows only: Python and LibreOffice need these to start.
        for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
            if name in os.environ:
                env[name] = os.environ[name]
        env["PATH"] = os.environ.get("PATH", "")
    return env


def run_job(
    kind: str,
    header: dict,
    body: bytes,
    *,
    slot: int,
    settings: config.Settings,
    cgroups: Cgroups | None,
    limits: Limits | None = None,
    use_cgroup: bool = True,
    seccomp: bool = True,
) -> JobResult:
    """
    `use_cgroup` and `seccomp` exist for the self-test controls only (a
    program run without its cap, to show the cap is what stopped it). The
    HTTP server never passes them; nothing in a request can reach them.
    """
    limits = limits or Limits()
    isolation = settings.isolation
    job_cg: JobCgroup | None = None
    started = time.monotonic()
    if isolation and use_cgroup:
        if cgroups is None:
            return JobResult("isolation_failed", cause="no_cgroups")
        try:
            job_cg = cgroups.create_job(
                f"job-{slot}-{uuid.uuid4().hex[:12]}",
                memory_bytes=limits.memory_bytes,
                pids_max=limits.pids_max,
                cpu_quota_cpus=limits.cpu_quota_cpus,
            )
        except (CgroupError, OSError) as exc:
            logger.error("job_cgroup_failed error=%s", exc)
            return JobResult("isolation_failed", cause="cgroup")

    # The reaper's private end-record pipe (sandbox_init._reap_as_init).
    status_r: int | None = None
    status_w: int | None = None
    if isolation:
        status_r, status_w = os.pipe()
        if job_cg is not None:
            argv = _isolated_argv(slot, kind, job_cg, status_w)
        else:  # a self-test control: the same sandbox, no cgroup
            argv = _isolated_argv(slot, kind, _NoCgroup(), status_w)  # type: ignore[arg-type]
    else:
        argv = [sys.executable, "-m", "parse_service.job", kind]

    header_bytes = json.dumps(header).encode("utf-8") + b"\n"
    out, err = bytearray(), bytearray()
    overflow, err_overflow = threading.Event(), threading.Event()
    try:
        with _children_lock:  # registered before the reaper can see its pid
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_chain_env(settings, isolation=isolation, seccomp=seccomp),
                close_fds=True,
                pass_fds=(status_w,) if status_w is not None else (),
                start_new_session=(os.name != "nt"),
            )
            _live_children.add(proc.pid)
    except OSError as exc:
        for fd in (status_r, status_w):
            if fd is not None:
                os.close(fd)
        if job_cg is not None:
            job_cg.remove()
        logger.error("job_start_failed error_type=%s", type(exc).__name__)
        return JobResult("isolation_failed" if isolation else "crashed", cause="start_failed")

    if status_w is not None:
        os.close(status_w)  # only the sandbox's reaper holds the write end now
    end_record_raw, end_overflow = bytearray(), threading.Event()
    threads = [
        threading.Thread(target=_writer, args=(proc.stdin, header_bytes, body), daemon=True),
        threading.Thread(target=_reader, args=(proc.stdout, limits.answer_cap_bytes, out, overflow), daemon=True),
        threading.Thread(target=_reader, args=(proc.stderr, 16384, err, err_overflow), daemon=True),
    ]
    if status_r is not None:
        status_stream = os.fdopen(status_r, "rb")
        threads.append(
            threading.Thread(target=_reader, args=(status_stream, 4096, end_record_raw, end_overflow), daemon=True)
        )
    for thread in threads:
        thread.start()

    oom_before = job_cg.oom_kills() if job_cg else 0
    killed = False
    cause: str | None = None
    peak_cpu = 0.0
    while True:
        if proc.poll() is not None and not threads[1].is_alive():
            break
        elapsed = time.monotonic() - started
        if job_cg is not None:
            peak_cpu = max(peak_cpu, job_cg.cpu_seconds())
            if job_cg.oom_kills() > oom_before:
                cause = "memory"
            elif peak_cpu > limits.cpu_budget_seconds:
                cause = "cpu"
        if cause is None and overflow.is_set():
            cause = "output_too_large"
        if cause is None and elapsed > limits.wall_seconds:
            cause = "wall_clock"
        if cause is not None:
            killed = True
            _kill(proc, job_cg)
            break
        time.sleep(config.WATCH_INTERVAL_SECONDS)

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        killed = True
        _kill(proc, job_cg)
        proc.wait(timeout=10)
    for thread in threads:
        thread.join(timeout=5)

    evidence: dict = {"exit_status": proc.returncode}
    with _children_lock:
        _live_children.discard(proc.pid)
        if killed and _reaper_started:
            # kill_all returned only once the job's cgroup was empty, so its
            # sandbox's PID 1 is already a zombie child of ours: reap it now,
            # before this slot can start another job (Fly run 1, cgroup v1).
            RECENT_REAPS.extend(_reap_zombie_orphans(frozenset({config.SLOT_UID_BASE + slot})))
    if job_cg is not None:
        if cause is None and job_cg.oom_kills() > oom_before:
            cause = "memory"
        evidence.update(
            oom_kills=job_cg.oom_kills() - oom_before,
            cpu_seconds=round(max(peak_cpu, job_cg.cpu_seconds()), 3),
            throttled_periods=job_cg.throttled_periods(),
            processes_left_before_cleanup=len(job_cg.processes()),
            limits=job_cg.limits(),
            swap_limited=job_cg.swap_limited,
        )
    if job_cg is not None and _reaper_started and not killed and cause is None:
        # The backstop: a job that ended normally leaves no orphans.
        found = _backstop(slot)
        if found:
            evidence["backstop_found"] = found
    if job_cg is not None:
        evidence["cgroup_removed"] = job_cg.remove()
    stderr_tail = bytes(err[-2000:]).decode("utf-8", errors="replace")
    if isolation:
        records = _records(bytes(end_record_raw))
        evidence["hardened"] = records[0] if records else None
        evidence["job_end"] = records[1] if len(records) > 1 else None
        job_status, isolation_cause = _job_status(records, seccomp_expected=seccomp)
    else:
        job_status, isolation_cause = proc.returncode, None
    result = _classify(job_status, cause, bytes(out), isolation_cause=isolation_cause)
    result.seconds = round(time.monotonic() - started, 3)
    result.evidence = evidence
    if result.outcome in ("crashed", "isolation_failed", "rejected"):
        # The tail of the job's own stderr: our messages and, for a failed
        # conversion, LibreOffice's own (the parsers print nothing about a
        # file's contents). For the service log only; never in the answer.
        evidence["stderr_tail"] = stderr_tail[-500:]
    return result


class _NoCgroup:
    procs_files: list = []


# ── Orphans of killed jobs (BUILD-STATUS 3c, B5/B11) ─────────────────────────
#
# Killing a job's cgroup kills `unshare` and its child, the sandbox's own
# PID 1, together; the child is reparented to this process (the subreaper)
# and would stay a zombie for the life of the service. So this process reaps
# them the moment they end (founder, 2026-10-01: no timing window): SIGCHLD
# wakes the reaper thread, which waits for every zombie child of ours that
# came from a job's sandbox and is not a live job's own Popen child.
#
# "Came from a job's sandbox" is decided by what the process is, never by
# how the kernel shows its cgroup: on Fly's cgroup v1 a dead process shows
# "/" (Fly run 1, 2026-10-01). It must be in a PID namespace other than
# ours (only a sandbox's processes are: the jobs' own unshare and the
# self-tests' setpriv children stay in ours) and run as a slot's user.
#
# Not waitpid(-1): that would also take the exit status of this process's
# legitimate children (the jobs' Popen processes, a self-test's other
# subprocesses), and Python's subprocess then reports a crashed child as
# exit 0. Each orphan is waited for by pid instead, the moment it is a
# zombie. A job's Popen process is created and entered in _live_children
# under _children_lock, the lock the reaper holds, so the reaper never sees
# a job's pid before it is registered, however soon the job dies. The
# SIGCHLD wakeup goes through Python's wakeup fd, written at C level
# whichever thread takes the signal, so the reaper never waits on the main
# thread (which may be blocked for minutes).
#
# A backstop runs after every job that ended normally: such a job leaves no
# orphans (its sandbox's PID 1 reaps inside and unshare reaps that), so any
# found among its slot's user's processes is counted (BACKSTOP_FOUND; B11
# fails on it) and logged.
_children_lock = threading.Lock()
_live_children: set[int] = set()
PR_SET_CHILD_SUBREAPER = 36
SLOT_UIDS = frozenset(config.SLOT_UID_BASE + slot for slot in range(config.PARSE_SLOTS))
RECENT_REAPS: deque = deque(maxlen=64)  # evidence for the self-tests (B5)
BACKSTOP_FOUND: list[dict] = []
_reaper_started = False
_own_pid_ns: str | None = None


def become_subreaper() -> None:
    """Orphans of our jobs come to this process instead of the machine's init
    (which on Fly is not us), so the reaper can wait for them. Linux only."""
    if not sys.platform.startswith("linux"):
        return
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_CHILD_SUBREAPER) failed")


def start_reaper() -> None:
    """Become the subreaper and start reaping on SIGCHLD. Call once, from the
    main thread (signal handlers and the wakeup fd can only be set there)."""
    global _reaper_started, _own_pid_ns
    if not sys.platform.startswith("linux") or _reaper_started:
        return
    _own_pid_ns = os.readlink("/proc/self/ns/pid")
    become_subreaper()
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_w, False)
    # A handler must be installed for the wakeup fd to be written; it does
    # nothing itself (SIGCHLD's default would ignore the signal).
    signal.signal(signal.SIGCHLD, lambda _signum, _frame: None)
    signal.set_wakeup_fd(wake_w, warn_on_full_buffer=False)
    threading.Thread(target=_reaper_loop, args=(wake_r,), daemon=True, name="orphan-reaper").start()
    _reaper_started = True


def _reaper_loop(wake_r: int) -> None:
    while True:
        try:
            os.read(wake_r, 4096)  # blocks until at least one SIGCHLD
        except InterruptedError:
            continue
        with _children_lock:
            RECENT_REAPS.extend(_reap_zombie_orphans(SLOT_UIDS))


def process_facts(pid: int) -> dict:
    """Name, state, parent, user, cgroup and PID namespace of one process."""
    facts: dict = {"pid": pid}
    try:
        with open(f"/proc/{pid}/status") as handle:
            for line in handle:
                key, _, value = line.partition(":")
                if key in ("Name", "State", "PPid"):
                    facts[key.lower()] = value.strip()
                elif key == "Uid":
                    facts["uid"] = int(value.split()[0])  # the real uid
    except (OSError, ValueError, IndexError) as exc:
        facts["status_error"] = type(exc).__name__
    try:
        with open(f"/proc/{pid}/cgroup") as handle:
            facts["cgroup"] = handle.read().strip().splitlines()
    except OSError as exc:
        facts["cgroup"] = type(exc).__name__
    try:
        facts["pid_ns"] = os.readlink(f"/proc/{pid}/ns/pid")
    except OSError as exc:
        facts["pid_ns"] = type(exc).__name__
    return facts


def _from_a_sandbox(facts: dict, uids: frozenset[int]) -> bool:
    """A process from a job's sandbox: another PID namespace, a slot's user."""
    pid_ns = facts.get("pid_ns")
    return (
        isinstance(pid_ns, str)
        and pid_ns.startswith("pid:[")
        and _own_pid_ns is not None
        and pid_ns != _own_pid_ns
        and facts.get("uid") in uids
    )


def _orphans(uids: frozenset[int]) -> list[dict]:
    """Facts for every child of this process that came from a job's sandbox
    running as one of `uids` and that no live job owns. Lock held."""
    me = str(os.getpid())
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) in _live_children:
            continue
        facts = process_facts(int(entry))
        if facts.get("ppid") == me and _from_a_sandbox(facts, uids):
            found.append(facts)
    return found


def _reap_zombie_orphans(uids: frozenset[int]) -> list[dict]:
    """Wait for each zombie orphan running as one of `uids`. Lock held."""
    reaped = []
    for facts in _orphans(uids):
        if not str(facts.get("state", "")).startswith("Z"):
            continue
        try:
            done, status = os.waitpid(facts["pid"], os.WNOHANG)
        except ChildProcessError:
            continue
        if done:
            facts["wait_status"] = status
            reaped.append(facts)
    return reaped


def _backstop(slot: int) -> list[dict]:
    """After a job that ended normally: orphans from its slot, if any (there
    should be none; one slot runs one job at a time). Zombies among them are
    reaped; all are counted and logged."""
    uids = frozenset({config.SLOT_UID_BASE + slot})
    with _children_lock:
        found = _orphans(uids)
        reaped = {f["pid"] for f in _reap_zombie_orphans(uids)}
    for facts in found:
        facts["reaped_by_backstop"] = facts["pid"] in reaped
    if found:
        BACKSTOP_FOUND.extend(found)
        logger.error("job_orphans_found_by_backstop count=%s", len(found))
    return found


def _kill(proc: subprocess.Popen, job_cg: JobCgroup | None) -> None:
    if job_cg is not None:
        job_cg.kill_all()
    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _records(raw: bytes) -> list[dict]:
    """The private pipe's lines, in order: the hardened child's confirmation,
    then the reaper's record of how the job ended. Anything unreadable ends
    the list there."""
    out: list[dict] = []
    for line in raw.decode("utf-8", errors="replace").splitlines()[:2]:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            break
        if not isinstance(record, dict):
            break
        out.append(record)
    return out


def _job_status(records: list[dict], *, seccomp_expected: bool) -> tuple[int | None, str | None]:
    """
    (the parser's own end, None), with a signal N as -N as subprocess
    reports it; or (None, why isolation failed). Isolation is confirmed only
    by the first record being exactly the hardened child's message, sent
    before the parser existed (founder, Q13), for the seccomp setting this
    launcher asked for. Without it, isolation failed, whatever happened
    next; with it, the end is the parser's, whatever its exit code.
    """
    if not records:
        return None, "not_hardened"
    if records[0].get("sandbox") == "failed":
        return None, "sandbox_init"
    if records[0] != {"hardened": True, "seccomp": seccomp_expected}:
        return None, "not_hardened"
    end = records[1] if len(records) > 1 else None
    if end is not None and isinstance(end.get("signaled"), int) and not isinstance(end.get("signaled"), bool):
        return -end["signaled"], None
    if end is not None and isinstance(end.get("exited"), int) and not isinstance(end.get("exited"), bool):
        return end["exited"], None
    return None, "reaper_lost"  # hardened, then no end record: the sandbox's PID 1 failed


def _classify(status: int | None, cause: str | None, out: bytes, *, isolation_cause: str | None = None) -> JobResult:
    """
    How a job ended, from the supervisor's own records first (founder,
    2026-10-01): `cause` is its kill decision (wall clock, CPU budget,
    answer cap) or the cgroup's OOM count (memory). Only when none of those
    applies does the parser's own end -- `status`, from the reaper's private
    record, never the sandbox's exit status -- decide, and then only as the
    parser failing on its own: a parser that exits 137 is a crash, not a kill.
    """
    if cause is not None:
        return JobResult("stopped", cause=cause, exit_status=status)
    if isolation_cause is not None:
        return JobResult("isolation_failed", cause=isolation_cause, exit_status=status)
    if status == EXIT_MEMORY:
        # The parser's own MemoryError (the per-process RLIMIT_AS backstop):
        # a parser failure, never DOC-029 -- a parser could exit 71 by itself
        # (founder, Q13). A real overrun reaches the job's cgroup first, since
        # RLIMIT_AS_BYTES >= JOB_MEMORY_BYTES, and `cause` says memory.
        logger.warning("job_self_reported_memory_error")
        return JobResult("crashed", cause="self_reported_memory_error", exit_status=status)
    if status != 0:
        name = f"signal_{-status}" if status is not None and status < 0 else f"exit_{status}"
        return JobResult("crashed", cause=name, exit_status=status)
    try:
        answer = json.loads(out.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JobResult("crashed", cause="invalid_output", exit_status=status)
    if not isinstance(answer, dict):
        return JobResult("crashed", cause="invalid_output", exit_status=status)
    outcome = answer.get("outcome")
    if outcome not in ("ok", "rejected", "selftest"):
        return JobResult("crashed", cause="invalid_output", exit_status=status)
    return JobResult(outcome, answer=answer, exit_status=status)
