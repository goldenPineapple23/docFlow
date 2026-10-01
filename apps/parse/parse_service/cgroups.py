"""
One cgroup set per parse job: the primary memory, process and CPU caps
(Stage 3c, item 3; founder Q8 and change 3).

Two layouts, chosen at startup:
- **v1**, as Fly delivers its machines: the `memory`, `pids` and `cpu,cpuacct`
  controllers each mounted on their own hierarchy under /sys/fs/cgroup.
  Proven on a real Fly machine before building
  (docs/spikes/3c-cgroup-check/).
- **v2**, a single hierarchy with the controllers in it: GitHub's runners,
  and Fly too if it ever moves to v2.

v2 is used when it offers the memory controller; otherwise v1. Neither
available is a refusal to start (the canary fails).

Per job:
- memory: the limit, and **memory+swap set to the same value** (v1
  `memory.memsw.limit_in_bytes`, v2 `memory.swap.max` = 0) so swap can never
  carry a job past its limit (founder's change 2). If the swap limit can't
  be set, the job is refused unless the machine has no active swap at all;
- pids: the process cap;
- cpu: a quota of N CPUs (slows; one job can't starve the other slot) and
  the CPU seconds used, which the supervisor compares with the budget
  (kills).

v1 has no "kill the whole group on out-of-memory" switch; v2 has
`memory.oom.group`, set here. Either way the supervisor kills the whole job
when the out-of-memory count rises.
"""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

JOBS_PARENT = "docflow-jobs"
SUPERVISOR_LEAF = "docflow-supervisor"
CFS_PERIOD_US = 100_000


class CgroupError(Exception):
    """Carries a short reason naming the file or controller, never data."""


def _write(path: Path, value: str) -> None:
    try:
        path.write_text(value)
    except OSError as exc:
        raise CgroupError(f"write {path.name}: errno {exc.errno}") from exc


def _read(path: Path) -> str:
    return path.read_text().strip()


def _keyed(path: Path, key: str) -> int:
    for line in _read(path).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == key:
            return int(parts[1])
    return 0


def active_swap() -> bool:
    """True when /proc/swaps lists any swap device or file."""
    try:
        lines = Path("/proc/swaps").read_text().strip().splitlines()
    except OSError:
        return False
    return len(lines) > 1


def _mounts() -> list[tuple[str, str, list[str]]]:
    out = []
    for line in Path("/proc/self/mounts").read_text().splitlines():
        fields = line.split()
        if len(fields) >= 4:
            out.append((fields[1], fields[2], fields[3].split(",")))
    return out


@dataclass
class JobCgroup:
    version: int
    name: str
    dirs: list[Path]  # every directory the job's processes join
    memory: Path
    pids: Path
    cpu: Path  # v1: the cpu,cpuacct directory; v2: the job directory
    swap_limited: bool = False
    _removed: bool = field(default=False, repr=False)

    @property
    def procs_files(self) -> list[Path]:
        return [d / "cgroup.procs" for d in self.dirs]

    def processes(self) -> list[int]:
        try:
            return [int(p) for p in _read(self.memory / "cgroup.procs").split()]
        except (OSError, ValueError):
            return []

    def oom_kills(self) -> int:
        try:
            if self.version == 1:
                return _keyed(self.memory / "memory.oom_control", "oom_kill")
            return _keyed(self.memory / "memory.events", "oom_kill")
        except OSError:
            return 0

    def cpu_seconds(self) -> float:
        try:
            if self.version == 1:
                return int(_read(self.cpu / "cpuacct.usage")) / 1e9
            return _keyed(self.cpu / "cpu.stat", "usage_usec") / 1e6
        except (OSError, ValueError):
            return 0.0

    def throttled_periods(self) -> int:
        try:
            return _keyed(self.cpu / "cpu.stat", "nr_throttled")
        except OSError:
            return 0

    def limits(self) -> dict[str, str]:
        """What the kernel holds for this job, for the canary and the B tests."""
        out: dict[str, str] = {}
        if self.version == 1:
            out["memory"] = _read(self.memory / "memory.limit_in_bytes")
            memsw = self.memory / "memory.memsw.limit_in_bytes"
            out["memory_swap"] = _read(memsw) if memsw.exists() else "absent"
            out["cpu_quota"] = f"{_read(self.cpu / 'cpu.cfs_quota_us')} {_read(self.cpu / 'cpu.cfs_period_us')}"
        else:
            out["memory"] = _read(self.memory / "memory.max")
            swap = self.memory / "memory.swap.max"
            out["memory_swap"] = f"swap.max {_read(swap)}" if swap.exists() else "absent"
            out["cpu_quota"] = _read(self.cpu / "cpu.max")
        out["pids"] = _read(self.pids / "pids.max")
        return out

    def kill_all(self) -> None:
        """SIGKILL every process in the job, until none is left (or ~2 s)."""
        if self.version == 2 and (self.memory / "cgroup.kill").exists():
            try:
                (self.memory / "cgroup.kill").write_text("1")
            except OSError:
                pass
        for _ in range(100):
            pids = self.processes()
            if not pids:
                return
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            time.sleep(0.02)

    def remove(self) -> bool:
        """Remove the job's cgroups (they must be empty). True when all are gone."""
        if self._removed:
            return True
        self.kill_all()
        ok = True
        for directory in self.dirs:
            for _ in range(50):
                try:
                    directory.rmdir()
                    break
                except FileNotFoundError:
                    break
                except OSError:
                    time.sleep(0.02)
            else:
                ok = False
        self._removed = ok
        return ok


class Cgroups:
    """The machine's cgroup layout, set up once by the supervisor."""

    def __init__(self, version: int, roots: dict[str, Path]):
        self.version = version
        self.roots = roots  # v1: controller -> mount dir; v2: {"unified": dir}

    @classmethod
    def detect(cls) -> Cgroups:
        mounts = _mounts()
        for mountpoint, fstype, _ in mounts:
            if fstype == "cgroup2":
                root = Path(mountpoint)
                try:
                    controllers = _read(root / "cgroup.controllers").split()
                except OSError:
                    continue
                if {"memory", "pids", "cpu"} <= set(controllers):
                    return cls(2, {"unified": root})
        v1: dict[str, Path] = {}
        for mountpoint, fstype, options in mounts:
            if fstype != "cgroup":
                continue
            for controller in ("memory", "pids", "cpu", "cpuacct"):
                if controller in options:
                    v1[controller] = Path(mountpoint)
        if {"memory", "pids", "cpu", "cpuacct"} <= set(v1):
            if v1["cpu"] != v1["cpuacct"]:
                raise CgroupError("v1 cpu and cpuacct are on different hierarchies")
            return cls(1, v1)
        raise CgroupError("no cgroup layout offers memory, pids and cpu")

    def setup(self) -> None:
        if self.version == 1:
            for controller in ("memory", "pids", "cpu"):
                (self.roots[controller] / JOBS_PARENT).mkdir(exist_ok=True)
            return
        root = self.roots["unified"]
        enable = "+memory +pids +cpu"
        try:
            _write(root / "cgroup.subtree_control", enable)
        except CgroupError:
            # Not the real root (a container's own cgroup namespace): a cgroup
            # that hands controllers down may hold no process, so this
            # process moves into a leaf of its own first.
            leaf = root / SUPERVISOR_LEAF
            leaf.mkdir(exist_ok=True)
            for pid in _read(root / "cgroup.procs").split():
                try:
                    (leaf / "cgroup.procs").write_text(pid)
                except OSError:
                    pass
            _write(root / "cgroup.subtree_control", enable)
        parent = root / JOBS_PARENT
        parent.mkdir(exist_ok=True)
        _write(parent / "cgroup.subtree_control", enable)

    def job_dirs(self) -> list[str]:
        """Job cgroups that still exist (none should, between jobs)."""
        if self.version == 1:
            parents = [self.roots[c] / JOBS_PARENT for c in ("memory", "pids", "cpu")]
        else:
            parents = [self.roots["unified"] / JOBS_PARENT]
        return sorted(str(p) for parent in parents if parent.exists() for p in parent.iterdir() if p.is_dir())

    def create_job(self, name: str, *, memory_bytes: int, pids_max: int, cpu_quota_cpus: float) -> JobCgroup:
        quota = str(int(CFS_PERIOD_US * cpu_quota_cpus))
        if self.version == 1:
            dirs = {c: self.roots[c] / JOBS_PARENT / name for c in ("memory", "pids", "cpu")}
            for directory in dirs.values():
                directory.mkdir()
            job = JobCgroup(1, name, list(dirs.values()), dirs["memory"], dirs["pids"], dirs["cpu"])
            try:
                _write(dirs["memory"] / "memory.limit_in_bytes", str(memory_bytes))
                memsw = dirs["memory"] / "memory.memsw.limit_in_bytes"
                if memsw.exists():
                    _write(memsw, str(memory_bytes))  # memsw must be >= the limit: set after it
                    job.swap_limited = True
                _write(dirs["pids"] / "pids.max", str(pids_max))
                _write(dirs["cpu"] / "cpu.cfs_period_us", str(CFS_PERIOD_US))
                _write(dirs["cpu"] / "cpu.cfs_quota_us", quota)
            except CgroupError:
                job.remove()
                raise
        else:
            directory = self.roots["unified"] / JOBS_PARENT / name
            directory.mkdir()
            job = JobCgroup(2, name, [directory], directory, directory, directory)
            try:
                _write(directory / "memory.max", str(memory_bytes))
                if (directory / "memory.swap.max").exists():
                    _write(directory / "memory.swap.max", "0")
                    job.swap_limited = True
                if (directory / "memory.oom.group").exists():
                    _write(directory / "memory.oom.group", "1")
                _write(directory / "pids.max", str(pids_max))
                _write(directory / "cpu.max", f"{quota} {CFS_PERIOD_US}")
            except CgroupError:
                job.remove()
                raise
        if not job.swap_limited and active_swap():
            job.remove()
            raise CgroupError("swap is active and the job's memory+swap limit can't be set")
        return job
