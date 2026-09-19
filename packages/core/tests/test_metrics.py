"""
The dashboard's arithmetic, without a database (D-121).

`summarise` turns stored tenant-days into the KPI card values, so it is the
one place a wrong share or a wrong median would show up on every card. These
tests pin the definitions in Section 7.15.3 exactly:

  * shares are part/whole over the WINDOW, never an average of daily shares;
  * the median is over every document's own hours, not over daily medians;
  * p95 is nearest-rank, and Decimal all the way -- these are dollars;
  * an empty window answers None, not zero: "no data" and "zero" are
    different things on a dashboard.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from docflow_core.metrics import _median, _percentile, is_stale, summarise


def _day(**over):
    row = {
        "documents_received": 0,
        "documents_failed": 0,
        "documents_approved": 0,
        "documents_exported": 0,
        "zero_edit_approvals": 0,
        "edited_actions": 0,
        "line_items": 0,
        "matched_lines": 0,
        "learned_rule_lines": 0,
        "confidence_sum": Decimal("0"),
        "confidence_count": 0,
        "review_within_target": 0,
        "review_sessions": 0,
        "review_sessions_excluded": 0,
        "approval_hours": [],
        "document_costs": [],
        "est_cost_usd": Decimal("0"),
    }
    row.update(over)
    return row


def test_an_empty_window_answers_nothing_rather_than_zero():
    empty = summarise([])
    assert empty["documents_received"] == 0
    for key in (
        "mean_confidence",
        "review_within_target_share",
        "zero_edit_share",
        "mapping_reuse_share",
        "corrections_per_100_lines",
        "median_hours_to_approval",
        "mean_cost_per_document",
        "p95_cost_per_document",
    ):
        assert empty[key] is None, key


def test_shares_are_over_the_window_not_an_average_of_daily_shares():
    """A day with one review at 100% and a day with nine at 0% is 10%, not
    50%. Averaging daily shares would flatter a quiet day into the number."""
    rows = [
        _day(review_within_target=1, review_sessions=1),
        _day(review_within_target=0, review_sessions=9),
    ]
    assert summarise(rows)["review_within_target_share"] == Decimal("0.1000")


def test_zero_edit_mapping_reuse_and_corrections():
    rows = [
        _day(documents_approved=4, zero_edit_approvals=3, line_items=50, edited_actions=5,
             matched_lines=40, learned_rule_lines=10),
        _day(documents_approved=1, zero_edit_approvals=0, line_items=50, edited_actions=0,
             matched_lines=10, learned_rule_lines=0),
    ]
    out = summarise(rows)
    assert out["zero_edit_share"] == Decimal("0.6000")  # 3 of 5
    assert out["mapping_reuse_share"] == Decimal("0.2000")  # 10 of 50 matched
    assert out["corrections_per_100_lines"] == Decimal("5.00")  # 5 edits per 100 lines


def test_mean_confidence_is_weighted_by_documents():
    rows = [
        _day(confidence_sum=Decimal("1.8"), confidence_count=2),
        _day(confidence_sum=Decimal("0.5"), confidence_count=1),
    ]
    assert summarise(rows)["mean_confidence"] == Decimal("0.7667")


def test_the_median_is_over_documents_not_over_days():
    rows = [
        _day(approval_hours=[Decimal("1"), Decimal("2"), Decimal("3")]),
        _day(approval_hours=[Decimal("100")]),
    ]
    assert summarise(rows)["median_hours_to_approval"] == Decimal("2.5")


def test_cost_mean_and_p95_come_from_every_document():
    rows = [
        _day(document_costs=[Decimal("0.10"), Decimal("0.20")], est_cost_usd=Decimal("0.30")),
        _day(document_costs=[Decimal("0.90")], est_cost_usd=Decimal("0.90")),
    ]
    out = summarise(rows)
    assert out["est_cost_usd"] == Decimal("1.20")
    assert out["mean_cost_per_document"] == Decimal("0.4000")
    assert out["p95_cost_per_document"] == Decimal("0.90")


def test_percentile_and_median_edges():
    assert _percentile([], Decimal("0.95")) is None
    assert _median([]) is None
    assert _percentile([Decimal("5")], Decimal("0.95")) == Decimal("5")
    values = [Decimal(str(n)) for n in range(1, 101)]
    # Nearest rank over (n-1): index round(0.95 * 99) = 94, i.e. the 95th of
    # 1..100 -- the same answer numpy's default percentile gives, to the
    # nearest stored value (we never interpolate a cost that never occurred).
    assert _percentile(values, Decimal("0.95")) == Decimal("95")
    assert _median(values) == Decimal("50.5")


def test_a_rollup_is_stale_when_it_never_ran_failed_or_is_too_old():
    now = datetime.now(timezone.utc)
    assert is_stale(None, hours=36) is True
    assert is_stale({"finished_at": None, "ok": None}, hours=36) is True
    assert is_stale({"finished_at": now, "ok": False}, hours=36) is True
    assert is_stale({"finished_at": now - timedelta(hours=40), "ok": True}, hours=36) is True
    assert is_stale({"finished_at": now - timedelta(hours=2), "ok": True}, hours=36) is False
