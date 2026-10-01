"""
The root-owned first step inside a parse job's new namespaces (Stage 3c,
item 3, steps 3-6). Started by the launcher as

    unshare --net --mount --pid --ipc --uts --fork --mount-proc --kill-child \
        python3 -I -m parse_service.sandbox_init <slot> <kind> <end-record fd>

so it runs as root, as PID 1 of the job's PID namespace, already inside the
job's cgroups. It never reads the input (that waits on stdin for the job):
it builds the job's view of the machine, sets the backstop limits, starts
the unprivileged job, and stays as the namespace's PID 1: a minimal reaper,
itself unprivileged and under the job's seccomp filter (_reap_as_init).

The view (founder's item 3, Q7, D-150 spike findings):
- every existing mount read-only;
- an empty tmpfs over /sys and /.fly;
- a /dev of its own: null, zero, full, random, urandom, a size-capped
  /dev/shm, and nothing else -- no block device;
- the only writable places: /work (TMPDIR) and /lohome (HOME, LibreOffice's
  profile), each a size-capped tmpfs owned by the slot's user; /tmp and
  /var/tmp are /work/tmp bind-mounted (Q14), so the same tmpfs and cap. All
  of them noexec (founder, 2026-10-01; A15).

Any failure exits with status 70 before the job starts: the supervisor
answers that as the service failing to isolate, never as a verdict on the
file.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import resource
import stat
import sys

from parse_service import config

EXIT_ISOLATION_FAILED = 70
PR_SET_NO_NEW_PRIVS = 38
PR_SET_DUMPABLE = 4
PR_CAPBSET_DROP = 24
_LINUX_CAPABILITY_VERSION_3 = 0x20080522

MS_RDONLY = 0x1
MS_NOSUID = 0x2
MS_NODEV = 0x4
MS_NOEXEC = 0x8
MS_REMOUNT = 0x20
MS_BIND = 0x1000
MS_REC = 0x4000
MS_PRIVATE = 1 << 18

_libc = ctypes.CDLL(None, use_errno=True)


def _mount(source: str, target: str, fstype: str | None, flags: int, data: str | None = None) -> None:
    rc = _libc.mount(
        source.encode(),
        target.encode(),
        fstype.encode() if fstype else None,
        ctypes.c_ulong(flags),
        data.encode() if data else None,
    )
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"mount {target}: {os.strerror(err)}")


def _mountpoints() -> list[tuple[str, int]]:
    """(mount point, its per-mount flags) for every mount in this namespace."""
    out = []
    with open("/proc/self/mountinfo") as handle:
        for line in handle:
            fields = line.split()
            mountpoint = fields[4].replace("\\040", " ")
            options = fields[5].split(",")
            flags = 0
            flags |= MS_NOSUID if "nosuid" in options else 0
            flags |= MS_NODEV if "nodev" in options else 0
            flags |= MS_NOEXEC if "noexec" in options else 0
            out.append((mountpoint, flags))
    return out


def _read_only_everything() -> None:
    for mountpoint, flags in _mountpoints():
        if mountpoint == "/proc" or mountpoint.startswith("/proc/"):
            continue
        try:
            _mount("none", mountpoint, None, MS_REMOUNT | MS_BIND | MS_RDONLY | flags)
        except OSError:
            # A mount hidden under a later one can't be reached by path; it is
            # covered by what is mounted over it.
            if os.path.ismount(mountpoint):
                raise


def _tmpfs(target: str, size: int, mode: str, flags: int, owner: int | None = None) -> None:
    data = f"size={size},mode={mode}"
    if owner is not None:
        data += f",uid={owner},gid={owner}"
    _mount("tmpfs", target, "tmpfs", flags, data)


def _hide(path: str) -> None:
    if os.path.isdir(path):
        _tmpfs(path, 4096, "0555", MS_NOSUID | MS_NODEV | MS_NOEXEC)
        _mount("none", path, None, MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC)


_DEVICES = {"null": (1, 3), "zero": (1, 5), "full": (1, 7), "random": (1, 8), "urandom": (1, 9)}


def _minimal_dev() -> None:
    _tmpfs("/dev", 65536, "0755", MS_NOSUID | MS_NOEXEC)
    for name, (major, minor) in _DEVICES.items():
        path = f"/dev/{name}"
        os.mknod(path, 0o666 | stat.S_IFCHR, os.makedev(major, minor))
        os.chmod(path, 0o666)
    for name, target in (
        ("fd", "/proc/self/fd"),
        ("stdin", "/proc/self/fd/0"),
        ("stdout", "/proc/self/fd/1"),
        ("stderr", "/proc/self/fd/2"),
    ):
        os.symlink(target, f"/dev/{name}")
    os.mkdir("/dev/shm", 0o1777)
    _tmpfs("/dev/shm", config.SHM_TMPFS_BYTES, "1777", MS_NOSUID | MS_NODEV | MS_NOEXEC)
    _mount("none", "/dev", None, MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NOEXEC)


def _tmp_is_work() -> None:
    """/tmp and /var/tmp become the job's own /work/tmp (founder, 2026-10-01,
    Q14 option 2). LibreOffice's launcher insists on one of them for its pipe
    and exits otherwise. A bind mount, not a new tmpfs: the same /work tmpfs
    under its existing cap (B8), seen only in this job's mount namespace
    (A11); the rest of the root stays read-only (A14)."""
    for target in config.TMP_DIRS:
        _mount(f"{config.WORK_DIR}/tmp", target, None, MS_BIND)
        _mount("none", target, None, MS_REMOUNT | MS_BIND | MS_NOSUID | MS_NODEV | MS_NOEXEC)


def _limits() -> None:
    for which, value in (
        (resource.RLIMIT_AS, config.RLIMIT_AS_BYTES),
        (resource.RLIMIT_CPU, config.RLIMIT_CPU_SECONDS),
        (resource.RLIMIT_NPROC, config.RLIMIT_NPROC),
        (resource.RLIMIT_NOFILE, config.RLIMIT_NOFILE),
        (resource.RLIMIT_FSIZE, config.RLIMIT_FSIZE_BYTES),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(which, (value, value))


def _report(status_fd: int, record: dict) -> None:
    """One line on the launcher's private end-record pipe (never the job's)."""
    try:
        os.write(status_fd, (json.dumps(record) + "\n").encode())
    except OSError:
        pass


def main(argv: list[str]) -> int:
    slot, kind, status_fd = int(argv[1]), argv[2], int(argv[3])
    uid = config.SLOT_UID_BASE + slot
    try:
        _mount("none", "/", None, MS_REC | MS_PRIVATE)
        _read_only_everything()
        _hide("/sys")
        _hide("/.fly")
        _minimal_dev()
        _tmpfs(config.WORK_DIR, config.WORK_TMPFS_BYTES, "0700", MS_NOSUID | MS_NODEV | MS_NOEXEC, owner=uid)
        _tmpfs(config.HOME_DIR, config.HOME_TMPFS_BYTES, "0700", MS_NOSUID | MS_NODEV | MS_NOEXEC, owner=uid)
        os.mkdir(f"{config.WORK_DIR}/tmp", 0o700)
        os.chown(f"{config.WORK_DIR}/tmp", uid, uid)
        _tmp_is_work()
        _limits()
    except Exception as exc:  # noqa: BLE001 -- any failure here is the service's, not the file's
        print(f"sandbox_init failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        _report(status_fd, {"sandbox": "failed"})
        return EXIT_ISOLATION_FAILED

    # "off" here only for a self-test control that runs without the filter
    # (launcher._chain_env); nothing in a request can choose it.
    with_seccomp = os.environ.get("PARSE_ISOLATION", "on") == "on"
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": config.HOME_DIR,
        "TMPDIR": f"{config.WORK_DIR}/tmp",
        "LANG": "C.UTF-8",
        "PARSE_ISOLATION": "on" if with_seccomp else "off",
        "LIBREOFFICE_PATH": os.environ.get("LIBREOFFICE_PATH", ""),
    }
    job_argv = [sys.executable, "-I", "-m", "parse_service.job", kind]
    os.chdir(config.WORK_DIR)
    job = os.fork()
    if job == 0:
        try:
            _harden_and_confirm(uid, status_fd, with_seccomp)
            os.execve(job_argv[0], job_argv, env)
        except BaseException as exc:  # noqa: BLE001 -- no confirmation was sent: isolation failed
            print(f"job hardening failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        os._exit(EXIT_ISOLATION_FAILED)
    return _reap_as_init(job, uid, status_fd)


def _drop_to(uid: int) -> None:
    """Root to the slot's user, keeping nothing: no supplementary groups, an
    empty capability bounding set (dropped while CAP_SETPCAP is still held),
    then every uid and gid, then empty inheritable capabilities (setresuid
    has already cleared permitted, effective and ambient)."""
    os.setgroups([])
    for cap in range(64):
        if _libc.prctl(PR_CAPBSET_DROP, cap, 0, 0, 0) != 0:
            if ctypes.get_errno() == errno.EINVAL:
                break  # past the last capability this kernel knows
            raise OSError(ctypes.get_errno(), f"prctl(PR_CAPBSET_DROP, {cap})")
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)
    header = (ctypes.c_uint32 * 2)(_LINUX_CAPABILITY_VERSION_3, 0)
    data = (ctypes.c_uint32 * 6)(0, 0, 0, 0, 0, 0)
    if _libc.capset(header, data) != 0:
        raise OSError(ctypes.get_errno(), "capset")


def _proc_status() -> dict[str, str]:
    with open("/proc/self/status") as handle:
        return {k.strip(): v.strip() for k, _, v in (line.partition(":") for line in handle)}


def _harden_and_confirm(uid: int, status_fd: int, with_seccomp: bool) -> None:
    """
    In the forked child, before the parser exists (founder, 2026-10-01,
    Q13): drop to the slot's user, set no-new-privileges, load the seccomp
    filter, check all of it in /proc/self/status, and only then send the
    supervisor one "hardened" message on the private pipe -- and close this
    process's copy of the pipe, so the parser that replaces it never holds
    it. The supervisor accepts that first message only. No message means
    isolation failed (the document waits, the founder is alerted); with it,
    any later exit is the parser's, whatever the code, so a compromised
    parser can't fake an isolation failure. (Replaces `setpriv`, which did
    the same steps but couldn't confirm them before the parser started.)
    """
    _drop_to(uid)
    if _libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS)")
    if with_seccomp:
        from parse_service import seccomp_filter

        seccomp_filter.load()
    status = _proc_status()
    expected = {
        "Uid": f"{uid}\t{uid}\t{uid}\t{uid}",
        "Gid": f"{uid}\t{uid}\t{uid}\t{uid}",
        "Groups": "",
        "CapInh": "0000000000000000",
        "CapPrm": "0000000000000000",
        "CapEff": "0000000000000000",
        "CapBnd": "0000000000000000",
        "CapAmb": "0000000000000000",
        "NoNewPrivs": "1",
        "Seccomp": "2" if with_seccomp else "0",
    }
    wrong = {k: status.get(k) for k, v in expected.items() if status.get(k) != v}
    if wrong:
        raise RuntimeError(f"not hardened: {wrong}")
    os.write(status_fd, (json.dumps({"hardened": True, "seccomp": with_seccomp}) + "\n").encode())
    os.close(status_fd)


def _reap_as_init(job: int, uid: int, status_fd: int) -> int:
    """
    This process stays PID 1 of the job's namespace as a minimal reaper
    (founder, 2026-10-01): an orphan inside the sandbox, such as a process
    LibreOffice leaves behind, is reparented to PID 1, and the parser would
    never wait for it. First it drops to the slot's user under
    no-new-privileges and the same seccomp filter as the job; then it only
    waits. When the job exits it exits too, and its exit ends every other
    process in the namespace (the kernel kills them).

    How the job ended goes to the launcher as one record on a pipe only this
    process holds (`status_fd`; the job's copy is closed before it starts,
    and this process is non-dumpable, so the job can't reach it through
    /proc): {"exited": code} or {"signaled": N}. Never as this process's
    exit status, which a parser could imitate (founder, 2026-10-01): the
    launcher decides a job's end from its own records -- the cgroup's OOM
    count, its own kill decisions -- and this record only says how the
    parser ended when nothing else did.
    """
    try:
        devnull = os.open("/dev/null", os.O_RDWR)
        os.dup2(devnull, 0)  # the job holds the pipes; this process never reads or writes them
        os.dup2(devnull, 1)
        _drop_to(uid)
        if _libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_DUMPABLE)")
        if _libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS)")
        from parse_service import seccomp_filter

        seccomp_filter.load()
    except Exception as exc:  # noqa: BLE001 -- never leave a root process beside the job
        print(f"sandbox_init reaper failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        os.kill(job, 9)
        _report(status_fd, {"sandbox": "failed"})
        return EXIT_ISOLATION_FAILED
    while True:
        try:
            pid, status = os.wait()
        except ChildProcessError:
            _report(status_fd, {"sandbox": "failed"})  # the job gone without a status: not possible
            return EXIT_ISOLATION_FAILED
        except InterruptedError:
            continue
        if pid != job:
            continue  # an orphan, now reaped
        if os.WIFSIGNALED(status):
            _report(status_fd, {"signaled": os.WTERMSIG(status)})
        else:
            _report(status_fd, {"exited": os.waitstatus_to_exitcode(status)})
        return 0  # the record above is the job's end; this status means nothing


if __name__ == "__main__":
    sys.exit(main(sys.argv))
