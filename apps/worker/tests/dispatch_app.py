"""
The Celery app `test_dispatch_real_worker.py` starts a real worker on (Stage
3d; Linux, like prefork_app.py). It is the production app -- the real
document task, the real claim, the real dispatcher -- with the document's own
work replaced at import, before the worker forks:

  * the task claims its document for real (document_status, compare-and-set),
    records the claim's order in Redis, "reads" for WORK_SECONDS, and fails
    the document (DOC-008) to free its slot -- no file, no model;
  * the pass at the end of every task is the real `dispatch.run_pass`, with a
    target of one slot (staging's) and sends to this test's own queue.

Usage: python -m celery -A tests.dispatch_app worker --pool=prefork -c 2 -Q <queue>
with DISPATCH_TEST_QUEUE and DISPATCH_TEST_ORDER_KEY set. Never import this
module from a test: the changes above would apply to the pytest process too.
"""

from __future__ import annotations

import os
import time
from uuid import UUID

import redis
from docflow_core import dispatch, document_status
from docflow_core.config import get_settings
from docflow_core.db import tenant_session

import app.tasks.parse_and_extract as task_module
from app.celery_app import celery_app

QUEUE = os.environ["DISPATCH_TEST_QUEUE"]
ORDER_KEY = os.environ["DISPATCH_TEST_ORDER_KEY"]
WORK_SECONDS = float(os.environ.get("DISPATCH_TEST_WORK_SECONDS", "1.0"))
TARGET = 1

_redis = redis.Redis.from_url(get_settings().redis_url)


def send(tenant_id: UUID, document_id: UUID) -> None:
    celery_app.send_task("docflow.parse_and_extract", args=[str(tenant_id), str(document_id)], queue=QUEUE)


def _work(tid: UUID, did: UUID) -> None:
    with tenant_session(tid) as session:
        if not document_status.claim_for_processing(session, did):
            return
    _redis.rpush(ORDER_KEY, f"{tid}:{did}")
    time.sleep(WORK_SECONDS)
    with tenant_session(tid) as session:
        document_status.transition(
            session,
            did,
            from_statuses=["processing"],
            to="failed",
            values={"failure_code": "DOC-008", "processed_at": document_status.NOW},
        )


task_module._parse_and_extract = _work  # type: ignore[assignment]
task_module.dispatch_after_task = lambda: dispatch.run_pass(send, target=TARGET)  # type: ignore[assignment]

app = celery_app
