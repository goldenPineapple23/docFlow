"""
The "needs review" digest's wording (slice 5.8c, D-131), with no database: the
plural forms, the relative waiting time, and that the rendered email carries
counts and a link and nothing from a document (Section 7.10).

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from docflow_core import email_outbox, review_digest
from docflow_core.constants import REVIEW_DIGEST_INTERVAL_MIN

NOW = datetime(2026, 9, 24, 15, 0, tzinfo=UTC)


def _render(new_waiting: int, waiting: int, waited: timedelta) -> tuple[str, str]:
    params = review_digest.digest_params(
        tenant_name="Acme Test Distributor",
        new_waiting=new_waiting,
        waiting=waiting,
        oldest=NOW - waited,
        now=NOW,
    )
    return email_outbox.render("review_digest", params)


def test_one_order_reads_in_the_singular():
    subject, body = _render(1, 1, timedelta(minutes=16))
    assert subject == "DocFlow: 1 new purchase order is ready for review"
    assert "1 new purchase order is ready for review in DocFlow for Acme Test Distributor." in body
    assert "It is the only one waiting." in body


def test_a_backfill_reads_as_one_count_with_the_whole_queue_and_its_oldest():
    subject, body = _render(1250, 1300, timedelta(days=3, hours=2))
    assert subject == "DocFlow: 1,250 new purchase orders are ready for review"
    assert "1,300 purchase orders are waiting in all; the oldest has waited about 3 days." in body


def test_the_email_links_to_the_queue_and_states_its_own_limit():
    _, body = _render(2, 2, timedelta(minutes=20))
    assert "/review?status=needs_review" in body
    assert f"at most once every {REVIEW_DIGEST_INTERVAL_MIN} minutes" in body


@pytest.mark.parametrize(
    ("waited", "phrase"),
    [
        (timedelta(minutes=5), "less than an hour"),
        (timedelta(minutes=59), "less than an hour"),
        (timedelta(minutes=60), "about 1 hour"),
        (timedelta(hours=47, minutes=59), "about 47 hours"),
        (timedelta(hours=48), "about 2 days"),
        (timedelta(minutes=-3), "less than an hour"),  # clock skew never reads as negative
    ],
)
def test_waiting_time_is_relative_so_no_timezone_is_needed(waited, phrase):
    assert review_digest.waited_phrase(NOW - waited, NOW) == phrase


def test_only_the_roles_that_work_the_queue_are_emailed():
    assert review_digest.RECIPIENT_ROLES == ("owner", "admin", "reviewer")
