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


def _isolated_argv(slot: int, kind: str, job: JobCgroup) -> list[str]:
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

    if isolation:
        if job_cg is not None:
            argv = _isolated_argv(slot, kind, job_cg)
        else:  # a self-test control: the same sandbox, no cgroup
            argv = _isolated_argv(slot, kind, _NoCgroup())  # type: ignore[arg-type]
    else:
        argv = [sys.executable, "-m", "parse_service.job", kind]

    header_bytes = json.dumps(header).encode("utf-8") + b"\n"
    out, err = bytearray(), bytearray()
    overflow, err_overflow = threading.Event(), threading.Event()
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_chain_env(settings, isolation=isolation, seccomp=seccomp),
            close_fds=True,
            start_new_session=(os.name != "nt"),
        )
    except OSError as exc:
        if job_cg is not None:
            job_cg.remove()
        logger.error("job_start_failed error_type=%s", type(exc).__name__)
        return JobResult("isolation_failed" if isolation else "crashed", cause="start_failed")

    threads = [
        threading.Thread(target=_writer, args=(proc.stdin, header_bytes, body), daemon=True),
        threading.Thread(target=_reader, args=(proc.stdout, limits.answer_cap_bytes, out, overflow), daemon=True),
        threading.Thread(target=_reader, args=(proc.stderr, 16384, err, err_overflow), daemon=True),
    ]
    for thread in threads:
        thread.start()

    oom_before = job_cg.oom_kills() if job_cg else 0
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
            _kill(proc, job_cg)
            break
        time.sleep(config.WATCH_INTERVAL_SECONDS)

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _kill(proc, job_cg)
        proc.wait(timeout=10)
    for thread in threads:
        thread.join(timeout=5)

    evidence: dict = {"exit_status": proc.returncode}
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
        evidence["cgroup_removed"] = job_cg.remove()
    stderr_tail = bytes(err[-2000:]).decode("utf-8", errors="replace")
    result = _classify(proc.returncode, cause, bytes(out))
    result.seconds = round(time.monotonic() - started, 3)
    result.evidence = evidence
    if result.outcome in ("crashed", "isolation_failed"):
        # The tail of the job's own stderr: our messages, never file content
        # (the parsers print nothing about a file's contents).
        evidence["stderr_tail"] = stderr_tail[-500:]
    return result


class _NoCgroup:
    procs_files: list = []


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


def _classify(status: int | None, cause: str | None, out: bytes) -> JobResult:
    if cause is not None:
        return JobResult("stopped", cause=cause, exit_status=status)
    if status == EXIT_ISOLATION_FAILED:
        return JobResult("isolation_failed", cause="job_hardening", exit_status=status)
    if status == EXIT_MEMORY:
        return JobResult("stopped", cause="memory", exit_status=status)
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
