"""DOC-022's alert detail names how the last lost parse was lost (founder,
2026-10-01, departure #5): a 502 or 504 from Fly's proxy, or a read error,
so a Fly machine failing to start can be told apart from a parser crash.
Fixed labels only; the alert is emailed (Section 7.10)."""

from __future__ import annotations

from docflow_core.retry_rules import failure_detail


def test_the_last_lost_reason_is_in_the_detail():
    detail = failure_detail(3, [], [2, 3], last_lost_reason="http_502")
    assert detail["last_lost_reason"] == "http_502"
    assert detail["parse_lost_attempts"] == [2, 3]
    assert detail["cause_detail"] == "parse_lost"


def test_without_a_lost_parse_there_is_no_reason():
    assert "last_lost_reason" not in failure_detail(3, [3], [])
