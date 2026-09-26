"""
Runs `parse_and_extract` in a child process for the H3 kill test
(test_pipeline_integrity_db.py): the model is faked, and matching blocks after
writing a marker file, so the test can kill the process with SIGKILL /
TerminateProcess at a known point -- after the model's answer was saved, before
the document reached review -- exactly as a crashed or redeployed worker would.

Usage: python -m tests.kill_runner <tenant_id> <document_id> <marker_path>
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import app.tasks.parse_and_extract as task_module
from tests.db_helpers import FakeAnthropic, model_payload


def main(tenant_id: str, document_id: str, marker: str) -> None:
    payload = model_payload(
        header={"order_total": "570.00"},
        lines=[{"quantity": "12", "unit_price": "47.50", "line_total": "570.00"}],
    )
    task_module.anthropic.Anthropic = FakeAnthropic(payload)  # type: ignore[misc,assignment]

    def blocked_matching(*args: object, **kwargs: object) -> None:
        Path(marker).write_text("reached", encoding="utf-8")
        time.sleep(600)

    task_module.match_document_lines = blocked_matching  # type: ignore[assignment]
    task_module.parse_and_extract(tenant_id, document_id)


if __name__ == "__main__":
    main(*sys.argv[1:4])
