"""
Stage 3d against the real database (migration 0035): the dispatcher, the
waits, the provider state, the stuck sweep's new rules, and the alerts --
every SECURITY DEFINER function and flag policy exercised for real.

The queue is a recording `send` here; test_dispatch_real_worker.py runs the
same dispatcher behind a real Celery worker and a real queue (Linux, CI).

These tests share the database with whatever else is on it (staging keeps
earlier test data), so they assert only on their own tenants' documents and
put back anything of anyone else's that a pass touched. They leave the model
provider marked up, as they found it.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from uuid import UUID

import pytest
from docflow_core import dispatch, document_status, founder_alerts, model_provider, stuck_documents
from docflow_core.config import get_settings
from docflow_core.constants import (
    DISPATCHER_STALE_MIN,
    PROVIDER_DOWN_FAILURES,
    PROVIDER_MAX_WAIT_HOURS,
    TENANT_IN_FLIGHT_CAP,
)
from docflow_core.db import dispatcher_session, platform_session, rollup_session, tenant_session
from docflow_core.provider_errors import ProviderError
from sqlalchemy import text

from tests.conftest import requires_stage3d_schema
from tests.db_helpers import WorkerTestTenant, model_payload, run_extraction

pytestmark = [requires_stage3d_schema, pytest.mark.real_dispatch]

WIDE_OPEN = 100_000  # a target no test reaches: every ready document is chosen, in turn order

_TENANT_LESS = ("model_api_failure", "model_api_recovered", "dispatcher_stopped", "routing_model_failure")


# ── helpers ────────────────────────────────────────────────────────────────


def _waiting(tenant: WorkerTestTenant, n: int, *, minutes_ago: int, lane: str = "interactive") -> list[UUID]:
    """n waiting documents, oldest first, one minute apart. No file: the
    dispatcher never reads one."""
    with platform_session() as session:
        rows = session.execute(
            text(
                """
                INSERT INTO documents (id, tenant_id, original_filename, storage_path, source, status,
                                       dispatch_lane, created_at)
                SELECT gen_random_uuid(), :t, 'po.txt', 'tenants/' || :t || '/uploads/none.txt',
                       'upload', 'pending', :lane,
                       now() - make_interval(mins => :ago) + make_interval(mins => g)
                  FROM generate_series(0, :n - 1) AS g
                RETURNING id, created_at
                """
            ),
            {"t": str(tenant.tenant_id), "n": n, "ago": minutes_ago, "lane": lane},
        ).all()
    return [UUID(str(r[0])) for r in sorted(rows, key=lambda r: r[1])]


def _set(document_id: UUID, sql: str) -> None:
    with platform_session() as session:
        session.execute(text(f"UPDATE documents SET {sql} WHERE id = :id"), {"id": str(document_id)})


def _row(document_id: UUID) -> dict:
    with platform_session() as session:
        return dict(
            session.execute(text("SELECT * FROM documents WHERE id = :id"), {"id": str(document_id)})
            .mappings()
            .one()
        )


def _provider_status() -> str:
    with dispatcher_session() as session:
        return session.execute(text("SELECT status FROM public.provider_state('anthropic')")).scalar_one()


def _provider_up() -> None:
    """Mark the provider up (a success) and drop the notice that raised."""
    model_provider.record_success()
    _delete_tenant_less_alerts()


def _delete_tenant_less_alerts() -> None:
    with platform_session() as session:
        session.execute(
            text(
                "DELETE FROM email_outbox WHERE id IN (SELECT email_outbox_id FROM founder_alerts "
                "WHERE tenant_id IS NULL AND type = ANY(:types))"
            ),
            {"types": list(_TENANT_LESS)},
        )
        session.execute(
            text("DELETE FROM founder_alerts WHERE tenant_id IS NULL AND type = ANY(:types)"),
            {"types": list(_TENANT_LESS)},
        )


def _alerts(alert_type: str) -> list[dict]:
    with platform_session() as session:
        return [
            dict(r)
            for r in session.execute(
                text(
                    "SELECT * FROM founder_alerts WHERE type = :t AND tenant_id IS NULL "
                    "ORDER BY created_at"
                ),
                {"t": alert_type},
            ).mappings()
        ]


@pytest.fixture
def clean_provider() -> Iterator[None]:
    _provider_up()
    try:
        yield
    finally:
        _provider_up()


class _Recorder:
    """The queue, recorded. Documents of tenants other than `mine` that a pass
    dispatched are put back to waiting afterwards (they were never sent)."""

    def __init__(self, *mine: WorkerTestTenant):
        self.mine = {t.tenant_id for t in mine}
        self.sent: list[tuple[UUID, UUID]] = []
        self._lock = threading.Lock()

    def __call__(self, tenant_id: UUID, document_id: UUID) -> None:
        with self._lock:
            self.sent.append((tenant_id, document_id))

    def ours(self) -> list[tuple[UUID, UUID]]:
        return [s for s in self.sent if s[0] in self.mine]

    def put_back_others(self) -> None:
        for tenant_id, document_id in self.sent:
            if tenant_id not in self.mine:
                with dispatcher_session() as session:
                    session.execute(text("SELECT public.clear_dispatched(:id)"), {"id": str(document_id)})


def _pass(recorder: _Recorder, target: int = WIDE_OPEN) -> dispatch.PassResult:
    result = dispatch.run_pass(recorder, target=target)
    # RUNBOOK 1.6: a pass that couldn't take the lock says why.
    assert result.ran, (
        "another dispatch pass held the advisory lock -- is a worker running against this database?"
    )
    return result


# ── the dispatcher ───────────────────────────────────────────────────────────


def test_candidates_list_only_documents_that_can_go_now_with_each_tenants_in_flight(clean_provider):
    """Gap 3 at the source: a document sitting out a backoff is not a
    candidate, and a tenant with nothing ready doesn't appear at all."""
    with WorkerTestTenant("Acme Test Candidates A") as a, WorkerTestTenant("Acme Test Candidates B") as b:
        ready = _waiting(a, 3, minutes_ago=30)
        busy = _waiting(a, 1, minutes_ago=40)[0]
        _set(busy, "status = 'processing', processing_started_at = now()")
        backing_off = _waiting(b, 1, minutes_ago=50)[0]
        _set(backing_off, "retry_at = now() + interval '10 minutes'")

        with dispatcher_session() as session:
            rows = session.execute(
                text("SELECT * FROM public.dispatch_candidates(10) WHERE tenant_id = ANY(:t)"),
                {"t": [str(a.tenant_id), str(b.tenant_id)]},
            ).mappings().all()
        assert {UUID(str(r["tenant_id"])) for r in rows} == {a.tenant_id}
        assert [UUID(str(r["document_id"])) for r in rows] == ready
        assert {r["in_flight"] for r in rows} == {1}


def test_a_newcomer_goes_ahead_of_a_running_backfill(clean_provider):
    """H4 / Section 5.1: tenant A has 300 waiting and one in flight; tenant B
    sends one order later. In turn order B's order comes before A's next."""
    with WorkerTestTenant("Acme Test Backfill") as a, WorkerTestTenant("Acme Test Newcomer") as b:
        backlog = _waiting(a, 300, minutes_ago=400)
        _set(backlog[0], "dispatched_at = now()")  # in flight
        newcomer = _waiting(b, 1, minutes_ago=1)[0]
        recorder = _Recorder(a, b)
        try:
            _pass(recorder)
            order = [d for _t, d in recorder.ours()]
            assert order.index(newcomer) < order.index(backlog[1]), order[:5]
            assert len([d for t, d in recorder.ours() if t == a.tenant_id]) == 299
        finally:
            recorder.put_back_others()


def test_a_tenants_interactive_upload_goes_ahead_of_its_own_bulk_lane(clean_provider):
    with WorkerTestTenant("Acme Test Lanes") as a:
        bulk = _waiting(a, 20, minutes_ago=120, lane="bulk")
        single = _waiting(a, 1, minutes_ago=1, lane="interactive")[0]
        recorder = _Recorder(a)
        try:
            _pass(recorder)
            assert [d for _t, d in recorder.ours()] == [single, *bulk]
        finally:
            recorder.put_back_others()


def test_gap_3_a_capped_tenant_uses_idle_slots_while_the_only_other_document_backs_off(clean_provider):
    """Founder's gap 3: B's only waiting document has a future retry_at, so it
    can't go now and must not hold A back from idle slots."""
    with WorkerTestTenant("Acme Test Capped") as a, WorkerTestTenant("Acme Test Backing Off") as b:
        in_flight = _waiting(a, TENANT_IN_FLIGHT_CAP, minutes_ago=90)
        for d in in_flight:
            _set(d, "dispatched_at = now()")
        more = _waiting(a, 3, minutes_ago=60)
        backing_off = _waiting(b, 1, minutes_ago=200)[0]
        _set(backing_off, "retry_at = now() + interval '10 minutes'")
        recorder = _Recorder(a, b)
        try:
            _pass(recorder)
            assert [d for _t, d in recorder.ours()] == more
            assert _row(backing_off)["dispatched_at"] is None
        finally:
            recorder.put_back_others()


def test_the_target_counts_what_is_already_in_flight(clean_provider):
    """Q2: free slots = target - in flight, across every tenant."""
    with WorkerTestTenant("Acme Test Target") as a:
        docs = _waiting(a, 5, minutes_ago=30)
        with dispatcher_session() as session:
            in_flight = session.execute(
                text("SELECT coalesce(sum(in_flight), 0) FROM (SELECT DISTINCT tenant_id, in_flight "
                     "FROM public.dispatch_candidates(1)) x")
            ).scalar_one()
        recorder = _Recorder(a)
        try:
            dispatch.run_pass(recorder, target=int(in_flight) + 2)
            sent_anyone = len(recorder.sent)
            assert sent_anyone == 2, recorder.sent
            # Nothing more fits until something finishes.
            dispatch.run_pass(recorder, target=int(in_flight) + 2)
            assert len(recorder.sent) == sent_anyone
            if all(t == a.tenant_id for t, _ in recorder.sent):
                assert [d for _t, d in recorder.sent] == docs[:2]
        finally:
            recorder.put_back_others()


def test_twenty_concurrent_passes_dispatch_every_document_exactly_once(clean_provider):
    """The advisory lock and the compare-and-set: passes never overlap, and
    a document is marked dispatched once."""
    with WorkerTestTenant("Acme Test Concurrency A") as a, WorkerTestTenant("Acme Test Concurrency B") as b:
        docs = _waiting(a, 40, minutes_ago=100) + _waiting(b, 40, minutes_ago=99)
        recorder = _Recorder(a, b)
        threads = [threading.Thread(target=dispatch.run_pass, args=(recorder,), kwargs={"target": WIDE_OPEN})
                   for _ in range(20)]
        try:
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=120)
            ours = [d for _t, d in recorder.ours()]
            assert len(ours) == len(set(ours)), "a document was sent twice"
            # Any pass that ran sent everything ready; the rest saw the lock.
            assert set(ours) == set(docs)
        finally:
            recorder.put_back_others()


def test_a_send_that_fails_puts_the_document_back_to_waiting(clean_provider):
    with WorkerTestTenant("Acme Test Send Fails") as a:
        doc = _waiting(a, 1, minutes_ago=5)[0]
        others: list[UUID] = []

        def broken(tenant_id: UUID, document_id: UUID) -> None:
            if tenant_id != a.tenant_id:
                others.append(document_id)
                return
            raise ConnectionError("broker down")

        result = dispatch.run_pass(broken, target=WIDE_OPEN)
        try:
            assert doc in result.send_failed
            assert _row(doc)["dispatched_at"] is None
        finally:
            for document_id in others:
                with dispatcher_session() as session:
                    session.execute(text("SELECT public.clear_dispatched(:id)"), {"id": str(document_id)})


# ── the heartbeat (gap 1) ────────────────────────────────────────────────────


def test_every_pass_writes_the_heartbeat_holding_or_not(clean_provider):
    recorder = _Recorder()
    try:
        _pass(recorder, target=0)
        health = dispatch.health()
        assert health["stale"] is False and 0 <= health["heartbeat_age_seconds"] <= 5, health

        model_provider.record_failure(ProviderError("wait", "our_configuration", "http_401"))
        assert _provider_status() == "down"
        held = _pass(recorder, target=0)
        assert held.holding and not dispatch.status().stale
    finally:
        recorder.put_back_others()


def test_a_stale_heartbeat_with_documents_waiting_raises_dispatcher_stopped_once_an_hour(monkeypatch):
    """Item 3 / Q5: the sweep's check, through the real dispatcher policy.
    The stale age is given at the status boundary (the database's own clock
    can't be wound forward); the alert, its policy and its dedupe are real."""
    _delete_tenant_less_alerts()
    stale = dispatch.DispatcherStatus(None, DISPATCHER_STALE_MIN * 60 + 1, waiting=4)
    monkeypatch.setattr(dispatch, "status", lambda: stale)
    try:
        assert dispatch.check_stopped() is True
        assert dispatch.check_stopped() is False  # one an hour
        alert = _alerts("dispatcher_stopped")[0]
        assert alert["severity"] == "high" and alert["payload"]["waiting_documents"] == 4

        monkeypatch.setattr(dispatch, "status", lambda: dispatch.DispatcherStatus(None, 30, waiting=4))
        _delete_tenant_less_alerts()
        assert dispatch.check_stopped() is False  # fresh: a holding dispatcher never looks stopped
        monkeypatch.setattr(
            dispatch, "status", lambda: dispatch.DispatcherStatus(None, DISPATCHER_STALE_MIN * 60 + 1, 0)
        )
        assert dispatch.check_stopped() is False  # nothing waiting: nothing is held up
    finally:
        _delete_tenant_less_alerts()


# ── the model provider (Q7, change A) ───────────────────────────────────────


def test_down_after_three_waiting_failures_one_alert_then_probe_and_recover(clean_provider, monkeypatch):
    with WorkerTestTenant("Acme Test Provider") as a:
        oldest, newer = _waiting(a, 2, minutes_ago=20)
        for d in (oldest, newer):
            _set(d, "wait_cause = 'model_provider', waiting_since = now() - interval '5 minutes', "
                    "retry_at = now() + interval '10 minutes'")
        overloaded = ProviderError("wait", "overloaded", "http_529")
        results = [model_provider.record_failure(overloaded) for _ in range(PROVIDER_DOWN_FAILURES)]
        assert results == [False] * (PROVIDER_DOWN_FAILURES - 1) + [True]
        assert _provider_status() == "down"
        down = _alerts("model_api_failure")
        assert len(down) == 1 and down[0]["severity"] == "high"
        assert down[0]["payload"]["cause"] == "overloaded" and down[0]["payload"]["waiting_documents"] >= 2
        assert model_provider.record_failure(overloaded) is False  # still one alert for the outage
        assert len(_alerts("model_api_failure")) == 1

        # Holding: nothing goes but the probe. Being marked down stamps the
        # probe clock, so the very next pass sends nothing...
        recorder = _Recorder(a)
        try:
            held = dispatch.run_pass(recorder, target=WIDE_OPEN)
            assert held.holding and not held.probe and recorder.sent == []
            # ...and once PROVIDER_PROBE_MINUTES have passed (0 here), exactly
            # one document goes: the oldest one waiting on the provider.
            monkeypatch.setattr(dispatch, "PROVIDER_PROBE_MINUTES", 0)
            probe = dispatch.run_pass(recorder, target=WIDE_OPEN)
            assert probe.holding and probe.probe and len(recorder.sent) == 1, recorder.sent
            if recorder.ours():
                assert recorder.ours() == [(a.tenant_id, oldest)]
                assert _row(oldest)["retry_at"] is None  # sent before its own backoff ended

            # Recovery: the first success marks it up, releases the backlog
            # (retry_at cleared) and raises the notice.
            assert model_provider.record_success() is True
            assert _provider_status() == "up"
            assert _row(newer)["retry_at"] is None
            notice = _alerts("model_api_recovered")
            assert len(notice) == 1 and notice[0]["severity"] == "info"
            assert notice[0]["payload"]["documents_waited"] >= 1
            assert len(_alerts("model_api_failure")) == 1  # recovery acknowledges nothing
        finally:
            recorder.put_back_others()


def test_our_own_configuration_marks_the_provider_down_at_the_first_failure(clean_provider):
    """Founder, 2026-10-01: a wrong key, no credit or a retired model holds
    dispatch and alerts at once -- no 3-in-5 threshold."""
    assert model_provider.record_failure(ProviderError("wait", "our_configuration", "http_402")) is True
    assert _provider_status() == "down"
    assert _alerts("model_api_failure")[0]["payload"]["cause"] == "our_configuration"


def test_a_document_waiting_six_hours_on_the_provider_fails_doc_024_and_alerts(clean_provider):
    """Q7: after PROVIDER_MAX_WAIT_HOURS, failed with a code that blames the
    provider -- by the sweep, since a held document is never claimed. Time is
    moved in the database, not slept."""
    with WorkerTestTenant("Acme Test Wait Limit") as a:
        late, early = _waiting(a, 2, minutes_ago=600)
        _set(late, f"wait_cause = 'model_provider', waiting_since = now() - interval "
                   f"'{PROVIDER_MAX_WAIT_HOURS} hours 1 minute', retry_at = now() + interval '5 minutes'")
        _set(early, "wait_cause = 'model_provider', waiting_since = now() - interval '5 hours'")
        result = stuck_documents.sweep_tenant(a.tenant_id, lambda _t, _d: None)
        assert result.wait_limit_failed == [late]
        row = _row(late)
        assert (row["status"], row["failure_code"]) == ("failed", "DOC-024")
        assert _row(early)["status"] == "pending"
        with platform_session() as session:
            alert = session.execute(
                text("SELECT payload FROM founder_alerts WHERE tenant_id = :t AND type = 'document_failed'"),
                {"t": str(a.tenant_id)},
            ).scalar_one()
        assert alert["error_code"] == "DOC-024"


# ── the wait itself, through the real task ──────────────────────────────────


class _ProviderDownThenUp:
    """The Anthropic client: raises the provider's 529 `failures` times, then
    answers with `payload` (via the suite's FakeAnthropic)."""

    def __init__(self, failures: int, payload: dict):
        from tests.db_helpers import FakeAnthropic

        self.left = failures
        self.answer = FakeAnthropic(payload)

    def __call__(self, *args, **kwargs):
        return self

    @property
    def messages(self):
        return self

    def with_options(self, **kwargs):
        return self

    def count_tokens(self, **kwargs):
        return self.answer.count_tokens(**kwargs)

    def create(self, **kwargs):
        return self.answer.create(**kwargs)

    def stream(self, **kwargs):
        if self.left > 0:
            self.left -= 1
            import anthropic
            import httpx2

            request = httpx2.Request("POST", "https://api.anthropic.test/v1/messages")
            response = httpx2.Response(529, request=request, json={"type": "error", "error": {
                "type": "overloaded_error", "message": "Overloaded"}})
            raise anthropic.InternalServerError("Overloaded", response=response, body=response.json())
        return self.answer.stream(**kwargs)


def test_waiting_never_costs_a_try_and_the_wait_is_cleared_on_success(monkeypatch, clean_provider):
    """Item 8: a document that waits five times on the provider, then is
    read, has used one try, no timeout, and nothing left of its waits."""
    import app.tasks.parse_and_extract as task_module

    # Recorded only: the real provider row is exercised by the tests above.
    monkeypatch.setattr(task_module, "dispatch_after_task", lambda: None)
    monkeypatch.setattr(model_provider, "record_failure", lambda error: False)
    with WorkerTestTenant("Acme Test Waits") as tenant:
        document_id = tenant.create_pending_document()
        client = _ProviderDownThenUp(5, model_payload(lines=[{}]))
        monkeypatch.setattr(task_module.anthropic, "Anthropic", client)
        for attempt in range(5):
            task_module.parse_and_extract(str(tenant.tenant_id), str(document_id))
            row = _row(document_id)
            assert (row["status"], row["wait_cause"], row["last_wait_error"]) == (
                "pending", "model_provider", "http_529"
            ), attempt
            assert row["processing_attempts"] == 0 and row["dispatched_at"] is None
            assert row["retry_at"] is not None
            _set(document_id, "retry_at = NULL")  # the backoff, passed
        first_wait = _row(document_id)["waiting_since"]
        assert first_wait is not None

        run_extraction(monkeypatch, tenant, document_id, model_payload(lines=[{}]))
        row = _row(document_id)
        assert row["status"] == "needs_review", row["failure_code"]
        assert row["processing_attempts"] == 1 and list(row["timeout_attempts"] or []) == []
        assert (row["waiting_since"], row["wait_cause"], row["retry_at"]) == (None, None, None)


def test_a_document_inside_its_backoff_is_not_claimed_by_a_stray_job(clean_provider):
    with WorkerTestTenant("Acme Test Backoff Claim") as a:
        doc = _waiting(a, 1, minutes_ago=5)[0]
        _set(doc, "retry_at = now() + interval '10 minutes', wait_cause = 'storage'")
        with tenant_session(a.tenant_id) as session:
            assert document_status.claim_for_processing(session, doc) is False
        _set(doc, "retry_at = now() - interval '1 second'")
        with tenant_session(a.tenant_id) as session:
            assert document_status.claim_for_processing(session, doc) is True


# ── routing_model_failure (founder's two conditions) ────────────────────────


def test_routing_model_failure_is_raised_end_to_end_and_counts_the_documents_without_examples():
    _delete_tenant_less_alerts()
    retired = ProviderError("wait", "our_configuration", "http_404")
    try:
        for _ in range(3):
            model_provider.alert_routing_failure(retired)
        alerts = _alerts("routing_model_failure")
        assert len(alerts) == 1, alerts
        alert = alerts[0]
        assert alert["severity"] == "warning" and alert["tenant_id"] is None
        assert alert["payload"]["cause"] == "http_404"
        assert alert["payload"]["documents_without_examples"] == 3
        assert alert["payload"]["first_failure_at"]
        assert alert["email_outbox_id"] is not None or not get_settings().founder_alert_email
    finally:
        _delete_tenant_less_alerts()


# ── every alert type, raised for real (M6) ───────────────────────────────────


def test_every_registered_alert_type_can_be_raised_end_to_end():
    """Founder's condition, so M6 can't recur: each type in ALERT_TYPES is
    raised through `raise_alert` into the real table."""
    marker = "every-type-test"
    with platform_session() as session:
        for alert_type in founder_alerts.ALERT_TYPES:
            assert founder_alerts.raise_alert(
                session, alert_type=alert_type, severity="info", tenant_id=None,
                payload={"test": marker}, dedupe_key=f"{marker}:{alert_type}",
            ), alert_type
    try:
        with platform_session() as session:
            raised = {
                r[0]
                for r in session.execute(
                    text("SELECT type FROM founder_alerts WHERE payload->>'test' = :m"), {"m": marker}
                )
            }
        assert raised == set(founder_alerts.ALERT_TYPES)
    finally:
        with platform_session() as session:
            session.execute(
                text(
                    "DELETE FROM email_outbox WHERE id IN (SELECT email_outbox_id FROM founder_alerts "
                    "WHERE payload->>'test' = :m)"
                ),
                {"m": marker},
            )
            session.execute(text("DELETE FROM founder_alerts WHERE payload->>'test' = :m"), {"m": marker})


def test_m6_the_rollups_stale_alert_is_raised_through_its_own_session():
    """Review M6: raising rollup_stale used to raise ValueError inside the
    rollup. Through its real path and session now."""
    from docflow_core import metrics

    def open_ids() -> set[str]:
        with platform_session() as session:
            return {
                str(r[0])
                for r in session.execute(
                    text(
                        "SELECT id FROM founder_alerts "
                        "WHERE type = 'rollup_stale' AND acknowledged_at IS NULL"
                    )
                )
            }

    # A real open rollup_stale alert on this database is left alone: the
    # raise is then deduped (no row, no error), which proves the same path.
    before = open_ids()
    with rollup_session() as session:
        metrics._raise_stale_alert(session, None)  # raised ValueError before M6 was fixed
    after = open_ids()
    assert len(after) == 1, after
    ours = after - before
    if ours:
        with platform_session() as session:
            session.execute(
                text(
                    "DELETE FROM email_outbox WHERE id IN (SELECT email_outbox_id FROM founder_alerts "
                    "WHERE id = CAST(:id AS uuid))"
                ),
                {"id": next(iter(ours))},
            )
            session.execute(
                text("DELETE FROM founder_alerts WHERE id = CAST(:id AS uuid)"), {"id": next(iter(ours))}
            )


# ── the functions themselves (Q8 condition) ──────────────────────────────────

_FUNCTIONS = (
    "dispatch_candidates", "probe_candidate", "mark_dispatched", "clear_dispatched", "dispatcher_heartbeat",
    "dispatcher_status", "provider_state", "provider_record_failure", "provider_record_success",
    "provider_take_probe", "count_routing_model_failure",
)


def test_every_0035_function_is_security_definer_with_an_empty_search_path_and_no_public_execute():
    """Founder's Q8 condition: a fixed search_path (here '' -- nothing to
    shadow), every name schema-qualified in the source, and nothing callable
    by Supabase's public roles."""
    with platform_session() as session:
        rows = session.execute(
            text(
                """
                SELECT p.proname, p.prosecdef, p.proconfig,
                       has_function_privilege('docflow_app', p.oid, 'EXECUTE') AS app_can,
                       (SELECT bool_or(has_function_privilege(r.rolname, p.oid, 'EXECUTE'))
                          FROM pg_roles r WHERE r.rolname IN ('anon', 'authenticated')) AS public_can
                  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'public' AND p.proname = ANY(:names)
                """
            ),
            {"names": list(_FUNCTIONS)},
        ).mappings().all()
    found = {r["proname"]: r for r in rows}
    assert set(found) == set(_FUNCTIONS)
    for name, row in found.items():
        assert row["prosecdef"], name
        assert 'search_path=""' in (row["proconfig"] or []), (name, row["proconfig"])
        assert row["app_can"], name
        assert not row["public_can"], name


def test_the_flag_lets_the_dispatcher_raise_only_its_four_tenant_less_alerts():
    with pytest.raises(Exception):
        with dispatcher_session() as session:
            founder_alerts.raise_alert(
                session, alert_type="document_failed", severity="high", tenant_id=None, payload={}
            )
    with dispatcher_session() as session:
        assert session.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0  # reads nothing
