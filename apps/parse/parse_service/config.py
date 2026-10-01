"""
The parse service's settings and limits (Stage 3c; BUILD-STATUS "3c
detailed design", item 3).

Every limit is a named constant here, not a literal elsewhere. The values
were proposed in the design and are fixed by measurement: test D1 (a real
PO in every format parses under them) runs in CI and on Fly.

Production mode is on when `FLY_APP_NAME` is set (Fly sets it on every
machine) or `DOCFLOW_ENV=production`. In production mode the service
refuses to start with isolation off, without a token, or with any
`PARSE_TEST_ONLY_*` switch set (founder, 2026-10-01: a test-only switch that
weakens a cap must be impossible to turn on on Fly; test E5).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

MIB = 1024 * 1024

# ── Per-job limits (item 3) ────────────────────────────────────────────────
# Wall clock, enforced by the supervisor with SIGKILL to the whole job.
# Above LibreOffice's own 120 s, well inside the 20-minute read budget (D-163).
PARSE_JOB_WALL_SECONDS = 180
# Primary caps, per job, from the job's cgroups.
JOB_MEMORY_BYTES = 768 * MIB  # memory+swap is set to the same value
JOB_CPU_BUDGET_SECONDS = 150  # all the job's processes together; kills
JOB_CPU_QUOTA_CPUS = 1  # cfs quota / cpu.max; slows, never kills
JOB_PIDS_MAX = 128
# setrlimit backstops (per process, or per slot user for processes).
RLIMIT_AS_BYTES = 2048 * MIB  # high on purpose: LibreOffice reserves far more than it uses
RLIMIT_CPU_SECONDS = 170
RLIMIT_NPROC = 256
RLIMIT_NOFILE = 256
RLIMIT_FSIZE_BYTES = 64 * MIB
# Writable tmpfs inside the job; their pages count against job memory.
WORK_TMPFS_BYTES = 256 * MIB
HOME_TMPFS_BYTES = 128 * MIB
SHM_TMPFS_BYTES = 64 * MIB
# The job's answer, read by the supervisor. A 25 MB scanned PDF returned
# base64 is about 34 MB.
ANSWER_CAP_BYTES = 48 * MIB
# How often the supervisor reads the job's cgroups.
WATCH_INTERVAL_SECONDS = 0.1

# ── The service ────────────────────────────────────────────────────────────
PARSE_SLOTS = 2
SLOT_UID_BASE = 10001  # slot 0 runs as 10001, slot 1 as 10002
BUSY_RETRY_AFTER_SECONDS = 5

# Inside the job's mount namespace.
WORK_DIR = "/work"
HOME_DIR = "/lohome"
# Bind-mounted from WORK_DIR/tmp inside every job (sandbox_init._tmp_is_work, Q14).
TMP_DIRS = ("/tmp", "/var/tmp")

TEST_ONLY_PREFIX = "PARSE_TEST_ONLY_"


@dataclass(frozen=True)
class Settings:
    production: bool
    isolation: bool
    token: str
    port: int
    libreoffice_path: str
    test_only: tuple[str, ...]


class StartupRefused(Exception):
    """The service must not start; the message names the setting, never a value."""


def load(environ: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if environ is None else environ)
    production = bool(env.get("FLY_APP_NAME")) or env.get("DOCFLOW_ENV", "").lower() == "production"
    isolation_value = env.get("PARSE_ISOLATION", "on").strip().lower()
    if isolation_value not in ("on", "off"):
        raise StartupRefused("PARSE_ISOLATION must be 'on' or 'off'")
    isolation = isolation_value == "on"
    test_only = tuple(sorted(name for name in env if name.startswith(TEST_ONLY_PREFIX)))
    settings = Settings(
        production=production,
        isolation=isolation,
        token=env.get("PARSE_SERVICE_TOKEN", ""),
        port=int(env.get("PORT", "8100")),
        libreoffice_path=env.get("LIBREOFFICE_PATH", ""),
        test_only=test_only,
    )
    check(settings)
    return settings


def check(settings: Settings) -> None:
    if not settings.production:
        return
    if not settings.isolation:
        raise StartupRefused("PARSE_ISOLATION=off is refused in production mode")
    if not settings.token:
        raise StartupRefused("PARSE_SERVICE_TOKEN is required in production mode")
    if settings.test_only:
        raise StartupRefused("test-only switches are refused in production mode: " + ", ".join(settings.test_only))
