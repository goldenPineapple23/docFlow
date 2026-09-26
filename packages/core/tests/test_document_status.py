"""
The document state machine, as pure code (H3; D-158). The database half --
that Postgres refuses an illegal change and agrees with this list -- is in
apps/worker/tests/test_pipeline_integrity_db.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from docflow_core import document_status
from docflow_core.document_status import IllegalTransition, is_allowed

REPO = Path(__file__).resolve().parents[3]
APPLICATION_CODE = (
    REPO / "packages" / "core" / "docflow_core",
    REPO / "apps" / "api" / "app",
    REPO / "apps" / "worker" / "app",
)


@pytest.mark.parametrize("finished", ["approved", "exported", "needs_review", "rejected", "failed"])
def test_H3_nothing_finished_or_in_review_goes_back_to_processing(finished):
    assert not is_allowed(finished, "processing")


def test_H3_review_is_entered_only_from_processing_or_a_reopen():
    sources = {old for old, new in document_status.ALLOWED if new == "needs_review"}
    assert sources == {"processing", "approved", "exported", "rejected"}


def test_H3_an_unknown_transition_is_refused_before_it_reaches_the_database():
    with pytest.raises(IllegalTransition):
        document_status._check(["approved"], "processing")
    with pytest.raises(IllegalTransition):
        document_status._assignments({"status": "approved"})  # not settable alongside


def test_H3_every_status_change_in_the_application_goes_through_document_status():
    """A status written anywhere else would bypass the compare-and-set. The
    database trigger still refuses an illegal change, but a legal one written
    without the compare-and-set can overwrite someone else's (H3)."""
    offenders = []
    update = re.compile(r"UPDATE\s+documents\b(.*?)(WHERE|RETURNING|\"\"\"|$)", re.IGNORECASE | re.DOTALL)
    for root in APPLICATION_CODE:
        for path in root.rglob("*.py"):
            if path.name == "document_status.py":
                continue
            source = path.read_text(encoding="utf-8")
            for match in update.finditer(source):
                if re.search(r"\bstatus\s*=", match.group(1)):
                    line = source[: match.start()].count("\n") + 1
                    offenders.append(f"{path.relative_to(REPO)}:{line}")
    assert not offenders, f"document status written outside document_status: {offenders}"
