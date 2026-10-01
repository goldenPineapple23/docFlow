"""
Fixed programs that run INSIDE a parse job, for the canary and the 3c test
table's A, S and B rows. They are started only by `parse_service.selftest`
(a root shell on the machine, or the canary at startup); no HTTP request
can choose one. Each returns {"outcome": "selftest", ...} with what it
found, and nothing about any customer file (there is none here).

The same programs also run OUTSIDE the sandbox as the controls (RUNBOOK
1.7): `parse_service.selftest` calls `run()` in its own process for that.
"""

from __future__ import annotations

import ctypes
import errno
import os
import socket
import subprocess
import sys
import time

SENTINEL_ERRNO = 1001  # no real call returns it: proves the filter answered (S1)

# x86_64 call numbers for the S rows.
NR = {
    "unshare": 272,
    "setns": 308,
    "mount": 165,
    "umount2": 166,
    "ptrace": 101,
    "bpf": 321,
    "keyctl": 250,
    "add_key": 248,
    "request_key": 249,
    "perf_event_open": 298,
    "init_module": 175,
    "finit_module": 313,
    "delete_module": 176,
    "kexec_load": 246,
    "kexec_file_load": 320,
    "fsopen": 430,
    "fsconfig": 431,
    "fsmount": 432,
    "fspick": 433,
    "move_mount": 429,
    "open_tree": 428,
    "mount_setattr": 442,
    "process_vm_readv": 310,
    "process_vm_writev": 311,
    "io_uring_setup": 425,
    "io_uring_enter": 426,
    "io_uring_register": 427,
    "userfaultfd": 323,
    "clone": 56,
    "clone3": 435,
    "getpid": 39,
}
SIGCHLD = 17


def _libc():
    lib = ctypes.CDLL(None, use_errno=True)
    lib.syscall.restype = ctypes.c_long
    return lib


def _syscall(name_or_nr, *args) -> str:
    lib = _libc()
    nr = NR[name_or_nr] if isinstance(name_or_nr, str) else name_or_nr
    ctypes.set_errno(0)
    converted = [ctypes.c_long(a) if isinstance(a, int) else a for a in args]
    rc = lib.syscall(ctypes.c_long(nr), *converted)
    if rc == -1:
        err = ctypes.get_errno()
        if err == SENTINEL_ERRNO:
            return "FILTER"
        return errno.errorcode.get(err, str(err))
    return f"ok({rc})"


# ── network ────────────────────────────────────────────────────────────────


def _tcp(host: str, port: int, family: int) -> str:
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(3)
            sock.connect((host, port))
        return "reached"
    except OSError as exc:
        return f"blocked: {errno.errorcode.get(exc.errno or 0, type(exc).__name__)}"


def _dns(name: str) -> str:
    try:
        socket.getaddrinfo(name, 443)
        return "resolved"
    except OSError as exc:
        return f"blocked: {type(exc).__name__}"


def _interfaces() -> list[str]:
    import fcntl
    import struct

    names = []
    with open("/proc/self/net/dev") as handle:
        for line in handle.readlines()[2:]:
            names.append(line.split(":")[0].strip())
    out = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for name in names:
            try:
                raw = fcntl.ioctl(sock.fileno(), 0x8913, struct.pack("256s", name.encode()[:15]))
                up = bool(struct.unpack("H", raw[16:18])[0] & 0x1)
            except OSError:
                up = False
            out.append(f"{name}({'UP' if up else 'down'})")
    return out


def probe_network(args: dict) -> dict:
    """A1-A7: every target in `args["tcp"]` ([label, host, port, 4|6]) and
    every name in `args["dns"]`. Hosts are addresses resolved outside first,
    so "blocked" means the address is unreachable, not just a failed lookup."""
    out: dict = {"tcp": {}, "dns": {}}
    for label, host, port, version in args.get("tcp", []):
        family = socket.AF_INET6 if int(version) == 6 else socket.AF_INET
        out["tcp"][label] = _tcp(host, int(port), family)
    for name in args.get("dns", []):
        out["dns"][name] = _dns(name)
    try:
        out["interfaces"] = _interfaces()
    except OSError as exc:
        out["interfaces"] = [f"error {exc.errno}"]
    return out


# ── the job's view of the machine ──────────────────────────────────────────


def _status() -> dict[str, str]:
    fields = {}
    with open("/proc/self/status") as handle:
        for line in handle:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def _try_write(path: str) -> str:
    target = os.path.join(path, f".parse-selftest-{os.getpid()}")
    try:
        with open(target, "wb") as handle:
            handle.write(b"x")
        os.unlink(target)
        return "writable"
    except OSError as exc:
        return errno.errorcode.get(exc.errno or 0, str(exc.errno))


def probe_view(args: dict) -> dict:
    """A8, A9, A11, A12, A13, A14."""
    status = _status()
    dev = {}
    for name in sorted(os.listdir("/dev")):
        mode = os.lstat(f"/dev/{name}").st_mode
        kind = (
            "block"
            if (mode & 0o170000) == 0o060000
            else "char"
            if (mode & 0o170000) == 0o020000
            else "link"
            if (mode & 0o170000) == 0o120000
            else "dir"
            if (mode & 0o170000) == 0o040000
            else "file"
        )
        dev[name] = kind
    return {
        "uid": os.getuid(),
        "gid": os.getgid(),
        "groups": os.getgroups(),
        "CapEff": status.get("CapEff"),
        "CapBnd": status.get("CapBnd"),
        "CapPrm": status.get("CapPrm"),
        "NoNewPrivs": status.get("NoNewPrivs"),
        "Seccomp": status.get("Seccomp"),
        "fly_dir": sorted(os.listdir("/.fly")) if os.path.isdir("/.fly") else "absent",
        "sys_dir": sorted(os.listdir("/sys")) if os.path.isdir("/sys") else "absent",
        "dev": dev,
        "writes": {p: _try_write(p) for p in ("/", "/usr", "/etc", "/tmp", "/opt", "/work", "/lohome")},
        "processes_visible": sorted(int(p) for p in os.listdir("/proc") if p.isdigit()),
        "pid": os.getpid(),
        "env_names": sorted(os.environ),
        "work_listing": sorted(os.listdir("/work")) if os.path.isdir("/work") else "absent",
        "netns": os.readlink("/proc/self/ns/net"),
        "pidns": os.readlink("/proc/self/ns/pid"),
    }


def escape(args: dict) -> dict:
    """A10: no way out -- join another namespace, bring an interface up, mount."""
    out = {}
    fd = os.open("/proc/self/ns/net", os.O_RDONLY)
    try:
        out["setns"] = _syscall("setns", fd, 0x40000000)
    finally:
        os.close(fd)
    out["mount_tmpfs"] = _syscall(
        "mount", ctypes.c_char_p(b"none"), ctypes.c_char_p(b"/work"), ctypes.c_char_p(b"tmpfs"), 0, None
    )
    try:
        import fcntl
        import struct

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            fcntl.ioctl(sock.fileno(), 0x8914, struct.pack("16sH14s", b"lo", 0x1, b"\x00" * 14))
        out["interface_up"] = "ok"
    except OSError as exc:
        out["interface_up"] = errno.errorcode.get(exc.errno or 0, str(exc.errno))
    return out


# ── seccomp ────────────────────────────────────────────────────────────────


def _calls() -> dict[str, str]:
    """Every call the filter refuses, each made with harmless arguments."""
    c = ctypes.c_char_p
    out = {}
    for flag_name, bit in (("NEWNET", 0x40000000), ("NEWUSER", 0x10000000), ("NEWNS", 0x00020000)):
        out[f"unshare({flag_name})"] = _syscall("unshare", bit)
    fd = os.open("/proc/self/ns/uts", os.O_RDONLY)
    out["setns"] = _syscall("setns", fd, 0x04000000)
    os.close(fd)
    out["mount"] = _syscall("mount", c(b"none"), c(b"/work"), c(b"tmpfs"), 0, None)
    out["umount2"] = _syscall("umount2", c(b"/nonexistent"), 0)
    out["ptrace"] = _syscall("ptrace", 16, 999999, 0, 0)
    out["process_vm_readv"] = _syscall("process_vm_readv", 999999, 0, 0, 0, 0, 0)
    out["process_vm_writev"] = _syscall("process_vm_writev", 999999, 0, 0, 0, 0, 0)
    out["bpf"] = _syscall("bpf", 0, 0, 0)
    out["keyctl"] = _syscall("keyctl", 0, -3, 0)
    out["add_key"] = _syscall("add_key", c(b"user"), c(b"docflow"), c(b"x"), 1, -3)
    out["request_key"] = _syscall("request_key", c(b"user"), c(b"docflow-none"), None, 0)
    out["perf_event_open"] = _syscall("perf_event_open", 0, 0, -1, -1, 0)
    out["init_module"] = _syscall("init_module", 0, 0, c(b""))
    out["finit_module"] = _syscall("finit_module", -1, c(b""), 0)
    out["delete_module"] = _syscall("delete_module", c(b"docflow_none"), 0)
    out["kexec_load"] = _syscall("kexec_load", 0, 0, 0, 0x7FFF0000)
    out["kexec_file_load"] = _syscall("kexec_file_load", -1, -1, 0, 0, 0)
    out["fsopen"] = _syscall("fsopen", c(b"tmpfs"), 0)
    out["fsconfig"] = _syscall("fsconfig", -1, 0, 0, 0, 0)
    out["fsmount"] = _syscall("fsmount", -1, 0, 0)
    out["fspick"] = _syscall("fspick", -100, c(b"/"), 0)
    out["move_mount"] = _syscall("move_mount", -1, c(b""), -1, c(b""), 0)
    out["open_tree"] = _syscall("open_tree", -100, c(b"/"), 0)
    out["mount_setattr"] = _syscall("mount_setattr", -100, c(b"/"), 0, 0, 0)
    out["io_uring_setup"] = _syscall("io_uring_setup", 1, 0)
    out["io_uring_enter"] = _syscall("io_uring_enter", -1, 0, 0, 0, 0, 0)
    out["io_uring_register"] = _syscall("io_uring_register", -1, 0, 0, 0)
    out["userfaultfd"] = _syscall("userfaultfd", 0)
    # clone with a namespace flag: on success this forks, so the child leaves.
    lib = _libc()
    ctypes.set_errno(0)
    rc = lib.syscall(ctypes.c_long(NR["clone"]), ctypes.c_long(SIGCHLD | 0x40000000), *([ctypes.c_long(0)] * 4))
    if rc == 0:
        os._exit(0)
    if rc > 0:
        os.waitpid(rc, 0)
        out["clone(NEWNET)"] = "ok(child)"
    else:
        err = ctypes.get_errno()
        out["clone(NEWNET)"] = "FILTER" if err == SENTINEL_ERRNO else errno.errorcode.get(err, str(err))
    return out


def syscalls(args: dict) -> dict:
    """S1 (sentinel filter stacked on the shipped one), S2 (shipped only), S3
    (control: no filter, chosen by the launcher), S4 (normal work)."""
    if args.get("sentinel"):
        from parse_service import seccomp_filter

        seccomp_filter.load(SENTINEL_ERRNO)
    out = {"calls": _calls(), "Seccomp": _status().get("Seccomp"), "NoNewPrivs": _status().get("NoNewPrivs")}
    out["clone3"] = _syscall("clone3", 0, 0)
    import threading

    box: list[str] = []
    thread = threading.Thread(target=lambda: box.append("ran"))
    thread.start()
    thread.join()
    out["thread"] = box[0] if box else "failed"
    pid = os.fork()
    if pid == 0:
        os._exit(7)
    _, status = os.waitpid(pid, 0)
    out["fork"] = "ran" if os.waitstatus_to_exitcode(status) == 7 else "failed"
    return out


def x32(args: dict) -> dict:
    """S5: getpid with the x32 bit. Under the filter the process is killed
    (SIGSYS), so it never answers; without it, the result is reported."""
    return {"x32_getpid": _syscall(0x40000000 | NR["getpid"])}


# ── limits ─────────────────────────────────────────────────────────────────

_ALLOC = (
    "import sys, time\n"
    "n = int(sys.argv[1]) * 1024 * 1024\n"
    "b = bytearray(n)\n"
    "for i in range(0, n, 4096):\n"
    "    b[i] = 1\n"
    "time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 0.5)\n"
    "print('ALLOC_OK', flush=True)\n"
)


def memory(args: dict) -> dict:
    """B1 (one process) and B2 (`processes` of `mib` each, together)."""
    count, mib = int(args.get("processes", 1)), int(args["mib"])
    kids = [
        subprocess.Popen([sys.executable, "-c", _ALLOC, str(mib), "1"], stdout=subprocess.PIPE, text=True)
        for _ in range(count)
    ]
    survived = sum(1 for kid in kids if "ALLOC_OK" in kid.communicate()[0])
    return {"processes": count, "mib_each": mib, "survived": survived}


_SPIN = (
    "import time\nend = time.monotonic() + float(__import__('sys').argv[1])\nwhile time.monotonic() < end:\n    pass\n"
)


def cpu(args: dict) -> dict:
    """B4 (budget) and B12 (quota): `processes` spinning for `seconds` each."""
    count, seconds = int(args.get("processes", 1)), float(args["seconds"])
    start = time.monotonic()
    kids = [subprocess.Popen([sys.executable, "-c", _SPIN, str(seconds)]) for _ in range(count)]
    for kid in kids:
        kid.wait()
    wall = time.monotonic() - start
    usage = os.times()
    return {
        "processes": count,
        "wall_seconds": round(wall, 2),
        "children_cpu_seconds": round(usage.children_user + usage.children_system, 2),
    }


_STUBBORN = (
    "import os, signal, subprocess, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "for _ in range(3):\n"
    "    subprocess.Popen([sys.executable, '-c', 'import signal, time; "
    "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(3600)'])\n"
    "time.sleep(3600)\n"
)


def stubborn(args: dict) -> dict:
    """B5: ignores SIGTERM, starts children that do too, and never ends."""
    subprocess.run([sys.executable, "-c", _STUBBORN])
    return {"finished": True}  # never reached under the wall clock


def forkbomb(args: dict) -> dict:
    """B6: forks until refused; reports how many it got."""
    count = 0
    for _ in range(int(args.get("tries", 1000))):
        try:
            pid = os.fork()
        except OSError as exc:
            return {"forked": count, "stopped_by": errno.errorcode.get(exc.errno or 0, str(exc.errno))}
        if pid == 0:
            time.sleep(5)
            os._exit(0)
        count += 1
    return {"forked": count, "stopped_by": None}


def flood(args: dict) -> dict:
    """B7: writes far more than the answer cap to stdout."""
    chunk = b"x" * (1 << 20)
    out = sys.stdout.buffer
    for _ in range(int(args.get("mib", 64))):
        out.write(chunk)
    out.flush()
    return {"flooded": True}


def disk(args: dict) -> dict:
    """B8: fills each writable place until refused, in 32 MiB files (each
    file is also under the 64 MiB per-file limit, so the place's own size is
    what stops it)."""
    out = {}
    budget = int(args.get("max_mib", 1024)) << 20
    chunk = bytes(1 << 20)  # 1 MiB of zeros
    for place in ("/work", "/lohome", "/dev/shm"):
        written, stopped_by = 0, None
        files: list[str] = []
        try:
            while written < budget and stopped_by is None:
                path = os.path.join(place, f"fill-{len(files)}")
                files.append(path)
                with open(path, "wb") as handle:
                    for _ in range(32):
                        handle.write(chunk)
                        handle.flush()
                        written += len(chunk)
        except OSError as exc:
            stopped_by = errno.errorcode.get(exc.errno or 0, str(exc.errno))
        out[place] = {"written_mib": written >> 20, "stopped_by": stopped_by}
        for path in files:
            try:
                os.unlink(path)
            except OSError:
                pass
    return out


def leave(args: dict) -> dict:
    """B9: what this job sees of itself, so a second job can be compared;
    it also leaves a file in /work, which the next job must not find."""
    name = f"left-by-job-{time.time_ns()}"
    listing_before = sorted(os.listdir("/work"))
    with open(f"/work/{name}", "wb") as handle:
        handle.write(b"x")
    return {
        "left": name,
        "work_before": listing_before,
        "pid": os.getpid(),
        "netns": os.readlink("/proc/self/ns/net"),
        "pidns": os.readlink("/proc/self/ns/pid"),
        "work_listing": sorted(os.listdir("/work")),
    }


def cgroup_escape(args: dict) -> dict:
    """B10: try to raise this job's own memory limit and leave its cgroup."""
    out = {}
    for label, path, value in args.get("attempts", []):
        try:
            with open(path, "w") as handle:
                handle.write(value)
            out[label] = "WROTE"
        except OSError as exc:
            out[label] = errno.errorcode.get(exc.errno or 0, str(exc.errno))
    return out


def sleep(args: dict) -> dict:
    """A11: stays alive a little so a second job can look for it."""
    time.sleep(float(args.get("seconds", 2)))
    return {"pid": os.getpid()}


PROGRAMS = {
    "probe_network": probe_network,
    "probe_view": probe_view,
    "escape": escape,
    "syscalls": syscalls,
    "x32": x32,
    "memory": memory,
    "cpu": cpu,
    "stubborn": stubborn,
    "forkbomb": forkbomb,
    "flood": flood,
    "disk": disk,
    "leave": leave,
    "cgroup_escape": cgroup_escape,
    "sleep": sleep,
}


def run(name: str, args: dict) -> dict:
    return {"outcome": "selftest", "program": name, "result": PROGRAMS[name](args)}
