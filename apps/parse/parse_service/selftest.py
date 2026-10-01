"""
The parse service's self-tests and its startup canary (Stage 3c; the 3c
test table's A, S, B and E3 rows).

Run as root on the machine, from a shell -- `docker run ... selftest` in
CI, `fly ssh console` on Fly -- never over HTTP:

    python -m parse_service.selftest all [--targets targets.json]

Every check runs its control first or beside it (RUNBOOK 1.7): the same
probe outside the sandbox, or the same program without the cap, so a pass
can't come from a probe that would have failed anyway. Each line reports
its own evidence:

    RESULT <id> PASS|FAIL|NOT-RUN|INFO -- <evidence>

NOT-RUN means the control itself didn't hold (for example, no IPv6 outside
the sandbox on a CI runner), so the check proved nothing on this machine: it
did not run as a test, and it is never counted or reported as a pass
(founder, 2026-10-01). IPv6 isolation is proven only by the Fly run (RUNBOOK
8.1), where the machine has IPv6. The exit status is 1 if any check FAILed.

`canary()` is the subset the service runs at startup before it opens its
port (item 4).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from parse_service import config, selftest_programs
from parse_service.cgroups import Cgroups, active_swap
from parse_service.launcher import JobResult, Limits, become_subreaper, process_facts, run_job

EXPECTED_DEV = {"null", "zero", "full", "random", "urandom", "shm", "fd", "stdin", "stdout", "stderr"}
ALLOWED_JOB_ENV = {"PATH", "HOME", "TMPDIR", "LANG", "PARSE_ISOLATION", "LIBREOFFICE_PATH", "LC_CTYPE"}
DOCFLOW_SETTING_NAMES = (
    "PARSE_SERVICE_TOKEN",
    "DATABASE_URL",
    "REDIS_URL",
    "ANTHROPIC_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_JWT_SECRET",
    "STORAGE_S3_ENDPOINT",
    "STORAGE_S3_REGION",
    "STORAGE_S3_ACCESS_KEY_ID",
    "STORAGE_S3_SECRET_ACCESS_KEY",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "POSTMARK_WEBHOOK_USERNAME",
    "POSTMARK_WEBHOOK_PASSWORD",
    "EMAIL_PROVIDER_API_KEY",
    "DOCUMENT_URL_SIGNING_SECRET",
    "SESSION_SECRET",
)
EROFS_PLACES = ("/", "/usr", "/etc", "/tmp", "/opt")
WRITABLE_PLACES = ("/work", "/lohome")


@dataclass
class Check:
    id: str
    verdict: str  # PASS | FAIL | NOT-RUN | INFO
    detail: object

    def line(self) -> str:
        return f"RESULT {self.id} {self.verdict} -- {json.dumps(self.detail, default=str)}"


def _verdict(ok: bool, control_ok: bool = True) -> str:
    if not control_ok:
        return "NOT-RUN"
    return "PASS" if ok else "FAIL"


class Runner:
    def __init__(self, settings: config.Settings, cgroups: Cgroups):
        self.settings = settings
        self.cgroups = cgroups
        self.checks: list[Check] = []

    def add(self, check: Check) -> Check:
        self.checks.append(check)
        print(check.line(), flush=True)
        return check

    def job(self, program: str, args: dict | None = None, *, slot: int = 0, **kwargs) -> JobResult:
        return run_job(
            f"selftest:{program}",
            {"args": args or {}},
            b"",
            slot=slot,
            settings=self.settings,
            cgroups=self.cgroups,
            **kwargs,
        )

    @staticmethod
    def result(job: JobResult) -> dict:
        return (job.answer or {}).get("result") or {}

    # ── A: isolation ───────────────────────────────────────────────────────

    def network(self, targets: dict) -> None:
        outside = selftest_programs.probe_network(targets)
        inside_job = self.job("probe_network", targets)
        inside = self.result(inside_job)
        for label, _host, _port, _v in targets["tcp"]:
            control = outside["tcp"].get(label)
            got = inside.get("tcp", {}).get(label, "no answer")
            self.add(
                Check(
                    f"A-net:{label}",
                    _verdict(got.startswith("blocked"), control == "reached"),
                    {"outside": control, "inside": got},
                )
            )
        for name in targets.get("dns", []):
            control = outside["dns"].get(name)
            got = inside.get("dns", {}).get(name, "no answer")
            self.add(
                Check(
                    f"A2:dns:{name}",
                    _verdict(got.startswith("blocked"), control == "resolved"),
                    {"outside": control, "inside": got},
                )
            )
        interfaces = inside.get("interfaces")
        self.add(
            Check(
                "A8:interfaces",
                _verdict(interfaces == ["lo(down)"], len(outside.get("interfaces", [])) > 1),
                {"outside": outside.get("interfaces"), "inside": interfaces},
            )
        )

    def view(self) -> None:
        outside = selftest_programs.probe_view({})
        job = self.job("probe_view")
        inside = self.result(job)
        if not inside:
            self.add(Check("A-view", "FAIL", {"job": job.outcome, "cause": job.cause, "evidence": job.evidence}))
            return
        fly = inside.get("fly_dir")
        self.add(
            Check(
                "A8:/.fly-and-/sys",
                _verdict(fly in ("absent", []) and inside.get("sys_dir") == [], bool(outside.get("sys_dir"))),
                {
                    "outside": {"fly": outside.get("fly_dir"), "sys": len(outside.get("sys_dir") or [])},
                    "inside": {"fly": fly, "sys": inside.get("sys_dir")},
                },
            )
        )
        identity_ok = (
            inside.get("uid") == config.SLOT_UID_BASE
            and inside.get("gid") == config.SLOT_UID_BASE
            and inside.get("groups") in ([], [config.SLOT_UID_BASE])
            and int(inside.get("CapEff", "1"), 16) == 0
            and int(inside.get("CapBnd", "1"), 16) == 0
            and int(inside.get("CapPrm", "1"), 16) == 0
            and inside.get("NoNewPrivs") == "1"
            and inside.get("Seccomp") == "2"
        )
        self.add(
            Check(
                "A9:identity",
                _verdict(identity_ok, outside.get("uid") == 0),
                {
                    "outside_uid": outside.get("uid"),
                    "inside": {
                        k: inside.get(k)
                        for k in ("uid", "gid", "groups", "CapEff", "CapBnd", "CapPrm", "NoNewPrivs", "Seccomp")
                    },
                },
            )
        )
        env_names = set(inside.get("env_names", []))
        leaked = sorted(env_names & set(DOCFLOW_SETTING_NAMES))
        outside_has_token = "PARSE_SERVICE_TOKEN" in os.environ
        self.add(
            Check(
                "A12:environment",
                _verdict(not leaked and env_names <= ALLOWED_JOB_ENV, True),
                {
                    "inside": sorted(env_names),
                    "docflow_names_inside": leaked,
                    "outside_has_parse_token": outside_has_token,
                },
            )
        )
        dev = inside.get("dev", {})
        outside_blocks = sorted(n for n, k in outside.get("dev", {}).items() if k == "block")
        self.add(
            Check(
                "A13:/dev",
                _verdict("block" not in dev.values() and set(dev) == EXPECTED_DEV, bool(outside_blocks)),
                {"outside_block_devices": outside_blocks[:12], "inside": dev},
            )
        )
        writes = inside.get("writes", {})
        ro_ok = all(writes.get(p) == "EROFS" for p in EROFS_PLACES)
        rw_ok = all(writes.get(p) == "writable" for p in WRITABLE_PLACES)
        outside_writes = outside.get("writes", {})
        self.add(
            Check(
                "A14:read-only-root",
                _verdict(ro_ok and rw_ok, outside_writes.get("/opt") == "writable"),
                {"outside": outside_writes, "inside": writes},
            )
        )

    def escape(self) -> None:
        control_cmd = [
            "unshare",
            "--mount",
            "--net",
            "--uts",
            "--fork",
            sys.executable,
            "-I",
            "-c",
            "import json; from parse_service import selftest_programs as p; print(json.dumps(p.escape({})))",
        ]
        control = json.loads(subprocess.run(control_cmd, capture_output=True, text=True, timeout=30).stdout or "{}")
        inside = self.result(self.job("escape"))
        ok = bool(inside) and all(not str(v).startswith("ok") for v in inside.values())
        self.add(
            Check(
                "A10:no-way-out",
                _verdict(ok, all(str(v).startswith("ok") for v in control.values()) and bool(control)),
                {"control_root_in_throwaway_namespace": control, "inside": inside},
            )
        )

    def concurrent(self) -> None:
        first = {}

        def hold() -> None:
            first["job"] = self.job("sleep", {"seconds": 4}, slot=0)

        thread = threading.Thread(target=hold)
        thread.start()
        time.sleep(1.5)
        second = self.result(self.job("probe_view", slot=1))
        thread.join()
        visible = second.get("processes_visible", [])
        ok = (
            second.get("uid") == config.SLOT_UID_BASE + 1
            and len(visible) <= 3
            and second.get("work_listing") == ["tmp"]
        )
        self.add(
            Check(
                "A11:concurrent-jobs",
                _verdict(ok),
                {
                    "second_job_sees_processes": visible,
                    "second_job_work": second.get("work_listing"),
                    "first": _outcome(first.get("job")),
                },
            )
        )

    # ── S: seccomp ─────────────────────────────────────────────────────────

    def seccomp(self) -> None:
        from parse_service import seccomp_filter

        sentinel = self.result(self.job("syscalls", {"sentinel": True}))
        calls = sentinel.get("calls", {})
        self.add(
            Check(
                "S1:filter-answers-every-listed-call",
                _verdict(bool(calls) and all(v == "FILTER" for v in calls.values())),
                calls,
            )
        )
        shipped = self.result(self.job("syscalls"))
        shipped_calls = shipped.get("calls", {})
        self.add(
            Check(
                "S2:shipped-filter",
                _verdict(
                    bool(shipped_calls)
                    and all(not v.startswith("ok") for v in shipped_calls.values())
                    and shipped.get("Seccomp") == "2"
                    and shipped.get("NoNewPrivs") == "1"
                ),
                {"calls": shipped_calls, "Seccomp": shipped.get("Seccomp"), "NoNewPrivs": shipped.get("NoNewPrivs")},
            )
        )
        control = self.result(self.job("syscalls", seccomp=False))
        self.add(
            Check(
                "S3:without-the-filter (control)",
                "INFO",
                {
                    "Seccomp": control.get("Seccomp"),
                    "only_the_filter_stops": sorted(
                        k for k, v in control.get("calls", {}).items() if str(v).startswith("ok")
                    ),
                    "calls": control.get("calls"),
                },
            )
        )
        self.add(
            Check(
                "S4:normal-work-under-the-filter",
                _verdict(
                    shipped.get("clone3") == "ENOSYS"
                    and shipped.get("thread") == "ran"
                    and shipped.get("fork") == "ran"
                ),
                {k: shipped.get(k) for k in ("clone3", "thread", "fork")},
            )
        )
        pfc = seccomp_filter.export_pfc()
        arch_lines = [line.strip() for line in pfc.splitlines() if "arch" in line.lower()][:6]
        x32_job = self.job("x32")
        x32_control = self.job("x32", seccomp=False)
        killed = x32_job.outcome == "crashed" and x32_job.cause == "signal_31"
        self.add(
            Check(
                "S5:architecture",
                _verdict(killed and "x86_64" in pfc and "x86\n" not in pfc and "aarch64" not in pfc),
                {
                    "x32_under_filter": {"outcome": x32_job.outcome, "cause": x32_job.cause},
                    "x32_without_filter": self.result(x32_control) or x32_control.outcome,
                    "filter_arch_lines": arch_lines,
                },
            )
        )

    # ── B: limits ──────────────────────────────────────────────────────────

    def _swap_ok(self, job: JobResult) -> tuple[bool, dict]:
        limits = job.evidence.get("limits", {})
        memory = limits.get("memory")
        swap = limits.get("memory_swap", "absent")
        swap_set = swap == memory or swap == "swap.max 0"
        no_swap = not active_swap()
        return (swap_set or no_swap) and (swap_set or not job.evidence.get("swap_limited")), {
            "memory": memory,
            "memory_swap": swap,
            "machine_has_active_swap": not no_swap,
        }

    def memory(self) -> None:
        small = Limits().lowered(memory_bytes=256 * config.MIB)
        one = self.job("memory", {"processes": 1, "mib": 400}, limits=small)
        one_control = self.job("memory", {"processes": 1, "mib": 400}, use_cgroup=False)
        swap_ok, swap = self._swap_ok(one)
        self.add(
            Check(
                "B1:memory-one-process",
                _verdict(
                    one.outcome == "stopped" and one.cause == "memory" and swap_ok,
                    self.result(one_control).get("survived") == 1,
                ),
                {
                    "with_cap": {"outcome": one.outcome, "cause": one.cause, **one.evidence},
                    "control_without_cap": self.result(one_control),
                    "swap": swap,
                },
            )
        )
        family = self.job("memory", {"processes": 4, "mib": 120}, limits=small)
        family_control = self.job("memory", {"processes": 4, "mib": 120}, use_cgroup=False)
        swap_ok, swap = self._swap_ok(family)
        self.add(
            Check(
                "B2:memory-family-together",
                _verdict(
                    family.outcome == "stopped" and family.cause == "memory" and swap_ok,
                    self.result(family_control).get("survived") == 4,
                ),
                {
                    "with_cap": {"outcome": family.outcome, "cause": family.cause, **family.evidence},
                    "control_setrlimit_only": self.result(family_control),
                    "swap": swap,
                },
            )
        )
        self.add(
            Check(
                "B3:one-oom-ends-the-whole-job",
                _verdict(
                    family.evidence.get("oom_kills", 0) >= 1
                    and family.evidence.get("processes_left_before_cleanup") == 0
                    and family.evidence.get("cgroup_removed") is True
                    and swap_ok
                ),
                {k: family.evidence.get(k) for k in ("oom_kills", "processes_left_before_cleanup", "cgroup_removed")},
            )
        )

    def cpu(self) -> None:
        budget = Limits().lowered(cpu_budget_seconds=5)
        family = self.job("cpu", {"processes": 3, "seconds": 60}, limits=budget)
        control = self.job("cpu", {"processes": 1, "seconds": 2}, limits=budget)
        self.add(
            Check(
                "B4:cpu-budget-kills",
                _verdict(
                    family.outcome == "stopped" and family.cause == "cpu" and family.seconds < 30,
                    control.outcome == "selftest",
                ),
                {
                    "family": {
                        "outcome": family.outcome,
                        "cause": family.cause,
                        "seconds": family.seconds,
                        "cpu_seconds": family.evidence.get("cpu_seconds"),
                    },
                    "control_one_process_under_budget": {
                        "outcome": control.outcome,
                        "cpu_seconds": control.evidence.get("cpu_seconds"),
                    },
                },
            )
        )
        other: dict = {}

        def neighbour() -> None:
            time.sleep(1)
            other["job"] = self.job("sleep", {"seconds": 1}, slot=1)

        thread = threading.Thread(target=neighbour)
        thread.start()
        quota = self.job("cpu", {"processes": 2, "seconds": 6})
        thread.join()
        unlimited = self.job("cpu", {"processes": 2, "seconds": 6}, use_cgroup=False)
        quota_result = self.result(quota)
        unlimited_result = self.result(unlimited)
        wall = quota_result.get("wall_seconds") or 1
        used = quota.evidence.get("cpu_seconds") or 0
        control_ratio = (unlimited_result.get("children_cpu_seconds") or 0) / (
            unlimited_result.get("wall_seconds") or 1
        )
        self.add(
            Check(
                "B12:cpu-quota-slows",
                _verdict(
                    used <= wall * 1.15 * config.JOB_CPU_QUOTA_CPUS
                    and quota.evidence.get("throttled_periods", 0) > 0
                    and other.get("job") is not None
                    and other["job"].outcome == "selftest",
                    control_ratio > 1.3,
                ),
                {
                    "with_quota": {
                        "wall": wall,
                        "cpu_seconds": used,
                        "throttled_periods": quota.evidence.get("throttled_periods"),
                        "quota": quota.evidence.get("limits", {}).get("cpu_quota"),
                    },
                    "control_cpus_used": round(control_ratio, 2),
                    "other_slot_finished": _outcome(other.get("job")),
                },
            )
        )

    def wall_clock(self) -> None:
        job = self.job("stubborn", limits=Limits().lowered(wall_seconds=5))
        left = _processes_of(config.SLOT_UID_BASE)
        self.add(
            Check(
                "B5:wall-clock-kills-everything",
                _verdict(
                    job.outcome == "stopped"
                    and job.cause == "wall_clock"
                    and job.evidence.get("processes_left_before_cleanup") == 0
                    and not left
                ),
                {
                    "outcome": job.outcome,
                    "cause": job.cause,
                    "seconds": job.seconds,
                    "slot_user_processes_after": left,
                    "left_details": _describe(left),
                    "pid1": _pid1(),
                    "supervisor_pid_ns": _supervisor_pid_ns(),
                    **job.evidence,
                },
            )
        )

    def forkbomb(self) -> None:
        job = self.job("forkbomb", {"tries": 1000}, limits=Limits().lowered(pids_max=64))
        result = self.result(job)
        self.add(
            Check(
                "B6:fork-bomb",
                _verdict(
                    job.outcome == "selftest"
                    and result.get("forked", 999) < 64
                    and result.get("stopped_by") == "EAGAIN"
                ),
                {"result": result, "pids_limit": job.evidence.get("limits", {}).get("pids")},
            )
        )

    def flood(self) -> None:
        job = self.job("flood", {"mib": 64}, limits=Limits().lowered(answer_cap_bytes=8 * config.MIB))
        self.add(
            Check(
                "B7:answer-cap",
                _verdict(job.outcome == "stopped" and job.cause == "output_too_large"),
                {"outcome": job.outcome, "cause": job.cause, "seconds": job.seconds},
            )
        )

    def disk(self) -> None:
        job = self.job("disk", {"max_mib": 1024})
        result = self.result(job)
        sizes = {
            "/work": config.WORK_TMPFS_BYTES,
            "/lohome": config.HOME_TMPFS_BYTES,
            "/dev/shm": config.SHM_TMPFS_BYTES,
        }
        ok = job.outcome == "selftest" and all(
            result.get(p, {}).get("stopped_by") == "ENOSPC" and (result[p]["written_mib"] << 20) <= size
            for p, size in sizes.items()
        )
        counted = self.job("disk", {"max_mib": 1024}, limits=Limits().lowered(memory_bytes=160 * config.MIB))
        counted_result = self.result(counted)
        counted_ok = (counted.outcome == "stopped" and counted.cause == "memory") or (
            counted.outcome == "selftest"
            and counted_result.get("/work", {}).get("written_mib", 999) < 160
            and counted_result.get("/work", {}).get("stopped_by") in ("ENOMEM", "ENOSPC")
        )
        self.add(Check("B8:disk-sizes", _verdict(ok), result or {"outcome": job.outcome, "cause": job.cause}))
        self.add(
            Check(
                "B8:tmpfs-counts-against-job-memory",
                _verdict(counted_ok),
                {"memory_limit_mib": 160, "outcome": counted.outcome, "cause": counted.cause, "result": counted_result},
            )
        )

    def one_per_file(self) -> None:
        first = self.job("leave")
        second = self.job("leave")
        a, b = self.result(first), self.result(second)
        self.add(
            Check(
                "B9:one-process-per-file",
                _verdict(
                    first.outcome == second.outcome == "selftest"
                    and a.get("work_before") == ["tmp"]
                    and b.get("work_before") == ["tmp"]
                    and a.get("left") not in b.get("work_listing", [])
                ),
                {"first": a, "second": b},
            )
        )

    def cgroup_escape(self) -> None:
        job_cg = self.cgroups.create_job("selftest-b10", memory_bytes=256 * config.MIB, pids_max=64, cpu_quota_cpus=1)
        try:
            limit_file = job_cg.memory / ("memory.limit_in_bytes" if job_cg.version == 1 else "memory.max")
            parent_procs = job_cg.memory.parent / "cgroup.procs"
            hidden = self.result(
                self.job(
                    "cgroup_escape",
                    {"attempts": [["raise_limit", str(limit_file), "-1"], ["leave", str(parent_procs), "1"]]},
                )
            )
            visible_cmd = [
                "/usr/bin/setpriv",
                f"--reuid={config.SLOT_UID_BASE}",
                f"--regid={config.SLOT_UID_BASE}",
                "--clear-groups",
                "--inh-caps=-all",
                "--bounding-set=-all",
                "--no-new-privs",
                sys.executable,
                "-I",
                "-c",
                "import json, sys\nfrom parse_service import selftest_programs as p\n"
                "print(json.dumps(p.cgroup_escape(json.loads(sys.argv[1]))))",
                json.dumps({"attempts": [["raise_limit", str(limit_file), "-1"], ["leave", str(parent_procs), "1"]]}),
            ]
            visible = json.loads(subprocess.run(visible_cmd, capture_output=True, text=True, timeout=30).stdout or "{}")
            root_can = True
            try:
                limit_file.write_text(str(300 * config.MIB))
            except OSError:
                root_can = False
        finally:
            job_cg.remove()
        self.add(
            Check(
                "B10:cannot-raise-or-leave-its-cgroup",
                _verdict(
                    all(v == "ENOENT" for v in hidden.values())
                    and bool(hidden)
                    and all(v in ("EACCES", "EPERM") for v in visible.values())
                    and bool(visible),
                    root_can,
                ),
                {"inside_sandbox": hidden, "slot_user_with_files_visible": visible, "control_root_can_write": root_can},
            )
        )

    def no_leak(self, count: int = 100) -> None:
        outcomes: dict[str, int] = {}
        for i in range(count):
            job = self.job("sleep", {"seconds": 0}, slot=i % config.PARSE_SLOTS)
            outcomes[job.outcome] = outcomes.get(job.outcome, 0) + 1
        leftover = self.cgroups.job_dirs()
        left = _processes_of(config.SLOT_UID_BASE) + _processes_of(config.SLOT_UID_BASE + 1)
        self.add(
            Check(
                "B11:no-leak",
                _verdict(outcomes == {"selftest": count} and not leftover and not left),
                {
                    "jobs": outcomes,
                    "job_cgroups_left": leftover,
                    "slot_user_processes_left": left,
                    "left_details": _describe(left),
                    "pid1": _pid1(),
                    "supervisor_pid_ns": _supervisor_pid_ns(),
                },
            )
        )

    def libreoffice(self) -> None:
        job = self.job("libreoffice")
        result = self.result(job)
        self.add(
            Check(
                "B13:libreoffice-converts-in-the-sandbox",
                _verdict(
                    job.outcome == "selftest"
                    and result.get("as_conversion_py", {}).get("exit_status") == 0
                    and bool(result.get("as_conversion_py", {}).get("produced"))
                ),
                {"outcome": job.outcome, "cause": job.cause, "result": result, **job.evidence},
            )
        )

    # ── the canary (item 4) ────────────────────────────────────────────────

    def canary(self) -> bool:
        targets = default_targets()
        net = self.result(self.job("probe_network", targets))
        net_ok = (
            bool(net)
            and all(v.startswith("blocked") for v in net.get("tcp", {}).values())
            and all(v.startswith("blocked") for v in net.get("dns", {}).values())
            and net.get("interfaces") == ["lo(down)"]
        )
        self.add(Check("canary:network", _verdict(net_ok), net))
        view = self.result(self.job("probe_view"))
        view_ok = (
            bool(view)
            and view.get("fly_dir") in ("absent", [])
            and view.get("sys_dir") == []
            and "block" not in view.get("dev", {}).values()
            and all(view.get("writes", {}).get(p) == "EROFS" for p in EROFS_PLACES)
            and view.get("uid") == config.SLOT_UID_BASE
            and int(view.get("CapEff", "1"), 16) == 0
            and view.get("NoNewPrivs") == "1"
            and view.get("Seccomp") == "2"
            and not set(view.get("env_names", [])) & set(DOCFLOW_SETTING_NAMES)
        )
        self.add(
            Check(
                "canary:view",
                _verdict(view_ok),
                {k: view.get(k) for k in ("uid", "CapEff", "NoNewPrivs", "Seccomp", "fly_dir", "sys_dir", "writes")},
            )
        )
        calls = self.result(self.job("syscalls")).get("calls", {})
        sys_ok = bool(calls) and all(not v.startswith("ok") for v in calls.values())
        self.add(
            Check(
                "canary:seccomp",
                _verdict(sys_ok),
                {k: calls.get(k) for k in ("unshare(NEWUSER)", "mount", "keyctl", "io_uring_setup", "userfaultfd")},
            )
        )
        mem = self.job("memory", {"processes": 1, "mib": 128}, limits=Limits().lowered(memory_bytes=64 * config.MIB))
        swap_ok, swap = self._swap_ok(mem)
        self.add(
            Check(
                "canary:memory-cap-kills",
                _verdict(mem.outcome == "stopped" and mem.cause == "memory" and swap_ok),
                {"outcome": mem.outcome, "cause": mem.cause, "swap": swap, "cgroup_version": self.cgroups.version},
            )
        )
        cpu = self.job("cpu", {"processes": 2, "seconds": 30}, limits=Limits().lowered(cpu_budget_seconds=1.5))
        quota = cpu.evidence.get("limits", {}).get("cpu_quota", "")
        self.add(
            Check(
                "canary:cpu-quota-set-and-budget-kills",
                _verdict(
                    cpu.outcome == "stopped"
                    and cpu.cause == "cpu"
                    and quota.startswith(str(100_000 * config.JOB_CPU_QUOTA_CPUS))
                ),
                {"outcome": cpu.outcome, "cause": cpu.cause, "quota": quota},
            )
        )
        return all(c.verdict == "PASS" for c in self.checks if c.id.startswith("canary:"))


def _outcome(job: JobResult | None) -> str | None:
    return job.outcome if job is not None else None


def _processes_of(uid: int) -> list[int]:
    out = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text()
        except OSError:
            continue
        for line in status.splitlines():
            if line.startswith("Uid:") and int(line.split()[1]) == uid:
                out.append(int(entry.name))
    return out


def _describe(pids: list[int]) -> list[dict]:
    """Name, state, parent, cgroup and PID namespace of each process, so a
    leak says what it is and where it lives (founder, 2026-10-01): one still
    in a job's cgroup or PID namespace is a cleanup bug; one outside both
    escaped (a B10 failure)."""
    return [process_facts(pid) for pid in pids]


def _supervisor_pid_ns() -> str:
    try:
        return os.readlink("/proc/self/ns/pid")
    except OSError as exc:
        return type(exc).__name__


def _pid1() -> str:
    """What is reaping orphans in this PID namespace."""
    try:
        return Path("/proc/1/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError as exc:
        return type(exc).__name__


def _resolve(host: str, version: int) -> str | None:
    family = socket.AF_INET6 if version == 6 else socket.AF_INET
    try:
        return str(socket.getaddrinfo(host, None, family)[0][4][0])
    except OSError:
        return None


def default_targets() -> dict:
    """Fixed targets, resolved OUTSIDE first, so a blocked connect inside
    means the address is unreachable (D-150's rule)."""
    tcp = [
        ["internet-ipv4", "1.1.1.1", 443, 4],
        ["internet-ipv6", "2606:4700:4700::1111", 443, 6],
    ]
    if os.environ.get("FLY_APP_NAME"):
        tcp.append(["fly-dns", "fdaa::3", 53, 6])
        api = _resolve("_api.internal", 6)
        if api:
            tcp.append(["fly-machines-api", api, 4280, 6])
    return {"tcp": tcp, "dns": ["example.com"]}


def _data_hosts() -> list[list]:
    """A6: the hosts that hold DocFlow's data and its model calls."""
    out = []
    for label, host in (
        ("anthropic-api", "api.anthropic.com"),
        ("supabase", os.environ.get("SELFTEST_SUPABASE_HOST", "")),
        ("supabase-db", os.environ.get("SELFTEST_SUPABASE_DB_HOST", "")),
    ):
        if host:
            address = _resolve(host, 4)
            if address:
                out.append([f"A6:{label}", address, 443 if label != "supabase-db" else 5432, 4])
    return out


class _Listener:
    """A7's target: a listener outside the sandbox on this machine's
    loopback, standing for the supervisor's own port."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
                conn.close()
            except OSError:
                return


def run_all(runner: Runner, groups: str, extra_targets: dict) -> None:
    if "A" in groups:
        listener = _Listener()
        supervisor_port = int(os.environ.get("SELFTEST_SUPERVISOR_PORT", "0"))
        targets = default_targets()
        targets["tcp"] = [
            *targets["tcp"],
            *_data_hosts(),
            ["A7:supervisor-port", "127.0.0.1", supervisor_port or listener.port, 4],
            *extra_targets.get("tcp", []),
        ]
        targets["dns"] = [*targets["dns"], *extra_targets.get("dns", [])]
        runner.network(targets)
        runner.view()
        runner.escape()
        runner.concurrent()
    if "S" in groups:
        runner.seccomp()
    if "B" in groups:
        runner.memory()
        runner.cpu()
        runner.wall_clock()
        runner.forkbomb()
        runner.flood()
        runner.disk()
        runner.one_per_file()
        runner.cgroup_escape()
        runner.libreoffice()
        runner.no_leak()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="parse_service.selftest")
    parser.add_argument("groups", help="any of A, S, B, canary; or 'all'")
    parser.add_argument("--targets", help="JSON file: extra tcp targets and dns names (A3, A4)")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("run as root on the parse machine")
        return 2
    settings = config.load()
    if not settings.isolation:
        print("self-tests need isolation on")
        return 2
    become_subreaper()  # as the service does: killed jobs' orphans are reaped (B5/B11)
    cgroups = Cgroups.detect()
    cgroups.setup()
    print(f"cgroup v{cgroups.version}; machine has active swap: {active_swap()}", flush=True)
    runner = Runner(settings, cgroups)
    if args.groups == "canary":
        runner.canary()
    else:
        extra = json.loads(Path(args.targets).read_text()) if args.targets else {}
        groups = "ASB" if args.groups == "all" else args.groups
        if args.groups == "all":
            runner.canary()
        run_all(runner, groups, extra)
    print("\n### Summary", flush=True)
    for check in runner.checks:
        print(f"{check.verdict:10} {check.id}", flush=True)
    return 1 if any(c.verdict == "FAIL" for c in runner.checks) else 0


if __name__ == "__main__":
    sys.exit(main())
