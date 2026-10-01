"""
The Celery app `test_time_limits_prefork.py` starts a real prefork worker on
(Linux only, Stage 3a). It is the production app with three test changes,
made at import, before the worker forks its children:

  * the document task's hard limit is PREFORK_TEST_LIMIT_SECONDS, not 27 min;
  * a file containing HANG_MARKER hangs the task where it waits for the parse
    service, ignoring every signal it can -- only the hard limit's SIGKILL
    ends it (Stage 3c moved parsing out; the task's own wait is what's left);
  * the model is faked, so a normal file reaches needs_review with no key.

Usage: python -m celery -A tests.prefork_app worker --pool=prefork -c 1 -Q <queue>

Never import this module from a test: the changes above would apply to the
pytest process too. Shared constants are in tests/prefork_constants.py.
"""

from __future__ import annotations

import time
from typing import Any

import app.tasks.parse_and_extract as task_module
from app.celery_app import celery_app
from tests.db_helpers import FakeAnthropic, model_payload
from tests.prefork_constants import HANG_MARKER, PREFORK_TEST_LIMIT_SECONDS

celery_app.tasks["docflow.parse_and_extract"].time_limit = PREFORK_TEST_LIMIT_SECONDS

_real_parse = task_module.parse_client.parse_document


def _parse_or_hang(content: bytes, filename: Any) -> Any:
    if HANG_MARKER in content:
        while True:
            try:
                time.sleep(1)
            except BaseException:  # noqa: BLE001, S110 -- a soft signal must not end it
                pass
    return _real_parse(content, filename)


task_module.parse_client.parse_document = _parse_or_hang  # type: ignore[assignment]
task_module.anthropic.Anthropic = FakeAnthropic(model_payload(lines=[{}]))  # type: ignore[misc,assignment]

app = celery_app
