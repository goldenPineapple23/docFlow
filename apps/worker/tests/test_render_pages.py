"""
The page-render task's control flow (viewer A3; DECISIONS.md D-232), with no
database: which outcome retries, which gives up and cleans up, the deadline's
arithmetic, and that queueing it never fails or holds up the document task.
What it draws, stores and writes is proved against the real database, Storage
and parse service in test_render_pages_db.py.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4

import pytest
from celery import Celery
from celery.exceptions import Reject
from docflow_core import constants, parse_client
from docflow_core.storage import StorageUnavailableError

import app.tasks.render_pages as pages
from app.celery_app import celery_app


@pytest.fixture
def flow(monkeypatch):
    """The task with its database and Storage steps recorded instead of run."""
    record: dict = {"draws": 0, "give_up": [], "written": [], "stale": []}
    behaviour: dict = {"draw": lambda: pages.Drawn(page_count=1, pages=[{"index": 0}]), "write": True}

    def draw(tid, did, *, deadline):
        record["draws"] += 1
        record["deadline"] = deadline
        return behaviour["draw"]()

    def write_list(tid, did, drawn):
        record["written"].append(drawn)
        return behaviour["write"]

    monkeypatch.setattr(pages, "draw", draw)
    monkeypatch.setattr(pages, "write_list", write_list)
    monkeypatch.setattr(pages, "give_up", lambda tid, did, error_type: record["give_up"].append(error_type))
    monkeypatch.setattr(pages, "delete_stale", lambda tid, did, drawn: record["stale"].append(drawn))
    return record, behaviour


def _run(retries: int = 0) -> None:
    pages.render_pages.apply(args=[str(uuid4()), str(uuid4())], retries=retries)


def _raise(exc: Exception):
    def raiser():
        raise exc

    return raiser


# ── The deadline (founder, 2026-11-03) ───────────────────────────────────────


def test_the_deadline_is_the_hard_limit_minus_the_most_one_answer_can_take():
    assert constants.PAGES_TASK_TIME_LIMIT_SECONDS == 900
    assert parse_client.PARSE_CONNECT_PATIENCE_SECONDS == 45
    assert constants.PAGES_UPLOAD_ALLOWANCE_SECONDS == 60
    assert parse_client.READ_TIMEOUT_SECONDS == 210
    assert pages.PAGES_DEADLINE_SECONDS == 900 - (45 + 60 + 210) == 585


def test_the_deadline_is_counted_from_the_start_of_this_attempt(flow):
    record, _ = flow
    before = time.monotonic()
    _run()
    assert before + 585 <= record["deadline"] <= time.monotonic() + 585


def test_at_the_deadline_it_gives_up_with_error_type_deadline(flow):
    record, behaviour = flow
    behaviour["draw"] = _raise(pages.Deadline())
    _run()
    assert record["give_up"] == ["deadline"] and record["written"] == []


def test_a_draw_that_ends_past_the_deadline_is_still_written(flow, monkeypatch):
    """Founder, 2026-11-04: every page is stored, so the list is written; no
    deadline check stands between draw() and the one UPDATE."""
    record, _ = flow
    monkeypatch.setattr(pages, "PAGES_DEADLINE_SECONDS", -1)
    _run()
    assert record["give_up"] == [] and len(record["written"]) == 1
    assert record["stale"] == record["written"]


# ── Retries: never got in, or Storage out ────────────────────────────────────


@pytest.mark.parametrize(
    "error", [parse_client.ParseUnavailable("http_503"), StorageUnavailableError("put: fake outage")]
)
def test_a_retried_error_tries_three_more_times_then_gives_up_once(flow, monkeypatch, error):
    record, behaviour = flow
    behaviour["draw"] = _raise(error)
    countdowns: list[int] = []
    real_retry = pages.render_pages.retry

    def retry(**kwargs):
        countdowns.append(kwargs["countdown"])
        return real_retry(**kwargs)

    monkeypatch.setattr(pages.render_pages, "retry", retry)
    _run()  # eager: each retry runs at once, in this process
    assert record["draws"] == 4
    assert countdowns == [60, 300, 900]
    assert record["give_up"] == [type(error).__name__]


def test_after_the_last_retry_it_gives_up_at_once(flow):
    record, behaviour = flow
    behaviour["draw"] = _raise(parse_client.ParseUnavailable("no_connection"))
    _run(retries=3)
    assert record["draws"] == 1 and record["give_up"] == ["ParseUnavailable"]


def test_a_retry_that_cannot_be_queued_gives_up_instead_of_vanishing(flow, monkeypatch):
    record, behaviour = flow
    behaviour["draw"] = _raise(parse_client.ParseUnavailable("http_503"))

    def retry(**kwargs):
        raise Reject(ConnectionRefusedError(), requeue=False)

    monkeypatch.setattr(pages.render_pages, "retry", retry)
    _run()
    assert record["give_up"] == ["retry_not_queued:ConnectionRefusedError"]


# ── Errors that are never retried ────────────────────────────────────────────


@pytest.mark.parametrize(
    "error, error_type",
    [
        (parse_client.ParseLost("ReadTimeout"), "ParseLost"),
        (parse_client.ParseAnswerInvalid("bad_page"), "ParseAnswerInvalid"),
        (pages.Refused("rejected:DOC-001"), "refused:rejected:DOC-001"),
        (pages.Refused("hash_mismatch"), "refused:hash_mismatch"),
        (RuntimeError("anything else"), "RuntimeError"),
    ],
)
def test_an_error_that_is_not_retried_gives_up_once(flow, error, error_type):
    record, behaviour = flow
    behaviour["draw"] = _raise(error)
    _run()
    assert record["draws"] == 1 and record["give_up"] == [error_type]
    assert record["written"] == [] and record["stale"] == []


def test_a_document_no_longer_wanted_is_left_alone(flow):
    record, behaviour = flow
    behaviour["draw"] = _raise(pages.NotWanted("gone"))
    _run()
    assert record["give_up"] == [] and record["written"] == [] and record["stale"] == []


# ── The two security events keep their alerts (founder, 2026-11-04) ──────────


def _draw_with_answer(monkeypatch, answer: parse_client.PagesAnswer) -> list[tuple]:
    """draw() itself, its database, Storage and parse service faked."""
    row = {"storage_path": "x", "original_filename": "po.pdf", "content_sha256": None, "page_count": None}

    class _Session:
        def execute(self, *_a, **_k):
            return self

        def mappings(self):
            return self

        def first(self):
            return row

    class _TenantSession:
        def __init__(self, _tid):
            pass

        def __enter__(self):
            return _Session()

        def __exit__(self, *_exc):
            return False

    alerts: list[tuple] = []
    monkeypatch.setattr(pages, "tenant_session", _TenantSession)
    monkeypatch.setattr(pages, "read_file", lambda tid, path: b"%PDF-1.4 fake")
    monkeypatch.setattr(pages.parse_client, "render_pages", lambda content, filename, first: answer)
    monkeypatch.setattr(
        pages.founder_alerts, "alert_seccomp_kill", lambda tenant_id, **kw: alerts.append((tenant_id, kw))
    )
    tid, did = uuid4(), uuid4()
    with pytest.raises(pages.Refused) as refused:
        pages.draw(tid, did, deadline=time.monotonic() + 60)
    return [(tid, did, refused.value.reason), *alerts]


def test_a_seccomp_kill_raises_its_own_alert_naming_the_render(monkeypatch):
    """where="pages" (its own dedupe key) and no catalog code: DOC-030 says
    the document failed, and a render's kill fails no document."""
    (tid, did, reason), *alerts = _draw_with_answer(
        monkeypatch, parse_client.PagesAnswer("crashed", cause="signal_31")
    )
    assert reason == "crashed:signal_31"
    assert alerts == [(tid, {"where": "pages", "ref_id": did, "error_code": None})]


@pytest.mark.parametrize(
    "answer",
    [
        parse_client.PagesAnswer("crashed", cause="signal_11"),
        parse_client.PagesAnswer("stopped", cause="memory"),
        parse_client.PagesAnswer("rejected", code="DOC-016"),
    ],
    ids=["other crash", "limit", "rejected"],
)
def test_any_other_render_failure_raises_no_alert(monkeypatch, answer):
    _, *alerts = _draw_with_answer(monkeypatch, answer)
    assert alerts == []


# ── First write wins ─────────────────────────────────────────────────────────


def test_stale_keys_are_deleted_only_after_this_run_wrote_the_list(flow):
    record, _ = flow
    _run()
    assert len(record["written"]) == 1 and record["stale"] == record["written"]


def test_a_run_that_finds_the_list_already_written_deletes_nothing(flow):
    record, behaviour = flow
    behaviour["write"] = False
    _run()
    assert len(record["written"]) == 1 and record["stale"] == [] and record["give_up"] == []


# ── Queueing it (founder, 2026-11-03) ────────────────────────────────────────


def test_it_goes_to_the_pages_queue_without_publish_retries(monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr(pages.celery_app, "send_task", lambda *a, **k: sent.append((a, k)))
    tid, did = uuid4(), uuid4()
    pages.queue_pages(tid, did)
    expected = {"args": [str(tid), str(did)], "queue": "pages", "retry": False}
    assert sent == [(("docflow.render_pages",), expected)]


def test_a_failure_to_queue_is_logged_and_never_raised(monkeypatch, caplog):
    def refuse(*_a, **_k):
        raise ConnectionRefusedError("broker down")

    monkeypatch.setattr(pages.celery_app, "send_task", refuse)
    did = uuid4()
    with caplog.at_level(logging.ERROR, logger=pages.__name__):
        pages.queue_pages(uuid4(), did)
    assert f"pages_enqueue_failed document_id={did} error_type=ConnectionRefusedError" in caplog.text


def test_a_refused_broker_costs_the_document_task_under_ten_seconds(monkeypatch, caplog):
    """The real client against a port nothing listens on: no publish retry,
    and kombu's own connection attempts end inside its connect timeout."""
    refused = Celery("refused-broker", broker="redis://127.0.0.1:1/0")
    monkeypatch.setattr(pages, "celery_app", refused)
    started = time.monotonic()
    with caplog.at_level(logging.ERROR, logger=pages.__name__):
        pages.queue_pages(uuid4(), uuid4())
    took = time.monotonic() - started
    assert took < 10, took
    assert "pages_enqueue_failed" in caplog.text


# ── Where it runs ────────────────────────────────────────────────────────────


def test_the_task_is_registered_and_its_queue_is_declared():
    assert "docflow.render_pages" in celery_app.tasks
    assert constants.PAGES_QUEUE == "pages" and "pages" in celery_app.conf.task_queues
    task = celery_app.tasks["docflow.render_pages"]
    assert (task.time_limit, task.soft_time_limit, task.max_retries) == (900, None, 3)
