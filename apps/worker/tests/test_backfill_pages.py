"""
The pages backfill without a database (viewer A6; DECISIONS.md D-236): it
queues at most a batch at a time, only once the pages queue is empty, stops
on a broker it can't read or a queue that doesn't drain, honours --limit and
--dry-run, and sends the normal render task to the pages queue. Its selection
against the real database is in test_backfill_pages_db.py.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from docflow_core.constants import PAGES_QUEUE

import app.backfill_pages as backfill

TENANT_A = UUID("aaaaaaaa-0000-4000-8000-000000000001")
TENANT_B = UUID("bbbbbbbb-0000-4000-8000-000000000002")


def _candidates(count: int, tenant: UUID = TENANT_A) -> list[backfill.Candidate]:
    return [backfill.Candidate(tenant, uuid4()) for _ in range(count)]


class _Broker:
    """The pages queue's length as the backfill reads it, and what it sent.
    Each send adds one to the queue; each read of an non-empty queue drains
    one, as the one pages process would."""

    def __init__(self, waiting: int = 0):
        self.waiting = waiting
        self.sent: list[backfill.Candidate] = []
        self.events: list[str] = []

    def send(self, candidate: backfill.Candidate) -> None:
        self.sent.append(candidate)
        self.waiting += 1
        self.events.append("send")

    def length(self) -> int:
        self.events.append(f"read {self.waiting}")
        current = self.waiting
        if self.waiting:
            self.waiting -= 1
        return current


def _run(candidates, broker, **kwargs):
    lines: list[str] = []
    queued = backfill.run(
        candidates, broker.send, broker.length, out=lines.append, sleep=lambda _s: None, **kwargs
    )
    return queued, lines


# ── Which documents (the database half is in test_backfill_pages_db.py) ─────


def test_only_the_statuses_a_reviewer_opens_are_ever_selected():
    """Never quarantined, failed, pending, staged or processing (founder,
    2026-11-11)."""
    assert backfill.BACKFILL_STATUSES == ("needs_review", "approved", "exported", "rejected")
    for never in ("quarantined", "failed", "pending", "staged", "processing"):
        assert f"'{never}'" not in backfill._STATUSES_SQL


# ── Bounded ──────────────────────────────────────────────────────────────────


def test_it_queues_a_batch_at_a_time_and_each_batch_only_once_the_queue_is_empty():
    candidates = _candidates(7)
    broker = _Broker()
    queued, lines = _run(candidates, broker, batch=3)
    assert queued == 7
    assert broker.sent == candidates  # oldest first, as selected
    # Before each batch the queue was read until it was empty; never more
    # than one batch was sent between two empty reads.
    sends_since_empty = 0
    for event in broker.events:
        if event == "read 0":
            sends_since_empty = 0
        elif event == "send":
            sends_since_empty += 1
            assert sends_since_empty <= 3
    assert len(lines) == 7
    assert lines[0] == f"backfill_pages queued tenant_id={TENANT_A} document_id={candidates[0].document_id}"


def test_it_waits_while_live_renders_are_queued_before_sending_anything():
    broker = _Broker(waiting=2)
    slept: list[float] = []
    backfill.run(_candidates(1), broker.send, broker.length, out=lambda _l: None, sleep=slept.append)
    assert broker.events[:4] == ["read 2", "read 1", "read 0", "send"]
    assert slept == [backfill.POLL_SECONDS, backfill.POLL_SECONDS]


def test_a_queue_that_does_not_drain_stops_the_run_and_sends_nothing_more():
    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    sent: list[backfill.Candidate] = []
    with pytest.raises(backfill.QueueNotDraining):
        backfill.run(
            _candidates(2), sent.append, lambda: 5, max_wait=30, out=lambda _l: None, sleep=sleep, clock=clock
        )
    assert sent == []


@pytest.mark.parametrize("batch", [0, backfill.MAX_BATCH + 1])
def test_the_batch_is_bounded(batch):
    with pytest.raises(ValueError):
        backfill.run(_candidates(1), lambda _c: None, lambda: 0, batch=batch, out=lambda _l: None)


# ── The command ─────────────────────────────────────────────────────────────


def test_dry_run_counts_per_tenant_and_queues_nothing(capsys):
    candidates = _candidates(2, TENANT_A) + _candidates(1, TENANT_B)
    broker = _Broker()
    assert (
        backfill.main(["--dry-run"], select=lambda: candidates, send=broker.send, queue_length=broker.length)
        == 0
    )
    assert broker.sent == [] and broker.events == []
    assert capsys.readouterr().out.splitlines() == [
        f"backfill_pages dry_run tenant_id={TENANT_A} documents=2",
        f"backfill_pages dry_run tenant_id={TENANT_B} documents=1",
        "backfill_pages dry_run total=3 tenants=2",
    ]


def test_limit_stops_after_that_many_documents(capsys):
    candidates = _candidates(5)
    broker = _Broker()
    assert (
        backfill.main(
            ["--limit", "2"], select=lambda: candidates, send=broker.send, queue_length=broker.length
        )
        == 0
    )
    assert broker.sent == candidates[:2]
    assert capsys.readouterr().out.splitlines()[-1].startswith("backfill_pages done queued=2.")


def test_a_broker_that_cannot_be_read_stops_the_run_with_a_clear_line(capsys):
    sent: list[backfill.Candidate] = []
    code = backfill.main([], select=lambda: _candidates(2), send=sent.append, queue_length=lambda: None)
    assert code == 1 and sent == []
    assert "backfill_pages stopped: the broker's pages queue can't be read" in capsys.readouterr().err


def test_a_render_that_cannot_be_queued_stops_the_run(capsys):
    def refuse(_candidate):
        raise backfill.BrokerUnavailable()

    assert backfill.main([], select=lambda: _candidates(2), send=refuse, queue_length=lambda: 0) == 1
    assert "can't be read" in capsys.readouterr().err


def test_a_queue_that_does_not_drain_ends_the_command_non_zero(monkeypatch, capsys):
    def never(*_args, **_kwargs):
        raise backfill.QueueNotDraining()

    monkeypatch.setattr(backfill, "wait_for_empty_queue", never)
    assert backfill.main([], select=lambda: _candidates(1), send=lambda _c: None, queue_length=lambda: 9) == 1
    assert "is the pages process running?" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["--batch", "0"], ["--batch", "11"], ["--limit", "0"], ["--max-wait", "1"]])
def test_out_of_range_arguments_are_refused_before_anything_is_read(argv):
    read: list[str] = []
    with pytest.raises(SystemExit) as stopped:
        backfill.main(
            argv, select=lambda: read.append("select") or [], send=lambda _c: None, queue_length=lambda: 0
        )
    assert stopped.value.code == 2 and read == []


def test_each_render_is_the_normal_task_on_the_pages_queue_sent_without_retries(monkeypatch):
    from app.celery_app import celery_app

    calls: list[tuple] = []
    monkeypatch.setattr(
        celery_app,
        "send_task",
        lambda name, args=None, queue=None, retry=None: calls.append((name, args, queue, retry)),
    )
    candidate = backfill.Candidate(TENANT_A, uuid4())
    backfill._send(candidate)
    assert calls == [
        ("docflow.render_pages", [str(TENANT_A), str(candidate.document_id)], PAGES_QUEUE, False)
    ]


def test_a_send_the_broker_refuses_becomes_a_stop(monkeypatch):
    from app.celery_app import celery_app

    def refused(*_args, **_kwargs):
        raise ConnectionRefusedError("acme test: no broker")

    monkeypatch.setattr(celery_app, "send_task", refused)
    with pytest.raises(backfill.BrokerUnavailable):
        backfill._send(backfill.Candidate(TENANT_A, uuid4()))
