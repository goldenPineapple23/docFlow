"""
The live-bucket tests run only against Storage on this machine, unless the
run says otherwise (D-250).

`test_storage_bucket_live.py` writes, reads and deletes real objects. In CI
its endpoint is the runner's own local stack (scripts/ci/local_storage_env.py
refuses anything else), so it always runs there.

An endpoint whose host is not loopback (127.0.0.1, localhost or ::1) is
refused, before any fixture or request, unless the process's own environment
carries DOCFLOW_STAGING_RUN=1, the flag a granted staging run already sets
(RUNBOOK 1.4; D-211). The flag is read from the environment only, never from
`.env`, as the database guard reads it (tests/database_guard.py).
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlparse

from tests.database_guard import STAGING_RUN_FLAG

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def refusal(endpoint: str, environ: Mapping[str, str]) -> str | None:
    """Why the live-bucket tests may not run against `endpoint`, or None when
    they may. The endpoint itself is never named: a staging host carries the
    project's reference."""
    try:
        host = (urlparse(endpoint).hostname or "").lower()
    except ValueError:
        host = ""
    if host in LOCAL_HOSTS:
        return None
    if environ.get(STAGING_RUN_FLAG) == "1":
        return None
    return (
        "refused: the Storage endpoint is not on this machine, and these tests write to a real bucket. "
        f"Set {STAGING_RUN_FLAG}=1 in the process's own environment only for a staging run the founder "
        "has granted (RUNBOOK 1.4)."
    )
