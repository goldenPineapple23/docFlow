"""
Runs `parse_and_extract` in a child process for the kill tests, with the
model faked, blocking after writing a marker file so the test can kill the
process with SIGKILL / TerminateProcess at a known point, exactly as a crashed
or redeployed worker would:

  matching  -- after the model's answer was saved, before the document
               reached review (H3, test_pipeline_integrity_db.py);
  model     -- inside the model call itself, after its started run row was
               committed (D-163, Stage 3c, test_stage3c_db.py).

Usage: python -m tests.kill_runner <tenant_id> <document_id> <marker_path> [matching|model]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import app.tasks.parse_and_extract as task_module
from tests.db_helpers import FakeAnthropic, model_payload


def main(tenant_id: str, document_id: str, marker: str, mode: str = "matching") -> None:
    payload = model_payload(
        header={"order_total": "570.00"},
        lines=[{"quantity": "12", "unit_price": "47.50", "line_total": "570.00"}],
    )

    def block(*args: object, **kwargs: object) -> Any:
        Path(marker).write_text("reached", encoding="utf-8")
        time.sleep(600)

    fake = FakeAnthropic(payload)
    if mode == "model":
        fake.stream = block  # type: ignore[method-assign]
    else:
        task_module.match_document_lines = block  # type: ignore[assignment]
    task_module.anthropic.Anthropic = fake  # type: ignore[misc,assignment]
    task_module.parse_and_extract(tenant_id, document_id)


if __name__ == "__main__":
    main(*sys.argv[1:5])
