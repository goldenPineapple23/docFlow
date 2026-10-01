"""
The seccomp filter every parse job loads before it reads its input (Stage
3c; founder's item 1 and change 1). Checked on a real Fly machine before
building, including loaded by the unprivileged job user under
no_new_privs (docs/spikes/3c-cgroup-check/, run 1).

Default allow, with these refused:
- the founder's list: unshare; clone with any new-namespace flag; setns;
  mount, umount2; ptrace; bpf; keyctl; perf_event_open; the module calls;
  kexec_load, kexec_file_load;
- the calls that do the same jobs by another name (the newer mount API,
  the rest of the key family, ptrace's relatives process_vm_readv/writev);
- io_uring_setup / io_uring_enter / io_uring_register (I/O performed by
  kernel threads the filter never sees) and userfaultfd (a common tool for
  winning kernel races) -- founder's change 1.

Each answers `errno_value` (EPERM as shipped). The tests swap in an errno no
real call returns, so a refusal is proven to come from the filter rather
than a missing privilege (test S1); the rule set is the same function.

clone3 answers ENOSYS: its flags live in a struct the filter can't read, so
it is refused in the way that makes the C library fall back to clone(),
whose flags the filter can read.

The filter is built for x86_64 only, with the "wrong architecture" action
set to kill the process, and libseccomp's x86_64 filter treats a call
number with the x32 bit (0x40000000) the same way: x32 calls report the
x86_64 architecture, so an architecture check alone wouldn't catch them
(founder's change 1; test S5 proves it on the real kernel).
"""

from __future__ import annotations

import errno

BLOCKED = (
    # founder's list
    "unshare",
    "setns",
    "mount",
    "umount2",
    "ptrace",
    "bpf",
    "keyctl",
    "perf_event_open",
    "init_module",
    "finit_module",
    "delete_module",
    "kexec_load",
    "kexec_file_load",
    # the same jobs by another name
    "fsopen",
    "fsconfig",
    "fsmount",
    "fspick",
    "move_mount",
    "open_tree",
    "mount_setattr",
    "add_key",
    "request_key",
    "process_vm_readv",
    "process_vm_writev",
    # founder's change 1
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
    "userfaultfd",
)

CLONE_NAMESPACE_FLAGS = {
    "CLONE_NEWNS": 0x00020000,
    "CLONE_NEWCGROUP": 0x02000000,
    "CLONE_NEWUTS": 0x04000000,
    "CLONE_NEWIPC": 0x08000000,
    "CLONE_NEWUSER": 0x10000000,
    "CLONE_NEWPID": 0x20000000,
    "CLONE_NEWNET": 0x40000000,
}

X32_SYSCALL_BIT = 0x40000000


def build(errno_value: int = errno.EPERM):
    """The filter, not yet loaded. Needs the libseccomp binding (the image's
    python3-seccomp); importing this module never needs it."""
    import seccomp

    flt = seccomp.SyscallFilter(defaction=seccomp.ALLOW)
    # Only the native architecture; any other is killed outright.
    flt.set_attr(seccomp.Attr.ACT_BADARCH, seccomp.KILL_PROCESS)
    for name in BLOCKED:
        flt.add_rule(seccomp.ERRNO(errno_value), name)
    for bit in CLONE_NAMESPACE_FLAGS.values():
        flt.add_rule(seccomp.ERRNO(errno_value), "clone", seccomp.Arg(0, seccomp.MASKED_EQ, bit, bit))
    flt.add_rule(seccomp.ERRNO(errno.ENOSYS), "clone3")
    return flt


def load(errno_value: int = errno.EPERM) -> None:
    build(errno_value).load()


def export_pfc() -> str:
    """The filter in libseccomp's readable form, for test S5."""
    import os
    import tempfile

    flt = build()
    with tempfile.TemporaryFile() as handle:
        flt.export_pfc(handle)
        handle.seek(0)
        return os.fsdecode(handle.read())
