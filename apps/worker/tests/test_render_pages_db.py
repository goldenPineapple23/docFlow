"""
The page-render task against the real database, real Storage and the real
parse service (viewer A3; DECISIONS.md D-232): what it draws and stores for
each kind of file, the one write of page_count and pages, the stale keys a
redraw leaves, every way it gives up (each cleaning up and leaving both
columns NULL), first write wins, the checks 0040 adds, and the document task
queueing it without ever depending on the queue.

CI runs these against the real parse image (isolation on) and its own
database; locally, against the dev parse service conftest.py starts.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from types import SimpleNamespace
from uuid import UUID

import pytest
from celery import Celery
from docflow_core import parse_client, storage
from docflow_core.db import platform_session, tenant_session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import app.tasks.parse_and_extract as document_task
import app.tasks.render_pages as pages
from tests.conftest import PARSE_DIR, fixture_bytes, requires_pages_schema
from tests.db_helpers import WorkerTestTenant, model_payload, run_extraction

pytestmark = requires_pages_schema


def _build_pdf(page_texts: list[str]) -> bytes:
    """The parse service's own hand-built PDF (no PDF library needed here)."""
    spec = importlib.util.spec_from_file_location(
        "parse_fixture_builders", PARSE_DIR / "tests" / "fixture_builders.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: its @dataclass looks the module up in
    # sys.modules, and fails on load without it (push run #517; D-232 item 17).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.build_pdf(page_texts)


THREE_PAGES = [f"Acme Test Distributor order WT-3003, page {n}" for n in (1, 2, 3)]


def _render(tenant: WorkerTestTenant, document_id: UUID, *, retries: int = 0) -> None:
    pages.render_pages.apply(args=[str(tenant.tenant_id), str(document_id)], retries=retries)


def _columns(document_id: UUID) -> tuple:
    with platform_session() as session:
        row = session.execute(
            text("SELECT page_count, pages FROM documents WHERE id = :id"), {"id": str(document_id)}
        ).one()
    return row[0], row[1]


def _keys(tenant: WorkerTestTenant, document_id: UUID) -> list[str]:
    return storage.list_pages(tenant.tenant_id, document_id)


def _key(tenant: WorkerTestTenant, document_id: UUID, number: int) -> str:
    return f"tenants/{tenant.tenant_id}/derived/{document_id}/pages/{number:04d}"


def _seed_old_pages(tenant: WorkerTestTenant, document_id: UUID, count: int) -> None:
    """What an earlier, unfinished attempt left: page keys, columns still NULL."""
    for index in range(count):
        storage.save_page(
            tenant.tenant_id, document_id, index, b"left by an earlier attempt", content_type="image/png"
        )


def _image_kind(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return "neither"


# ── What it draws and stores ─────────────────────────────────────────────────


def test_every_page_of_a_three_page_pdf_is_stored_and_listed_in_one_write():
    with WorkerTestTenant("Acme Test Pages PDF") as tenant:
        did = tenant.create_pending_document(_build_pdf(THREE_PAGES), "po.pdf")
        _render(tenant, did)

        page_count, listed = _columns(did)
        assert page_count == 3
        assert [entry["index"] for entry in listed] == [0, 1, 2]
        for entry in listed:
            assert set(entry) == {"index", "media_type", "width", "height"}
            assert (entry["width"], entry["height"]) == (1700, 2200)  # US Letter at 200 dpi
        keys = _keys(tenant, did)
        assert keys == [_key(tenant, did, n) for n in (1, 2, 3)]
        for entry, key in zip(listed, keys, strict=True):
            assert _image_kind(storage.read_file(tenant.tenant_id, key)) == entry["media_type"]


@pytest.mark.parametrize("name, count", [("po.tif", 2), ("po.jpg", 1), ("po.heic", 1)])
def test_each_paged_format_gets_every_page(name, count):
    with WorkerTestTenant("Acme Test Pages Formats") as tenant:
        did = tenant.create_pending_document(fixture_bytes(f"positive/{name}"), name)
        _render(tenant, did)
        page_count, listed = _columns(did)
        assert page_count == count and len(listed) == count
        assert len(_keys(tenant, did)) == count


def test_a_format_shown_as_text_gets_zero_pages_and_an_empty_list():
    with WorkerTestTenant("Acme Test Pages Text") as tenant:
        did = tenant.create_pending_document(fixture_bytes("positive/po.docx"), "po.docx")
        _render(tenant, did)
        assert _columns(did) == (0, [])
        assert _keys(tenant, did) == []


def test_a_redraw_that_draws_fewer_pages_leaves_no_old_key_behind():
    with WorkerTestTenant("Acme Test Pages Stale") as tenant:
        did = tenant.create_pending_document(_build_pdf(THREE_PAGES), "po.pdf")
        _seed_old_pages(tenant, did, 5)
        _render(tenant, did)
        assert _columns(did)[0] == 3
        keys = _keys(tenant, did)
        assert keys == [_key(tenant, did, n) for n in (1, 2, 3)]
        assert all(storage.read_file(tenant.tenant_id, k) != b"left by an earlier attempt" for k in keys)


def test_a_second_run_finds_the_list_written_and_changes_nothing(caplog):
    with WorkerTestTenant("Acme Test Pages Twice") as tenant:
        did = tenant.create_pending_document(_build_pdf(THREE_PAGES), "po.pdf")
        _render(tenant, did)
        before = (_columns(did), {k: storage.read_file(tenant.tenant_id, k) for k in _keys(tenant, did)})
        with caplog.at_level(logging.INFO, logger=pages.__name__):
            _render(tenant, did)
        after = (_columns(did), {k: storage.read_file(tenant.tenant_id, k) for k in _keys(tenant, did)})
        assert after == before
        assert f"pages_skipped document_id={did} reason=already_drawn" in caplog.text


# ── Before every write: still wanted ─────────────────────────────────────────


def test_a_deleted_document_gets_no_pages():
    with WorkerTestTenant("Acme Test Pages Deleted Doc") as tenant:
        did = tenant.create_pending_document(_build_pdf(THREE_PAGES), "po.pdf")
        with platform_session() as session:
            session.execute(text("UPDATE documents SET deleted_at = now() WHERE id = :id"), {"id": str(did)})
        _render(tenant, did)
        assert _columns(did) == (None, None)
        assert _keys(tenant, did) == []


def test_a_tenant_being_deleted_gets_no_pages():
    with WorkerTestTenant("Acme Test Pages Pending Deletion") as tenant:
        did = tenant.create_pending_document(_build_pdf(THREE_PAGES), "po.pdf")
        with platform_session() as session:
            session.execute(
                text("UPDATE tenants SET status = 'pending_deletion' WHERE id = :id"),
                {"id": str(tenant.tenant_id)},
            )
        _render(tenant, did)
        assert _columns(did) == (None, None)
        assert _keys(tenant, did) == []


# ── Giving up: keys deleted, both columns NULL, one log line ─────────────────


def _gave_up(caplog, did: UUID) -> str:
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("pages_failed")]
    assert len(lines) == 1, lines
    assert lines[0].startswith(f"pages_failed document_id={did} error_type=")
    return lines[0].split("error_type=", 1)[1]


def test_a_file_the_service_refuses_is_given_up_and_cleaned_up(caplog):
    with WorkerTestTenant("Acme Test Pages Refused") as tenant:
        did = tenant.create_pending_document(fixture_bytes("hostile/password.pdf"), "po.pdf")
        _seed_old_pages(tenant, did, 2)
        with caplog.at_level(logging.ERROR, logger=pages.__name__):
            _render(tenant, did)
        assert _gave_up(caplog, did).startswith("refused:rejected:DOC-")
        assert _columns(did) == (None, None)
        assert _keys(tenant, did) == []


def test_a_seccomp_kill_gives_up_and_raises_one_alert_naming_the_render(monkeypatch, caplog):
    """Founder, 2026-11-04: a security event keeps its alert. The render ends
    like any refusal, and its one alert row names the render: its own key,
    `where` "pages", and no error code (no document failed)."""
    with WorkerTestTenant("Acme Test Pages Seccomp") as tenant:
        did = tenant.create_pending_document(_build_pdf(THREE_PAGES), "po.pdf")
        _seed_old_pages(tenant, did, 2)
        killed = parse_client.PagesAnswer("crashed", cause="signal_31")
        monkeypatch.setattr(parse_client, "render_pages", lambda *_a, **_k: killed)
        with caplog.at_level(logging.ERROR, logger=pages.__name__):
            _render(tenant, did)
        assert _gave_up(caplog, did) == "refused:crashed:signal_31"
        assert _columns(did) == (None, None)
        assert _keys(tenant, did) == []
        with platform_session() as session:
            alerts = session.execute(
                text("SELECT type, dedupe_key, payload FROM founder_alerts WHERE tenant_id = :t"),
                {"t": str(tenant.tenant_id)},
            ).mappings().all()
        assert [(a["type"], a["dedupe_key"]) for a in alerts] == [
            ("parse_seccomp_kill", f"parse_seccomp_kill:pages:{did}")
        ]
        payload = alerts[0]["payload"]
        assert (payload["where"], payload["ref_id"]) == ("pages", str(did))
        assert "error_code" not in payload


def test_an_error_after_some_pages_were_stored_removes_them(monkeypatch, caplog):
    with WorkerTestTenant("Acme Test Pages Midway") as tenant:
        did = tenant.create_pending_document(_build_pdf(THREE_PAGES), "po.pdf")
        real = parse_client.page_bytes
        decoded: list[int] = []

        def second_page_is_bad(page: dict) -> bytes:
            decoded.append(page["index"])
            if page["index"] == 1:
                raise parse_client.ParseAnswerInvalid("page_bad_base64")
            return real(page)

        stored: list[list[str]] = []
        monkeypatch.setattr(parse_client, "page_bytes", second_page_is_bad)
        real_save = storage.save_page

        def save_and_note(*args, **kwargs):
            path = real_save(*args, **kwargs)
            stored.append(_keys(tenant, did))
            return path

        monkeypatch.setattr(pages, "save_page", save_and_note)
        with caplog.at_level(logging.ERROR, logger=pages.__name__):
            _render(tenant, did)
        assert decoded == [0, 1] and stored == [[_key(tenant, did, 1)]]  # page 1 was stored first
        assert _gave_up(caplog, did) == "ParseAnswerInvalid"
        assert _columns(did) == (None, None)
        assert _keys(tenant, did) == []


def test_after_the_last_retry_it_gives_up_and_cleans_up(monkeypatch, caplog):
    with WorkerTestTenant("Acme Test Pages Retries") as tenant:
        did = tenant.create_pending_document(_build_pdf(THREE_PAGES), "po.pdf")
        _seed_old_pages(tenant, did, 2)

        def never_in(*_args, **_kwargs):
            raise parse_client.ParseUnavailable("http_503")

        monkeypatch.setattr(parse_client, "render_pages", never_in)
        with caplog.at_level(logging.WARNING, logger=pages.__name__):
            _render(tenant, did)  # eager: the three retries run at once
        assert caplog.text.count("pages_retry") == 3
        assert _gave_up(caplog, did) == "ParseUnavailable"
        assert _columns(did) == (None, None)
        assert _keys(tenant, did) == []


def test_at_the_deadline_it_gives_up_and_cleans_up(monkeypatch, caplog):
    with WorkerTestTenant("Acme Test Pages Deadline") as tenant:
        did = tenant.create_pending_document(_build_pdf(THREE_PAGES), "po.pdf")
        _seed_old_pages(tenant, did, 2)
        # The task's own clock only: past the deadline once the first answer
        # is in, so the first page's upload is where it stops.
        clock = SimpleNamespace(now=0.0)
        monkeypatch.setattr(pages, "time", SimpleNamespace(monotonic=lambda: clock.now))
        real = parse_client.render_pages

        def answer_then_run_out_of_time(*args, **kwargs):
            answer = real(*args, **kwargs)
            clock.now = pages.PAGES_DEADLINE_SECONDS + 1.0
            return answer

        monkeypatch.setattr(parse_client, "render_pages", answer_then_run_out_of_time)
        with caplog.at_level(logging.ERROR, logger=pages.__name__):
            _render(tenant, did)
        assert _gave_up(caplog, did) == "deadline"
        assert _columns(did) == (None, None)
        assert _keys(tenant, did) == []


def test_a_run_whose_last_page_is_stored_past_the_deadline_still_writes_its_list(monkeypatch, caplog):
    """Founder, 2026-11-04: the check before the last page's upload passes,
    the upload ends past the deadline, and the list is still written."""
    with WorkerTestTenant("Acme Test Pages Last Page Late") as tenant:
        did = tenant.create_pending_document(_build_pdf(THREE_PAGES), "po.pdf")
        clock = SimpleNamespace(now=0.0)
        monkeypatch.setattr(pages, "time", SimpleNamespace(monotonic=lambda: clock.now))
        real_save = storage.save_page

        def last_upload_runs_late(tid, doc, index, content, *, content_type):
            path = real_save(tid, doc, index, content, content_type=content_type)
            if index == 2:
                clock.now = pages.PAGES_DEADLINE_SECONDS + 1.0
            return path

        monkeypatch.setattr(pages, "save_page", last_upload_runs_late)
        with caplog.at_level(logging.INFO, logger=pages.__name__):
            _render(tenant, did)
        assert clock.now > pages.PAGES_DEADLINE_SECONDS
        assert "pages_failed" not in caplog.text
        assert f"pages_written document_id={did} page_count=3" in caplog.text
        page_count, listed = _columns(did)
        assert page_count == 3 and [entry["index"] for entry in listed] == [0, 1, 2]
        assert _keys(tenant, did) == [_key(tenant, did, n) for n in (1, 2, 3)]


# ── First write wins ─────────────────────────────────────────────────────────


def test_a_run_that_finds_another_s_list_written_stops_without_deleting_a_key(monkeypatch, caplog):
    with WorkerTestTenant("Acme Test Pages First Write") as tenant:
        did = tenant.create_pending_document(_build_pdf(THREE_PAGES), "po.pdf")
        storage.save_page(tenant.tenant_id, did, 8, b"the other run's page 9", content_type="image/png")
        winner = [{"index": i, "media_type": "image/png", "width": 1, "height": 1} for i in range(9)]
        real_draw = pages.draw

        def another_run_writes_first(tid, document_id, *, deadline):
            drawn = real_draw(tid, document_id, deadline=deadline)
            with platform_session() as session:
                session.execute(
                    text("UPDATE documents SET page_count = 9, pages = CAST(:p AS jsonb) WHERE id = :id"),
                    {"id": str(document_id), "p": json.dumps(winner)},
                )
            return drawn

        monkeypatch.setattr(pages, "draw", another_run_writes_first)
        with caplog.at_level(logging.INFO, logger=pages.__name__):
            _render(tenant, did)
        assert f"pages_not_written document_id={did}" in caplog.text
        assert _columns(did) == (9, winner)
        # No stale delete ran: the other run's ninth page is still there.
        assert _key(tenant, did, 9) in _keys(tenant, did)
        assert "pages_failed" not in caplog.text


# ── 0040's checks, as the worker's login ─────────────────────────────────────


@pytest.mark.parametrize(
    "page_count, listed",
    [
        (2, '[{"index": 0}]'),  # the list's length is not the count
        (1, None),  # one without the other
        (None, "[]"),
        (0, '{"index": 0}'),  # not a list
        (-1, "[]"),
    ],
    ids=["length", "count without list", "list without count", "object", "negative"],
)
def test_0040_refuses_a_page_list_that_does_not_match_its_count(page_count, listed):
    with WorkerTestTenant("Acme Test Pages Checks") as tenant:
        did = tenant.create_pending_document(_build_pdf(THREE_PAGES), "po.pdf")
        with pytest.raises(IntegrityError, match="documents_page"):
            with tenant_session(tenant.tenant_id) as session:
                session.execute(
                    text("UPDATE documents SET page_count = :n, pages = CAST(:p AS jsonb) WHERE id = :id"),
                    {"id": str(did), "n": page_count, "p": listed},
                )
        with tenant_session(tenant.tenant_id) as session:
            session.execute(
                text("UPDATE documents SET page_count = 1, pages = CAST(:p AS jsonb) WHERE id = :id"),
                {"id": str(did), "p": '[{"index": 0}]'},
            )
        assert _columns(did) == (1, [{"index": 0}])


# ── The document task queues it, and never depends on it ─────────────────────


def test_the_document_task_queues_the_pages_once_after_its_parse_step(monkeypatch, request):
    with WorkerTestTenant("Acme Test Pages Queued") as tenant:
        did = tenant.create_pending_document()
        run_extraction(monkeypatch, tenant, did, model_payload())
        assert request.node.page_renders == [(tenant.tenant_id, did)]


def test_a_broker_that_refuses_costs_the_document_nothing(monkeypatch, caplog):
    with WorkerTestTenant("Acme Test Pages Broker Down") as tenant:
        did = tenant.create_pending_document()
        monkeypatch.setattr(pages, "celery_app", Celery("refused-broker", broker="redis://127.0.0.1:1/0"))
        monkeypatch.setattr(document_task, "queue_pages", pages.queue_pages)  # the real one
        with caplog.at_level(logging.ERROR, logger=pages.__name__):
            run_extraction(monkeypatch, tenant, did, model_payload())
        assert f"pages_enqueue_failed document_id={did}" in caplog.text
        with platform_session() as session:
            status = session.execute(
                text("SELECT status FROM documents WHERE id = :id"), {"id": str(did)}
            ).scalar_one()
        assert status == "needs_review"
