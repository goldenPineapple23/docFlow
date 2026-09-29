"""
Stage 3a, on a real worker (founder: "a Linux CI test showing an overrunning
task is killed and the worker carries on"). A real `celery worker --pool=
prefork` runs tests.prefork_app, where the document task's hard limit is 3 s
and a marked file hangs the parser, ignoring every signal it can.

What it proves, in order:
  1. the hung task is killed at the hard limit, and the worker's MAIN
     process records the timeout on the document (app.timeouts);
  2. the worker carries on: a normal document sent next reaches needs_review
     (on a child forked after the kill -- which also proves the child does
     not reuse the main process's database connections);
  3. the stuck sweep retries the timed-out document once; it hangs again and
     is killed again;
  4. the next sweep fails it with DOC-022, and the founder alert's cause is
     `timeout`.
And separately: a worker killed outright (no time limit involved) records
no timeout, so the document takes the unchanged worker-stopped path.

Time limits need signals and fork, so this runs on Linux only -- in CI, the
one place isolation and limits run before production (dev is Windows).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from docflow_core import stuck_documents
from docflow_core.db import platform_session
from sqlalchemy import text

from app.celery_app import celery_app
from tests.conftest import requires_timeout_schema
from tests.db_helpers import WorkerTestTenant
from tests.prefork_constants import HANG_MARKER

WORKER_ROOT = Path(__file__).resolve().parents[1]

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="Celery time limits need Linux (prefork, signals)"),
    requires_timeout_schema,
]


@pytest.fixture
def worker() -> Iterator[tuple[str, subprocess.Popen]]:
    queue = f"prefork-test-{uuid4().hex[:8]}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "tests.prefork_app",
            "worker",
            "--pool=prefork",
            "--concurrency=1",
            "-Q",
            queue,
            "--loglevel=INFO",
            "--without-heartbeat",
            "--without-mingle",
            "--without-gossip",
        ],
        cwd=WORKER_ROOT,
        start_new_session=True,  # its own process group, so it can be killed whole
    )
    try:
        yield queue, process
    finally:
        with_group(process, signal.SIGKILL)
        process.wait(timeout=30)


def with_group(process: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def send(queue: str, tenant_id: UUID, document_id: UUID) -> None:
    celery_app.send_task("docflow.parse_and_extract", args=[str(tenant_id), str(document_id)], queue=queue)


def document(document_id: UUID) -> dict:
    with platform_session() as session:
        return dict(
            session.execute(
                text(
                    "SELECT status, failure_code, processing_attempts, timeout_attempts "
                    "FROM documents WHERE id = :id"
                ),
                {"id": str(document_id)},
            )
            .mappings()
            .one()
        )


def wait_for(what: str, check: Callable[[], bool], timeout: float = 90.0) -> None:
    """Polls, and on failure says what it was waiting for (RUNBOOK 1.6)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.5)
    raise AssertionError(f"timed out after {timeout:.0f} s waiting for: {what}")


def make_stale(document_id: UUID) -> None:
    with platform_session() as session:
        session.execute(
            text("UPDATE documents SET processing_started_at = now() - interval '2 hours' WHERE id = :id"),
            {"id": str(document_id)},
        )


def test_a_hung_task_is_killed_the_worker_carries_on_and_a_timeout_gets_one_retry(worker):
    queue, process = worker
    with WorkerTestTenant("Acme Test Prefork Timeout") as tenant:
        hung = tenant.create_pending_document(content=HANG_MARKER + b"\nPO 1", filename="hang.txt")
        normal = tenant.create_pending_document()

        send(queue, tenant.tenant_id, hung)
        wait_for(
            "the first timeout recorded by the main process",
            lambda: document(hung)["timeout_attempts"] == [1],
        )
        assert document(hung)["status"] == "processing"

        send(queue, tenant.tenant_id, normal)
        wait_for(
            "the worker to carry on and finish a normal document",
            lambda: document(normal)["status"] == "needs_review",
        )
        assert process.poll() is None, "the worker itself must still be running"

        make_stale(hung)
        result = stuck_documents.sweep_tenant(tenant.tenant_id, lambda t, d: send(queue, t, d))
        assert result.requeued == [hung] and result.failed == []
        wait_for(
            "the one retry to hang and be killed too", lambda: document(hung)["timeout_attempts"] == [1, 2]
        )

        make_stale(hung)
        result = stuck_documents.sweep_tenant(tenant.tenant_id, lambda t, d: send(queue, t, d))
        assert result.failed == [hung]
        final = document(hung)
        assert (final["status"], final["failure_code"]) == ("failed", "DOC-022")
        with platform_session() as session:
            causes = [
                row[0]
                for row in session.execute(
                    text(
                        "SELECT payload->>'cause' FROM founder_alerts "
                        "WHERE tenant_id = :t AND type = 'document_stuck'"
                    ),
                    {"t": str(tenant.tenant_id)},
                )
            ]
        assert causes == ["timeout"]


def test_a_worker_killed_outright_records_no_timeout_and_takes_the_worker_stopped_path(worker):
    queue, process = worker
    with WorkerTestTenant("Acme Test Prefork Killed") as tenant:
        hung = tenant.create_pending_document(content=HANG_MARKER + b"\nPO 2", filename="hang.txt")
        send(queue, tenant.tenant_id, hung)
        wait_for("the task to claim the document", lambda: document(hung)["status"] == "processing")

        with_group(process, signal.SIGKILL)  # the whole worker, before any limit
        process.wait(timeout=30)
        assert document(hung)["timeout_attempts"] == []

        assert stuck_documents.decide(1, []) == ("retry", None)
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE documents SET processing_attempts = 3, "
                    "processing_started_at = now() - interval '2 hours' WHERE id = :id"
                ),
                {"id": str(hung)},
            )
        result = stuck_documents.sweep_tenant(tenant.tenant_id, lambda _t, _d: None)
        assert result.failed == [hung]
        with platform_session() as session:
            cause = session.execute(
                text(
                    "SELECT payload->>'cause' FROM founder_alerts "
                    "WHERE tenant_id = :t AND type = 'document_stuck'"
                ),
                {"t": str(tenant.tenant_id)},
            ).scalar_one()
        assert cause == "worker_stopped"
