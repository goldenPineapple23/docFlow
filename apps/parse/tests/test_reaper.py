"""
The orphan reaper (B5/B11): which processes it waits for, and that it never
takes a job's own exit status (founder, 2026-10-01, after Fly run 1).

Logic only. The proof that killed jobs' orphans are reaped on Fly's cgroup
v1 is B5 and B11 on the Fly machine (RUNBOOK 8.1).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from parse_service import config, launcher

linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the reaper is Linux only (subreaper, SIGCHLD, /proc); CI's unit job runs these",
)

OWN_NS = "pid:[4026531836]"
JOB_NS = "pid:[4026532245]"
ROOT_CGROUP_V1 = ["10:cpuset:/", "8:memory:/", "5:cpu,cpuacct:/", "3:pids:/", "0::/"]


def _facts(pid: int, *, state: str, uid: int, pid_ns: str, ppid: str | None = None) -> dict:
    return {
        "pid": pid,
        "name": "python",
        "state": state,
        "ppid": ppid if ppid is not None else str(os.getpid()),
        "uid": uid,
        "cgroup": ROOT_CGROUP_V1,
        "pid_ns": pid_ns,
    }


@pytest.fixture
def fake_proc(monkeypatch):
    """A /proc as Fly run 1 saw it: dead sandbox processes shown in "/"."""
    table = {
        # The sandbox's PID 1 of a killed slot-0 job: must be reaped.
        501: _facts(501, state="Z (zombie)", uid=config.SLOT_UID_BASE, pid_ns=JOB_NS),
        # A self-test's setpriv child: a slot's user, but in our namespace.
        502: _facts(502, state="Z (zombie)", uid=config.SLOT_UID_BASE, pid_ns=OWN_NS),
        # Another namespace, but not a slot's user.
        503: _facts(503, state="Z (zombie)", uid=0, pid_ns=JOB_NS),
        # Someone else's child.
        504: _facts(504, state="Z (zombie)", uid=config.SLOT_UID_BASE, pid_ns=JOB_NS, ppid="1"),
        # A live job's own process (registered): never ours to wait for.
        505: _facts(505, state="Z (zombie)", uid=config.SLOT_UID_BASE + 1, pid_ns=JOB_NS),
        # A sandbox process still running: found, not waited for.
        506: _facts(506, state="S (sleeping)", uid=config.SLOT_UID_BASE, pid_ns=JOB_NS),
    }
    real_listdir = os.listdir
    monkeypatch.setattr(os, "listdir", lambda path: [str(p) for p in table] if path == "/proc" else real_listdir(path))
    monkeypatch.setattr(launcher, "process_facts", lambda pid: dict(table[pid]))
    monkeypatch.setattr(launcher, "_own_pid_ns", OWN_NS)
    monkeypatch.setattr(launcher, "_live_children", {505})
    waited: list[int] = []

    def waitpid(pid: int, _options: int) -> tuple[int, int]:
        waited.append(pid)
        return pid, 9

    monkeypatch.setattr(os, "waitpid", waitpid)
    monkeypatch.setattr(os, "WNOHANG", 1, raising=False)  # absent on Windows
    monkeypatch.setattr(launcher, "BACKSTOP_FOUND", [])
    return waited


def test_an_orphan_the_kernel_shows_in_the_root_cgroup_is_still_reaped(fake_proc):
    with launcher._children_lock:
        reaped = launcher._reap_zombie_orphans(launcher.SLOT_UIDS)
    assert [f["pid"] for f in reaped] == [501]
    assert fake_proc == [501]


def test_the_orphans_are_the_sandbox_processes_of_ours_that_no_live_job_owns(fake_proc):
    with launcher._children_lock:
        found = launcher._orphans(launcher.SLOT_UIDS)
    assert sorted(f["pid"] for f in found) == [501, 506]


def test_the_backstop_looks_only_at_its_own_slots_user(fake_proc):
    assert launcher._backstop(1) == []  # slot 1's only process is a live job's
    found = launcher._backstop(0)
    assert sorted(f["pid"] for f in found) == [501, 506]
    assert {f["pid"]: f["reaped_by_backstop"] for f in found} == {501: True, 506: False}
    assert len(launcher.BACKSTOP_FOUND) == 2


def _state(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/status") as handle:
            for line in handle:
                if line.startswith("State:"):
                    return line.split()[1]
    except OSError:
        return "gone"
    return "?"


def _wait_until_zombie(pid: int) -> None:
    deadline = time.monotonic() + 5
    while _state(pid) != "Z" and time.monotonic() < deadline:
        time.sleep(0.001)
    assert _state(pid) == "Z"


@pytest.fixture
def reaper_matching_every_child(monkeypatch):
    """The reaper running, and treating every child of ours as a sandbox
    orphan, so only the registration in _live_children protects a job."""
    launcher.start_reaper()
    monkeypatch.setattr(launcher, "_from_a_sandbox", lambda _facts, _uids: True)


SETTINGS = config.Settings(production=False, isolation=False, token="", port=0, libreoffice_path="", test_only=())


@linux_only
def test_a_job_that_dies_before_it_is_registered_keeps_its_own_exit_status(reaper_matching_every_child, monkeypatch):
    real_popen = subprocess.Popen

    def dying_at_once(_argv, **kwargs):
        # Called with _children_lock held, as run_job creates every job. The
        # process is dead (a zombie, SIGCHLD sent) before Popen even returns,
        # and the reaper has had its wakeup before the job is registered.
        proc = real_popen(["/bin/sh", "-c", "exit 7"], **kwargs)
        _wait_until_zombie(proc.pid)
        time.sleep(0.2)
        return proc

    monkeypatch.setattr(subprocess, "Popen", dying_at_once)
    for _ in range(5):
        result = launcher.run_job("table", {}, b"", slot=0, settings=SETTINGS, cgroups=None)
        assert (result.outcome, result.cause, result.evidence["exit_status"]) == ("crashed", "exit_7", 7)


@linux_only
def test_control_an_unregistered_child_would_lose_its_exit_status(reaper_matching_every_child):
    """The same dead child, never registered: the reaper takes it, and Python
    then reports exit 0. This is what the lock prevents."""
    proc = subprocess.Popen(["/bin/sh", "-c", "exit 7"])
    deadline = time.monotonic() + 5
    while proc.pid not in [f["pid"] for f in launcher.RECENT_REAPS] and time.monotonic() < deadline:
        time.sleep(0.01)  # the reaper thread takes it on its SIGCHLD
    assert proc.pid in [f["pid"] for f in launcher.RECENT_REAPS]
    assert proc.wait(timeout=5) == 0


@linux_only
def test_a_real_orphan_outside_any_job_cgroup_is_reaped_on_sigchld(reaper_matching_every_child):
    """A real orphan reparented to this process (the subreaper), in whatever
    cgroup the runner gives it: reaped when it ends, not left a zombie."""
    shell = subprocess.run(
        ["/bin/sh", "-c", "sleep 0.5 >/dev/null 2>&1 & echo $!"], capture_output=True, text=True, timeout=10
    )
    orphan = int(shell.stdout.strip())
    deadline = time.monotonic() + 10
    while _state(orphan) != "gone" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _state(orphan) == "gone"
    assert orphan in [f["pid"] for f in launcher.RECENT_REAPS]
