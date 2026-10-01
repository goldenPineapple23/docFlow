#!/usr/bin/env python3
"""
3c cgroup check, run 2. Run 1 found Fly machines boot with the cgroup v1
controllers mounted (memory among them) and a cgroup v2 hierarchy with no
controllers, so v2's memory.max does not exist as delivered. This run
checks the two ways to a real per-job memory cap:

  A. the cgroup v1 memory controller, as Fly mounts it;
  B. moving the memory controller to v2 (unmount the v1 memory hierarchy,
     then enable it in v2). This alters the machine's setup, so it runs
     last, on a throwaway machine.

Same evidence rules as check.py: controls beside the checks they qualify.
"""

from __future__ import annotations

import errno
import subprocess
import sys
import textwrap
import time
from pathlib import Path

sys.path.insert(0, "/app")
from check import ALLOC, FAMILY, RESULTS, SANDBOX_PREFIX, read, report, run, section, sh, write  # noqa: E402

PY = sys.executable
MIB = 1024 * 1024


def v1_oom_kills(cg: Path) -> int:
    for line in read(cg / "memory.oom_control").splitlines():
        if line.startswith("oom_kill "):
            return int(line.split()[1])
    return -1


def v1() -> None:
    section("A. cgroup v1 memory controller (as Fly mounts it)")
    mem = Path("/sys/fs/cgroup/memory")
    pids = Path("/sys/fs/cgroup/pids")
    report("A0 v1 memory hierarchy present", (mem / "memory.limit_in_bytes").exists(),
           f"use_hierarchy={read(mem / 'memory.use_hierarchy')} "
           f"memsw file={(mem / 'memory.memsw.limit_in_bytes').exists()}")
    parent = mem / "docflow-check"
    parent.mkdir(exist_ok=True)
    pparent = pids / "docflow-check"
    pparent.mkdir(exist_ok=True)

    def job(name: str, mib: int | None) -> Path:
        j = parent / name
        j.mkdir(exist_ok=True)
        e = write(j / "memory.limit_in_bytes", "-1" if mib is None else str(mib * MIB))
        report(f"A1 create v1 job {name}", e is None,
               f"limit={read(j / 'memory.limit_in_bytes')} err={e}")
        return j

    def run_in(cgs: list[Path], argv: list[str]) -> subprocess.CompletedProcess:
        def pre() -> None:
            for cg in cgs:
                (cg / "cgroup.procs").write_text("0")
        try:
            return subprocess.run(argv, preexec_fn=pre, capture_output=True, text=True, timeout=90)
        except (subprocess.SubprocessError, OSError) as exc:
            return subprocess.CompletedProcess(argv, -999, "", f"LAUNCH_FAILED {exc}")

    ctrl = job("ctrl", None)
    r = run_in([ctrl], [PY, "-c", ALLOC, "400"])
    report("A2 control: 400MiB in an unlimited v1 job survives", "ALLOC_OK" in r.stdout,
           f"rc={r.returncode} out={r.stdout.strip()!r} {r.stderr[-200:]!r}")

    j1 = job("job1", 256)
    b = v1_oom_kills(j1)
    r = run_in([j1], [PY, "-c", ALLOC, "400"])
    report("A3 one process over a 256MiB v1 limit is OOM-killed",
           "ALLOC_OK" not in r.stdout and r.returncode == -9,
           f"rc={r.returncode} oom_kill {b}->{v1_oom_kills(j1)} "
           f"max_usage={read(j1 / 'memory.max_usage_in_bytes')} failcnt={read(j1 / 'memory.failcnt')}")

    j2 = job("job2", 256)
    b = v1_oom_kills(j2)
    r = run_in([j2], [PY, "-c", FAMILY, "120", ALLOC])
    report("A4 the 4 x 120MiB family in a 256MiB v1 job is stopped (cap holds for the whole job)",
           "FAMILY_SURVIVORS=4/4" not in r.stdout,
           f"rc={r.returncode} out={r.stdout.strip()!r} oom_kill {b}->{v1_oom_kills(j2)} "
           f"max_usage={read(j2 / 'memory.max_usage_in_bytes')} procs left={read(j2 / 'cgroup.procs')!r}")

    j3 = job("job3", 256)
    b = v1_oom_kills(j3)
    r = run_in([j3], SANDBOX_PREFIX + [PY, "-c", ALLOC, "400"])
    report("A5 inside the full sandbox (namespaces + uid 10001 + no caps): killed at the v1 limit",
           "ALLOC_OK" not in r.stdout and v1_oom_kills(j3) > b,
           f"rc={r.returncode} oom_kill {b}->{v1_oom_kills(j3)} err={r.stderr.strip()[-200:]!r}")
    r = run_in([ctrl], SANDBOX_PREFIX + [PY, "-c", ALLOC, "400"])
    report("A5' control: same sandboxed program in the unlimited job survives", "ALLOC_OK" in r.stdout,
           f"rc={r.returncode} out={r.stdout.strip()!r}")

    j4 = job("job4", 256)
    attempt = textwrap.dedent(
        f"""
        import os, errno
        def w(p, v):
            try:
                open(p, "w").write(v); return "WROTE"
            except OSError as e:
                return errno.errorcode.get(e.errno, str(e.errno))
        print("RAISE_LIMIT", w("{j4}/memory.limit_in_bytes", "-1"))
        print("LEAVE_CGROUP", w("{parent}/cgroup.procs", str(os.getpid())))
        print("ROOT_CGROUP", w("{mem}/cgroup.procs", str(os.getpid())))
        """
    )
    r = run_in([j4], SANDBOX_PREFIX + [PY, "-c", attempt])
    out = r.stdout.strip().replace("\n", " | ")
    report("A6 sandboxed job, /sys visible: can't raise its limit or leave",
           "WROTE" not in r.stdout and out.count("EACCES") == 3,
           f"{out} limit now={read(j4 / 'memory.limit_in_bytes')}")
    hide = ["unshare", "--net", "--mount", "--pid", "--ipc", "--uts", "--fork", "sh", "-c",
            "mount -t tmpfs -o size=1k,mode=0555 none /sys && exec "
            + " ".join(SANDBOX_PREFIX[7:]) + f" {PY} -c '" + attempt + "'"]
    r = run_in([j4], hide)
    out = r.stdout.strip().replace("\n", " | ")
    report("A7 with /sys hidden (the design): the cgroup files aren't there at all",
           "WROTE" not in r.stdout and out.count("ENOENT") == 3,
           f"{out} err={r.stderr.strip()[-200:]!r}")
    e = write(j4 / "memory.limit_in_bytes", str(300 * MIB))
    report("A8 control: root (the supervisor) can change the job's limit", e is None,
           f"err={e} limit={read(j4 / 'memory.limit_in_bytes')}")

    # pids (v1) as a per-job fork cap, the alternative to RLIMIT_NPROC per user.
    pj = pparent / "job"
    pj.mkdir(exist_ok=True)
    e = write(pj / "pids.max", "20")
    bomb = textwrap.dedent(
        """
        import os, time
        n = 0
        for _ in range(60):
            try:
                if os.fork() == 0:
                    time.sleep(3); os._exit(0)
                n += 1
            except OSError:
                break
        print(f"FORKED={n}", flush=True)
        """
    )
    r = run_in([pj], [PY, "-c", bomb])
    report("A9 v1 pids.max=20 stops a fork loop at the cap", e is None and "FORKED=" in r.stdout
           and int(r.stdout.split("FORKED=")[1].split()[0]) < 20,
           f"err={e} out={r.stdout.strip()!r} pids.events={read(pj / 'pids.events')!r}")

    time.sleep(4)
    removed = []
    for j in (ctrl, j1, j2, j3, j4, pj):
        try:
            j.rmdir()
            removed.append(j.name)
        except OSError as exc:
            removed.append(f"{j.name}:{errno.errorcode.get(exc.errno)}")
    report("A10 emptied v1 job cgroups are removable", all(":" not in x for x in removed), ",".join(removed))
    for p in (parent, pparent):
        try:
            p.rmdir()
        except OSError:
            pass


def v2_move() -> None:
    section("B. Moving the memory controller to v2 (alters the machine's setup)")
    unified = Path("/sys/fs/cgroup/unified")
    report("B0 before: v2 controllers", None, f"{read(unified / 'cgroup.controllers')!r}")
    children = sh("find /sys/fs/cgroup/memory -mindepth 1 -type d | head")
    report("B1 v1 memory hierarchy has no child cgroups left", children == "", f"children={children!r}")
    out = sh("umount /sys/fs/cgroup/memory && echo UMOUNTED")
    report("B2 root can unmount the v1 memory hierarchy", "UMOUNTED" in out, out)
    time.sleep(1)
    ctl = read(unified / "cgroup.controllers")
    report("B3 after: memory available to v2", "memory" in ctl.split(), f"cgroup.controllers={ctl!r}")
    if "memory" not in ctl.split():
        return
    e = write(unified / "cgroup.subtree_control", "+memory")
    report("B4 enable memory for v2 children", e is None,
           f"err={e} subtree_control={read(unified / 'cgroup.subtree_control')!r}")
    parent = unified / "docflow-check"
    parent.mkdir(exist_ok=True)
    e = write(parent / "cgroup.subtree_control", "+memory")
    report("B5 delegate memory to the job level", e is None,
           f"err={e} {read(parent / 'cgroup.subtree_control')!r}")
    j = parent / "job"
    j.mkdir(exist_ok=True)
    e1 = write(j / "memory.max", str(256 * MIB))
    e2 = write(j / "memory.oom.group", "1")
    report("B6 v2 job with memory.max and oom.group", e1 is None and e2 is None,
           f"max={read(j / 'memory.max')} e1={e1} e2={e2}")
    r = run([PY, "-c", FAMILY, "120", ALLOC], cgroup=j)
    report("B7 the 4 x 120MiB family in a 256MiB v2 job is stopped, whole group",
           "FAMILY_SURVIVORS=4/4" not in r.stdout,
           f"rc={r.returncode} out={r.stdout.strip()!r} events={read(j / 'memory.events')!r} "
           f"peak={read(j / 'memory.peak')} procs left={read(j / 'cgroup.procs')!r}")
    r = subprocess.run(["sh", "-c", "echo still-alive; uptime"], capture_output=True, text=True)
    report("B8 machine still healthy after the move", "still-alive" in r.stdout, r.stdout.strip())


def main() -> int:
    v1()
    v2_move()
    section("Summary")
    for name, verdict, _ in RESULTS:
        if verdict != "INFO":
            print(f"{verdict}  {name}")
    return 0 if all(v != "FAIL" for _, v, _ in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
