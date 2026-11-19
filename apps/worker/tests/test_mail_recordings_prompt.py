"""
The fixture emails' recorded label answers (B1 slice 4 commit 7; D-246) are
from the label prompt and schema in use: a label recording whose prompt hash
or schema version is another one's fails here by name, so a prompt change
cannot leave a stale recording standing in for the current prompt (founder,
2026-11-19). No database: this reads the recordings only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from docflow_core.config import ROUTING_MODEL_DEFAULT
from docflow_core.extraction import prompt_hash
from docflow_core.mail_labels import MAIL_LABEL_SCHEMA_VERSION, MAIL_LABEL_SYSTEM_PROMPT

RECORDINGS = Path(__file__).resolve().parent / "fixtures" / "mail_recordings"
LABEL_RECORDINGS = sorted(RECORDINGS.glob("*.label.json"))


def test_there_are_label_recordings_to_check():
    assert LABEL_RECORDINGS, f"no label recordings in {RECORDINGS}"


@pytest.mark.parametrize("path", LABEL_RECORDINGS, ids=[p.name for p in LABEL_RECORDINGS])
def test_each_label_recording_is_from_the_current_prompt_schema_and_model(path):
    recording = json.loads(path.read_text(encoding="utf-8"))
    assert recording["prompt_hash"] == prompt_hash(MAIL_LABEL_SYSTEM_PROMPT)
    assert recording["schema_version"] == MAIL_LABEL_SCHEMA_VERSION
    assert recording["model_id"] == ROUTING_MODEL_DEFAULT
