#!/usr/bin/env python3
"""
3c pre-build check on a real Fly machine (founder's review, 2026-09-30):

  1. Can the parse service (root in the machine) create a cgroup v2 per job,
     with a memory limit, and does that limit bound the job's processes
     *together* -- where setrlimit only bounds each process on its own?
  2. Does a seccomp filter work on Fly's kernel, for the calls the founder
     listed, including when loaded by the unprivileged job user?

Every check reports its own evidence. Where a check can only mean something
next to a control, the control runs first and is reported beside it
(RUNBOOK 1.7). Run as root: `python3 /app/check.py`.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import resource
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

RESULTS: list[tuple[str, str, str]] = []


def report(name: str, ok: bool | None, detail: str) -> None:
    verdict = "PASS" if ok else ("INFO" if ok is None else "FAIL")
    RESULTS.append((name, verdict, detail))
    print(f"RESULT {name}: {verdict} -- {detail}", flush=True)


def section(title: str) -> None:
    print(f"\n### {title}", flush=True)


def sh(cmd: str) -> str:
    done = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
    return (done.stdout + done.stderr).strip()


def read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError as exc:
        return f"<{type(exc).__name__}: {exc.strerror}>"


def write(path: Path, value: str) -> str | None:
    """Returns None on success, else the error."""
    try:
        path.write_text(value)
        return None
    except OSError as exc:
        return f"{type(exc).__name__} errno={exc.errno} ({errno.errorcode.get(exc.errno, '?')})"


# A program that allocates N MiB and touches every page, so the memory is
# really used (not just reserved). Prints ALLOC_OK if it survives.
ALLOC = textwrap.dedent(
    """
    import sys, time
    n = int(sys.argv[1]) * 1024 * 1024
    b = bytearray(n)
    for i in range(0, n, 4096):
        b[i] = 1
    time.sleep(1)
    print("ALLOC_OK", flush=True)
    """
)

# A family: the parent starts 3 children, each allocating N MiB, and
# allocates N MiB itself. Prints how many of the 4 survived.
FAMILY = textwrap.dedent(
    """
    import subprocess, sys
    n = sys.argv[1]
    alloc = sys.argv[2]
    kids = [subprocess.Popen([sys.executable, "-c", alloc, n], stdout=subprocess.PIPE, text=True)
            for _ in range(3)]
    ok = 0
    try:
        exec(alloc)  # sys.argv[1] is n here too
        ok += 1
    except MemoryError:
        print("PARENT_MEMORYERROR", flush=True)
    for k in kids:
        out, _ = k.communicate()
        if "ALLOC_OK" in out:
            ok += 1
        else:
            print(f"CHILD_RC={k.returncode}", flush=True)
    print(f"FAMILY_SURVIVORS={ok}/4", flush=True)
    """
)

SANDBOX_PREFIX = [
    "unshare", "--net", "--mount", "--pid", "--ipc", "--uts", "--fork",
    "setpriv", "--reuid=10001", "--regid=10001", "--clear-groups",
    "--inh-caps=-all", "--bounding-set=-all", "--no-new-privs",
]


def run(argv: list[str], *, cgroup: Path | None = None, rlimit_as: int | None = None,
        timeout: int = 90) -> subprocess.CompletedProcess:
    def pre() -> None:
        if cgroup is not None:
            # Move this child into the job's cgroup *before* exec: everything
            # it starts from now on is born inside the limit.
            (cgroup / "cgroup.procs").write_text("0")
        if rlimit_as is not None:
            resource.setrlimit(resource.RLIMIT_AS, (rlimit_as, rlimit_as))

    try:
        return subprocess.run(argv, preexec_fn=pre, capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as exc:
        return subprocess.CompletedProcess(argv, -999, "", f"LAUNCH_FAILED {type(exc).__name__}: {exc}")


def oom_kills(cg: Path) -> int:
    for line in read(cg / "memory.events").splitlines():
        if line.startswith("oom_kill "):
            return int(line.split()[1])
    return -1


# ── 0. Facts about the machine ─────────────────────────────────────────────


def facts() -> None:
    section("0. The machine")
    report("arch", None, platform.machine())
    report("kernel", None, sh("uname -r"))
    report("pid1", None, sh("cat /proc/1/comm; tr '\\0' ' ' < /proc/1/cmdline"))
    report("memtotal", None, sh("grep MemTotal /proc/meminfo"))
    report("swap", None, sh("cat /proc/swaps") or "<empty>")
    report("cgroup-filesystems", None, sh("grep cgroup /proc/filesystems") or "<none>")
    report("cgroup-mounts", None, sh("grep cgroup /proc/self/mounts") or "<none mounted>")
    report("own-cgroup", None, read(Path("/proc/self/cgroup")))
    report("seccomp-status", None, sh("grep -E '^(Seccomp|NoNewPrivs)' /proc/self/status"))
    report("user-namespaces", None, read(Path("/proc/sys/user/max_user_namespaces")))
    report("block-devices-in-/dev", None, sh("ls -l /dev | grep '^b' || echo '<none>'"))


# ── 1. cgroup v2 ──────────────────────────────────────────────────────────


def cgroups() -> None:
    section("1. cgroup v2: create, limit, and does it bound the whole job?")
    root = Path("/sys/fs/cgroup")
    fstype = sh(f"stat -f -c %T {root}") if root.exists() else "<missing>"
    report("1a cgroup2-mounted-at-/sys/fs/cgroup", fstype == "cgroup2fs", f"fstype={fstype}")
    if fstype != "cgroup2fs":
        root = Path("/mnt/cg")
        root.mkdir(parents=True, exist_ok=True)
        out = sh(f"mount -t cgroup2 none {root} && stat -f -c %T {root}")
        report("1a' cgroup2-mountable-by-root", out.endswith("cgroup2fs"), f"mount at {root}: {out}")
        if not out.endswith("cgroup2fs"):
            return

    controllers = read(root / "cgroup.controllers")
    report("1b memory-controller-available", "memory" in controllers.split(),
           f"cgroup.controllers={controllers!r}")
    err = write(root / "cgroup.subtree_control", "+memory")
    err_p = write(root / "cgroup.subtree_control", "+pids")
    report("1c enable-memory-for-children-at-root", err is None,
           f"memory error={err} pids error={err_p} subtree_control={read(root / 'cgroup.subtree_control')!r}")

    parent = root / "docflow-check"
    parent.mkdir(exist_ok=True)
    err = write(parent / "cgroup.subtree_control", "+memory")
    err_p = write(parent / "cgroup.subtree_control", "+pids")
    report("1d delegate-memory-to-a-job-level", err is None,
           f"memory error={err} pids error={err_p} parent subtree_control={read(parent / 'cgroup.subtree_control')!r}")

    def make_job(name: str, mib: int | None) -> Path:
        job = parent / name
        job.mkdir(exist_ok=True)
        e1 = write(job / "memory.max", "max" if mib is None else str(mib * 1024 * 1024))
        e2 = write(job / "memory.swap.max", "0") if (job / "memory.swap.max").exists() else "no file"
        e3 = write(job / "pids.max", "128")
        e4 = write(job / "memory.oom.group", "1") if (job / "memory.oom.group").exists() else "no file"
        report(f"1e create-job-cgroup {name}", e1 is None,
               f"memory.max={read(job / 'memory.max')} (err {e1}); swap.max err {e2}; "
               f"pids.max={read(job / 'pids.max')} (err {e3}); oom.group err {e4}; "
               f"memory.peak exists={(job / 'memory.peak').exists()}")
        return job

    py = sys.executable

    # Positive control: 400 MiB with no limit at all.
    r = run([py, "-c", ALLOC, "400"])
    report("1f control: 400MiB with no limit survives", "ALLOC_OK" in r.stdout,
           f"rc={r.returncode} out={r.stdout.strip()!r}")

    # T1: one process, 400 MiB, in a 256 MiB job cgroup.
    job = make_job("job1", 256)
    before = oom_kills(job)
    r = run([py, "-c", ALLOC, "400"], cgroup=job)
    after = oom_kills(job)
    report("1g one process over the cgroup limit is OOM-killed",
           "ALLOC_OK" not in r.stdout and r.returncode == -9 and after > before,
           f"rc={r.returncode} out={r.stdout.strip()!r} oom_kill {before}->{after} "
           f"memory.peak={read(job / 'memory.peak')}")

    # T2 (the founder's point): setrlimit is per process. 4 processes x
    # 150 MiB, each under its own 256 MiB address-space limit = 600 MiB.
    r = run([py, "-c", FAMILY, "120", ALLOC], rlimit_as=256 * 1024 * 1024)
    report("1h rlimit only: 4 x 120MiB each under RLIMIT_AS=256MiB -- all survive (the cap multiplies)",
           "FAMILY_SURVIVORS=4/4" in r.stdout,
           f"rc={r.returncode} out={r.stdout.strip()!r}")

    # T3: the same family in a 256 MiB job cgroup.
    job2 = make_job("job2", 256)
    before = oom_kills(job2)
    r = run([py, "-c", FAMILY, "120", ALLOC], cgroup=job2)
    after = oom_kills(job2)
    leftover = read(job2 / "cgroup.procs")
    report("1i cgroup: the same family in a 256MiB job is stopped (the cap holds for the whole job)",
           "FAMILY_SURVIVORS=4/4" not in r.stdout and after > before,
           f"rc={r.returncode} out={r.stdout.strip()!r} oom_kill {before}->{after} "
           f"procs left in job={leftover!r} (memory.oom.group=1 should leave none)")

    # T4: the limit still holds inside the full sandbox (namespaces + user drop).
    # Control: a job cgroup with no memory limit (processes can't live in
    # `parent` itself: it hands controllers down, so it must stay empty).
    ctrl = make_job("ctrl", None)
    r = run(SANDBOX_PREFIX + [py, "-c", ALLOC, "400"], cgroup=ctrl)
    report("1j control: sandboxed 400MiB in an unlimited job cgroup survives", "ALLOC_OK" in r.stdout,
           f"rc={r.returncode} out={r.stdout.strip()!r} err={r.stderr.strip()[:200]!r}")
    job3 = make_job("job3", 256)
    before = oom_kills(job3)
    r = run(SANDBOX_PREFIX + [py, "-c", ALLOC, "400"], cgroup=job3)
    after = oom_kills(job3)
    report("1k sandboxed (namespaces + uid 10001 + no caps) 400MiB in a 256MiB job is killed",
           "ALLOC_OK" not in r.stdout and after > before,
           f"rc={r.returncode} out={r.stdout.strip()!r} oom_kill {before}->{after}")

    # T5: the job cannot loosen or leave its own limit.
    job4 = make_job("job4", 256)
    attempt = textwrap.dedent(
        f"""
        import os
        def w(p, v):
            try:
                open(p, "w").write(v); return "WROTE"
            except OSError as e:
                import errno; return errno.errorcode.get(e.errno, str(e.errno))
        print("RAISE_LIMIT", w("{job4}/memory.max", "max"))
        print("LEAVE_CGROUP", w("{parent}/cgroup.procs", str(os.getpid())))
        print("ROOT_CGROUP", w("{root}/cgroup.procs", str(os.getpid())))
        """
    )
    r = run(SANDBOX_PREFIX + [py, "-c", attempt], cgroup=job4)
    out = r.stdout.strip().replace("\n", " | ")
    report("1l the sandboxed job can't raise its limit or move out (/sys still visible)",
           "WROTE" not in r.stdout and "RAISE_LIMIT" in r.stdout,
           f"{out} memory.max now={read(job4 / 'memory.max')}")
    hide = ["unshare", "--net", "--mount", "--pid", "--ipc", "--uts", "--fork", "sh", "-c",
            "mount -t tmpfs -o size=1k,mode=0555 none /sys && exec " + " ".join(SANDBOX_PREFIX[7:])
            + f" {py} -c '" + attempt.replace("'", "\"") + "'"]
    r = run(hide, cgroup=job4)
    out = r.stdout.strip().replace("\n", " | ")
    report("1m with /sys hidden by tmpfs (the design), the cgroup files aren't even there",
           "WROTE" not in r.stdout and "ENOENT" in r.stdout,
           f"{out} err={r.stderr.strip()[:200]!r}")
    # Root can still change it: the control for 1l.
    err = write(job4 / "memory.max", str(300 * 1024 * 1024))
    report("1n control: root (the supervisor) can change the job's limit", err is None,
           f"error={err} memory.max={read(job4 / 'memory.max')}")

    # Cleanup: an emptied job cgroup can be removed (no leak per job).
    removed = []
    for j in (job, job2, ctrl, job3, job4):
        try:
            j.rmdir()
            removed.append(j.name)
        except OSError as exc:
            removed.append(f"{j.name}:{errno.errorcode.get(exc.errno)}")
    report("1o empty job cgroups are removable", all(":" not in x for x in removed), ",".join(removed))
    try:
        parent.rmdir()
    except OSError:
        pass


# ── 2. seccomp ────────────────────────────────────────────────────────────

SENTINEL = 1001  # an errno no real syscall returns: proves the filter answered
NR = {  # x86_64
    "unshare": 272, "setns": 308, "mount": 165, "umount2": 166, "ptrace": 101,
    "bpf": 321, "keyctl": 250, "perf_event_open": 298, "init_module": 175,
    "finit_module": 313, "delete_module": 176, "kexec_load": 246,
    "kexec_file_load": 320, "clone": 56, "clone3": 435,
}
CLONE_NEW = {
    "NEWNS": 0x00020000, "NEWCGROUP": 0x02000000, "NEWUTS": 0x04000000,
    "NEWIPC": 0x08000000, "NEWUSER": 0x10000000, "NEWPID": 0x20000000,
    "NEWNET": 0x40000000,
}
SIGCHLD = 17


def load_filter() -> None:
    import seccomp

    f = seccomp.SyscallFilter(defaction=seccomp.ALLOW)
    for name in ("unshare", "setns", "mount", "umount2", "ptrace", "bpf", "keyctl",
                 "perf_event_open", "init_module", "finit_module", "delete_module",
                 "kexec_load", "kexec_file_load"):
        f.add_rule(seccomp.ERRNO(SENTINEL), name)
    for bit in CLONE_NEW.values():
        f.add_rule(seccomp.ERRNO(SENTINEL), "clone", seccomp.Arg(0, seccomp.MASKED_EQ, bit, bit))
    # clone3 passes its flags in a struct seccomp can't read: refuse it with
    # ENOSYS so the C library falls back to clone(), whose flags it can read.
    f.add_rule(seccomp.ERRNO(errno.ENOSYS), "clone3")
    f.load()


def attempt_calls() -> dict[str, str]:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    L = ctypes.c_long

    def call(name: str, *args) -> str:
        ctypes.set_errno(0)
        rc = libc.syscall(L(NR[name]), *[L(a) if isinstance(a, int) else a for a in args])
        if rc == -1:
            e = ctypes.get_errno()
            return "SENTINEL" if e == SENTINEL else errno.errorcode.get(e, str(e))
        return f"ok({rc})"

    out = {}
    out["unshare(NEWNET)"] = call("unshare", CLONE_NEW["NEWNET"])
    out["unshare(NEWUSER)"] = call("unshare", CLONE_NEW["NEWUSER"])
    fd = os.open("/proc/self/ns/uts", os.O_RDONLY)
    out["setns(own uts)"] = call("setns", fd, CLONE_NEW["NEWUTS"])
    os.close(fd)
    out["mount(tmpfs)"] = call("mount", ctypes.c_char_p(b"none"), ctypes.c_char_p(b"/tmp"),
                               ctypes.c_char_p(b"tmpfs"), 0, None)
    out["umount2(/nonexistent)"] = call("umount2", ctypes.c_char_p(b"/nonexistent"), 0)
    out["ptrace(ATTACH, no such pid)"] = call("ptrace", 16, 999999, 0, 0)
    out["bpf(0,NULL,0)"] = call("bpf", 0, 0, 0)
    out["keyctl(GET_KEYRING_ID)"] = call("keyctl", 0, -3, 0)
    out["perf_event_open(NULL)"] = call("perf_event_open", 0, 0, -1, -1, 0)
    out["init_module(NULL)"] = call("init_module", 0, 0, ctypes.c_char_p(b""))
    out["finit_module(-1)"] = call("finit_module", -1, ctypes.c_char_p(b""), 0)
    out["delete_module(nonexistent)"] = call("delete_module", ctypes.c_char_p(b"docflow_none"), 0)
    out["kexec_load(bad arch)"] = call("kexec_load", 0, 0, 0, 0x7FFF0000)
    out["kexec_file_load(-1)"] = call("kexec_file_load", -1, -1, 0, 0, 0)
    out["clone3(NULL)"] = call("clone3", 0, 0)
    # clone with a namespace flag: on success this forks, so the child leaves at once.
    ctypes.set_errno(0)
    rc = libc.syscall(L(NR["clone"]), L(SIGCHLD | CLONE_NEW["NEWNET"]), L(0), L(0), L(0), L(0))
    if rc == 0:
        os._exit(0)
    if rc > 0:
        os.waitpid(rc, 0)
        out["clone(NEWNET)"] = "ok(child)"
    else:
        e = ctypes.get_errno()
        out["clone(NEWNET)"] = "SENTINEL" if e == SENTINEL else errno.errorcode.get(e, str(e))
    # Ordinary work must still run under the filter: a thread and a fork.
    import threading

    box = []
    t = threading.Thread(target=lambda: box.append("thread-ran"))
    t.start()
    t.join()
    out["normal thread"] = box[0] if box else "THREAD_FAILED"
    pid = os.fork()
    if pid == 0:
        os._exit(7)
    _, status = os.waitpid(pid, 0)
    out["normal fork"] = "fork-ran" if os.waitstatus_to_exitcode(status) == 7 else "FORK_FAILED"
    return out


CHILD = """
import json, sys
sys.path.insert(0, "/app")
import check
with_filter = sys.argv[1] == "filter"
if with_filter:
    check.load_filter()
print("CALLS " + json.dumps(check.attempt_calls()), flush=True)
print("STATUS " + open("/proc/self/status").read().split("Seccomp:")[1].split()[0], flush=True)
"""


def seccomp_checks() -> None:
    section("2. seccomp")
    import json

    def calls(argv: list[str]) -> tuple[dict, str, str]:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd="/")
        got, mode = {}, "?"
        for line in r.stdout.splitlines():
            if line.startswith("CALLS "):
                got = json.loads(line[6:])
            if line.startswith("STATUS "):
                mode = line[7:]
        return got, mode, r.stderr.strip()[-300:]

    py = sys.executable
    # Each run is a fresh process in a fresh mount namespace, so a call that
    # succeeds (the controls) changes nothing on the machine.
    base = ["unshare", "--mount", "--uts", "--net", "--fork"]
    control, mode_c, err_c = calls(base + [py, "-c", CHILD, "none"])
    report("2a control (root, no filter): what each call does without the filter", None,
           f"seccomp mode={mode_c} {control} {err_c}")
    filtered, mode_f, err_f = calls(base + [py, "-c", CHILD, "filter"])
    blocked = [k for k, v in filtered.items() if k not in ("normal thread", "normal fork", "clone3(NULL)")]
    report("2b root with the filter: every listed call answered by the filter",
           bool(filtered) and all(filtered[k] == "SENTINEL" for k in blocked),
           f"seccomp mode={mode_f} {filtered} {err_f}")
    report("2c clone3 refused with ENOSYS; a normal thread and fork still work",
           filtered.get("clone3(NULL)") == "ENOSYS" and filtered.get("normal thread") == "thread-ran"
           and filtered.get("normal fork") == "fork-ran",
           f"clone3={filtered.get('clone3(NULL)')} thread={filtered.get('normal thread')} "
           f"fork={filtered.get('normal fork')}")

    # The design: the unprivileged job user loads the filter itself, after
    # setpriv has set no_new_privs (the kernel requires one or the other).
    sandbox = SANDBOX_PREFIX
    uctrl, mode_uc, err_uc = calls(sandbox + [py, "-c", CHILD, "none"])
    report("2d control (uid 10001, no caps, no filter): what the job could do without seccomp", None,
           f"seccomp mode={mode_uc} {uctrl} {err_uc}")
    ufilt, mode_uf, err_uf = calls(sandbox + [py, "-c", CHILD, "filter"])
    ublocked = [k for k, v in ufilt.items() if k not in ("normal thread", "normal fork", "clone3(NULL)")]
    report("2e uid 10001 loads the filter under no_new_privs: every listed call answered by it",
           bool(ufilt) and all(ufilt[k] == "SENTINEL" for k in ublocked) and mode_uf == "2",
           f"seccomp mode={mode_uf} {ufilt} {err_uf}")


def main() -> int:
    if os.geteuid() != 0:
        print("run as root")
        return 2
    facts()
    if platform.machine() != "x86_64":
        report("arch-supported", False, "syscall numbers here are x86_64 only")
        return 1
    cgroups()
    seccomp_checks()
    section("Summary")
    for name, verdict, _ in RESULTS:
        if verdict != "INFO":
            print(f"{verdict}  {name}")
    return 0 if all(v != "FAIL" for _, v, _ in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
