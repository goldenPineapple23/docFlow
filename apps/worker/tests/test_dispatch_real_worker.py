"""
Stage 3d, item 8: fairness with the real database, a real Redis queue and a
real Celery worker (tests/dispatch_app.py). What is replaced is the document's
own work (no file, no model); what is proven is the dispatcher, the queue and
the claim, all real, at staging's one slot:

  tenant A has five bulk-lane orders waiting; the first is being read. Then
  tenant B sends one order and A sends one more on its own (interactive).
  The worker takes them A1, B1, A-interactive, A2, A3, A4, A5: the newcomer
  gets the very next slot (H4), and A's single order goes ahead of its own
  backfill (Q3).

Linux only, like the prefork tests (prefork pool, process groups); it runs in
CI on a database that holds nothing else waiting.
"""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import redis
from docflow_core import dispatch
from docflow_core.config import get_settings
from docflow_core.db import platform_session
from sqlalchemy import text

from app.celery_app import celery_app
from tests.conftest import requires_stage3d_schema
from tests.db_helpers import WorkerTestTenant

WORKER_ROOT = Path(__file__).resolve().parents[1]

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="the prefork pool and process groups need Linux"),
    requires_stage3d_schema,
    pytest.mark.real_dispatch,
]


@pytest.fixture
def worker() -> Iterator[tuple[str, str]]:
    queue = f"dispatch-test-{uuid4().hex[:8]}"
    order_key = f"{queue}:claims"
    env = {**os.environ, "DISPATCH_TEST_QUEUE": queue, "DISPATCH_TEST_ORDER_KEY": order_key}
    process = subprocess.Popen(
        [
            sys.executable, "-m", "celery", "-A", "tests.dispatch_app", "worker",
            "--pool=prefork", "--concurrency=2", "-Q", queue, "--loglevel=INFO",
            "--without-heartbeat", "--without-mingle", "--without-gossip",
        ],
        cwd=WORKER_ROOT,
        env=env,
        start_new_session=True,
    )
    try:
        yield queue, order_key
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=30)
        redis.Redis.from_url(get_settings().redis_url).delete(order_key)


def _waiting(tenant: WorkerTestTenant, n: int, *, lane: str, minutes_ago: int) -> list[UUID]:
    ids = []
    with platform_session() as session:
        for i in range(n):
            document_id = uuid4()
            session.execute(
                text(
                    "INSERT INTO documents (id, tenant_id, original_filename, storage_path, source, status, "
                    "content_sha256, dispatch_lane, created_at) VALUES (:id, :t, 'po.txt', :p, 'upload', "
                    "'pending', :sha, :lane, now() - make_interval(mins => :ago) + make_interval(secs => :i))"
                ),
                {
                    "id": str(document_id),
                    "t": str(tenant.tenant_id),
                    # No file is stored (the stubbed work never reads one); the
                    # hash is the real SHA-256 of these stated bytes.
                    "sha": hashlib.sha256(b"dispatcher test document: no file is stored").hexdigest(),
                    "lane": lane,
                    "ago": minutes_ago,
                    "i": i,
                    "p": f"tenants/{tenant.tenant_id}/uploads/none.txt",
                },
            )
            ids.append(document_id)
    return ids


def _claims(order_key: str) -> list[UUID]:
    raw = redis.Redis.from_url(get_settings().redis_url).lrange(order_key, 0, -1)
    return [UUID(item.decode().split(":")[1]) for item in raw]


def _wait_for(what: str, check: Callable[[], bool], timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.2)
    raise AssertionError(f"timed out after {timeout:.0f} s waiting for: {what}")


def test_a_newcomer_gets_the_next_slot_and_a_tenants_single_order_beats_its_own_backfill(worker):
    queue, order_key = worker
    before = dispatch.status().waiting
    # RUNBOOK 1.6: anything else waiting would take turns too; say so plainly.
    assert before == 0, f"{before} documents from other tenants are waiting on this database"

    def send(tenant_id: UUID, document_id: UUID) -> None:
        celery_app.send_task(
            "docflow.parse_and_extract", args=[str(tenant_id), str(document_id)], queue=queue
        )

    with WorkerTestTenant("Acme Test Real Backfill") as a, WorkerTestTenant("Acme Test Real Newcomer") as b:
        backfill = _waiting(a, 5, lane="bulk", minutes_ago=60)
        dispatch.run_pass(send, target=1)  # what the intake nudge does
        _wait_for("the backfill's first order to be claimed", lambda: len(_claims(order_key)) >= 1)

        newcomer = _waiting(b, 1, lane="interactive", minutes_ago=0)[0]
        single = _waiting(a, 1, lane="interactive", minutes_ago=0)[0]
        _wait_for("all seven orders to be claimed", lambda: len(_claims(order_key)) >= 7)

        claims = _claims(order_key)
        assert claims == [backfill[0], newcomer, single, *backfill[1:]], claims
        assert len(set(claims)) == 7  # each claimed once
