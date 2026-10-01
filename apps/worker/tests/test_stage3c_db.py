"""
Stage 3c against the real database (3c test table F3, F4's sweep case, F6,
F7, and item 6a's lost-try path).

- F3 (D-163): a worker killed during the model call leaves a started run
  row; the stuck sweep writes its outcome with the counted input's cost, and
  every cost reader counts outcome rows only.
- Item 6a: a lost parse try is recorded and decided at once -- retried in
  seconds, then DOC-022 naming the lost parse.
- F4 through the real sweep: crash, crash, timeout gets 3 tries, not 4.
- F6: DOC-029's founder alert at most once per tenant per UTC day.
- F7: parse_service_unavailable at most once per UTC hour, platform-wide.

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from docflow_core import founder_alerts, model_runs, parse_client, stuck_documents, usage
from docflow_core.db import platform_session, tenant_session
from docflow_core.extraction import extraction_input_cost
from sqlalchemy import text

from tests.conftest import requires_stage3c_schema
from tests.db_helpers import FakeAnthropic, WorkerTestTenant

WORKER_ROOT = Path(__file__).resolve().parents[1]

pytestmark = requires_stage3c_schema


def _row(sql: str, **params) -> dict:
    with platform_session() as session:
        return dict(session.execute(text(sql), params).mappings().one())


def _rows(sql: str, **params) -> list[dict]:
    with platform_session() as session:
        return [dict(r) for r in session.execute(text(sql), params).mappings().all()]


def _runs(document_id: UUID) -> list[dict]:
    return _rows(
        "SELECT id, run_kind, run_state, succeeded, error_code, input_tokens, counted_input_tokens, "
        "est_cost_usd, started_run_id FROM extraction_runs WHERE document_id = :d ORDER BY created_at",
        d=str(document_id),
    )


def _backdate_claim(document_id: UUID) -> None:
    with platform_session() as session:
        session.execute(
            text("UPDATE documents SET processing_started_at = now() - interval '2 hours' WHERE id = :id"),
            {"id": str(document_id)},
        )


# ── F3: D-163, a worker killed during the model call ────────────────────────


def test_F3_a_worker_killed_during_the_model_call_leaves_the_call_on_the_cost_record():
    with WorkerTestTenant("Acme Test Killed Mid Call") as tenant:
        document_id = tenant.create_pending_document()
        marker = Path(tempfile.mkdtemp()) / "reached"
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.kill_runner",
                str(tenant.tenant_id),
                str(document_id),
                str(marker),
                "model",
            ],
            cwd=WORKER_ROOT,
        )
        try:
            deadline = time.monotonic() + 180
            while not marker.exists():
                assert child.poll() is None, "the child exited before reaching the model call"
                assert time.monotonic() < deadline, "the child never reached the model call"
                time.sleep(0.5)
        finally:
            child.kill()  # SIGKILL / TerminateProcess: no cleanup runs
            child.wait(timeout=30)

        runs = _runs(document_id)
        assert [(r["run_kind"], r["run_state"], r["succeeded"]) for r in runs] == [
            ("extraction", "started", None)
        ]
        assert runs[0]["counted_input_tokens"] == FakeAnthropic.COUNTED_INPUT_TOKENS
        with tenant_session(tenant.tenant_id) as session:
            assert usage.daily_ai_spend(session, tenant.tenant_id) == (Decimal(0), 0)

        # The dead worker's claim goes stale; the sweep takes it over and closes the call.
        _backdate_claim(document_id)
        stuck_documents.sweep_tenant(tenant.tenant_id, lambda _t, _d: None)

        runs = _runs(document_id)
        assert [(r["run_state"], r["succeeded"], r["error_code"]) for r in runs] == [
            ("started", None, None),
            ("finished", False, model_runs.LOST_CALL_CODE),
        ]
        lost = runs[1]
        expected = extraction_input_cost(FakeAnthropic.COUNTED_INPUT_TOKENS)
        assert lost["started_run_id"] == runs[0]["id"]
        assert lost["input_tokens"] == FakeAnthropic.COUNTED_INPUT_TOKENS
        # numeric(10,4): Postgres rounds half away from zero, Python half-even.
        assert abs(lost["est_cost_usd"] - expected) <= Decimal("0.0001")
        document = _row("SELECT est_cost_usd FROM documents WHERE id = :id", id=str(document_id))
        assert document["est_cost_usd"] == lost["est_cost_usd"]
        # The cost breaker reads it, once.
        with tenant_session(tenant.tenant_id) as session:
            cost, tokens = usage.daily_ai_spend(session, tenant.tenant_id)
        assert (cost, tokens) == (lost["est_cost_usd"], FakeAnthropic.COUNTED_INPUT_TOKENS)

        # Closing again writes nothing (one outcome per start).
        with tenant_session(tenant.tenant_id) as session:
            assert model_runs.close_lost_runs(session, tenant.tenant_id, document_id) == Decimal(0)
        assert len(_runs(document_id)) == 2


def test_F3_a_finished_call_has_its_started_row_and_one_outcome(monkeypatch):
    from tests.db_helpers import model_payload, run_extraction

    with WorkerTestTenant("Acme Test Started Rows") as tenant:
        document_id = tenant.create_pending_document()
        run_extraction(
            monkeypatch,
            tenant,
            document_id,
            model_payload(
                header={"order_total": "570.00"},
                lines=[{"quantity": "12", "unit_price": "47.50", "line_total": "570.00"}],
            ),
        )
        runs = _runs(document_id)
        started = [r for r in runs if r["run_state"] == "started"]
        finished = [r for r in runs if r["run_state"] == "finished"]
        assert len(started) == 1 and len(finished) == 1
        assert finished[0]["started_run_id"] == started[0]["id"]
        assert finished[0]["succeeded"] is True and finished[0]["input_tokens"] == 1000
        assert started[0]["counted_input_tokens"] == FakeAnthropic.COUNTED_INPUT_TOKENS


def test_F3_the_database_refuses_a_started_row_that_claims_an_outcome():
    with WorkerTestTenant("Acme Test Run State Check") as tenant:
        document_id = tenant.create_pending_document()
        with pytest.raises(Exception) as excinfo:
            with platform_session() as session:
                session.execute(
                    text(
                        "INSERT INTO extraction_runs (tenant_id, document_id, run_state, succeeded) "
                        "VALUES (:t, :d, 'started', true)"
                    ),
                    {"t": str(tenant.tenant_id), "d": str(document_id)},
                )
        assert "extraction_runs_succeeded_matches_state" in str(excinfo.value)


# ── Item 6a: a lost parse try, decided at once ──────────────────────────────


def test_a_lost_parse_is_retried_at_once_then_fails_doc_022_naming_it(monkeypatch):
    import app.tasks.parse_and_extract as task_module

    def lost(content, filename):
        raise parse_client.ParseLost("ReadTimeout")

    sent: list[tuple] = []
    monkeypatch.setattr(task_module.parse_client, "parse_document", lost)
    monkeypatch.setattr(task_module.celery_app, "send_task", lambda name, args, queue: sent.append(args))

    with WorkerTestTenant("Acme Test Lost Parse") as tenant:
        document_id = tenant.create_pending_document()
        task_module.parse_and_extract(str(tenant.tenant_id), str(document_id))

        first = _row(
            "SELECT status, processing_attempts, parse_lost_attempts, processing_started_at "
            "FROM documents WHERE id = :id",
            id=str(document_id),
        )
        assert first["status"] == "processing" and first["parse_lost_attempts"] == [1]
        assert first["processing_started_at"] is None  # released: the next job claims it at once
        assert sent == [[str(tenant.tenant_id), str(document_id)]]

        task_module.parse_and_extract(str(tenant.tenant_id), str(document_id))  # the retry, lost too
        second = _row(
            "SELECT status, failure_code, parse_lost_attempts FROM documents WHERE id = :id",
            id=str(document_id),
        )
        assert (second["status"], second["failure_code"], second["parse_lost_attempts"]) == (
            "failed",
            "DOC-022",
            [1, 2],
        )
        assert len(sent) == 1
        alert = _row(
            "SELECT payload FROM founder_alerts WHERE tenant_id = :t AND type = 'document_stuck'",
            t=str(tenant.tenant_id),
        )
        assert alert["payload"]["cause"] == "timeout"
        assert alert["payload"]["cause_detail"] == "parse_lost"
        assert alert["payload"]["parse_lost_attempts"] == [1, 2]


# ── F4 through the real sweep: 3 tries, not 4 ───────────────────────────────


def test_F4_crash_crash_timeout_fails_at_three_tries_through_the_real_sweep():
    with WorkerTestTenant("Acme Test Three Tries") as tenant:
        document_id = tenant.create_pending_document()
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE documents SET status = 'processing', processing_attempts = 3, "
                    "timeout_attempts = '{3}', processing_started_at = now() - interval '2 hours', "
                    "created_at = now() - interval '2 hours' WHERE id = :id"
                ),
                {"id": str(document_id)},
            )
        queued: list[UUID] = []
        result = stuck_documents.sweep_tenant(tenant.tenant_id, lambda _t, d: queued.append(d))
        assert queued == [] and result.failed == [document_id]
        assert (
            _row("SELECT failure_code FROM documents WHERE id = :id", id=str(document_id))["failure_code"]
            == "DOC-022"
        )


# ── F6, F7: the parse alerts' limits ────────────────────────────────────────


def _parse_alerts(tenant_id: UUID, code: str) -> int:
    return len(
        _rows(
            "SELECT id FROM founder_alerts WHERE tenant_id = :t AND type = 'document_failed' "
            "AND payload->>'error_code' = :c",
            t=str(tenant_id),
            c=code,
        )
    )


def test_F6_doc_029s_alert_fires_at_most_once_per_tenant_per_day():
    with WorkerTestTenant("Acme Test DOC-029 A") as a, WorkerTestTenant("Acme Test DOC-029 B") as b:
        raised = []
        for cause in ("stopped:memory", "stopped:cpu", "stopped:wall_clock"):
            with tenant_session(a.tenant_id) as session:
                raised.append(
                    founder_alerts.raise_parse_alert(
                        session,
                        tenant_id=a.tenant_id,
                        document_id=uuid4(),
                        error_code="DOC-029",
                        cause=cause,
                        by_cause=False,
                    )
                )
        with tenant_session(b.tenant_id) as session:
            raised_b = founder_alerts.raise_parse_alert(
                session,
                tenant_id=b.tenant_id,
                document_id=uuid4(),
                error_code="DOC-029",
                cause="stopped:memory",
                by_cause=False,
            )
        assert raised == [True, False, False] and raised_b is True
        assert _parse_alerts(a.tenant_id, "DOC-029") == 1 and _parse_alerts(b.tenant_id, "DOC-029") == 1


def test_F6_a_crash_alert_is_once_per_cause_per_day():
    with WorkerTestTenant("Acme Test Parse Fault") as tenant:
        raised = []
        for cause in ("crashed:signal_11", "crashed:signal_11", "invalid:bad_part"):
            with tenant_session(tenant.tenant_id) as session:
                raised.append(
                    founder_alerts.raise_parse_alert(
                        session,
                        tenant_id=tenant.tenant_id,
                        document_id=uuid4(),
                        error_code="DOC-005",
                        cause=cause,
                        by_cause=True,
                    )
                )
        assert raised == [True, False, True]


def test_F6_every_seccomp_kill_alerts_and_says_why_there_is_no_syscall_number():
    """Founder, departure #1: SIGSYS is its own alert, never rate-limited."""
    with WorkerTestTenant("Acme Test Seccomp Kill") as tenant:
        documents = [uuid4(), uuid4(), uuid4()]
        raised = []
        for document_id in documents:
            with tenant_session(tenant.tenant_id) as session:
                raised.append(
                    founder_alerts.raise_parse_alert(
                        session,
                        tenant_id=tenant.tenant_id,
                        document_id=document_id,
                        error_code="DOC-005",
                        cause=founder_alerts.SECCOMP_KILL_CAUSE,
                        by_cause=True,
                    )
                )
        rows = _rows(
            "SELECT payload FROM founder_alerts WHERE type = 'parse_seccomp_kill' AND tenant_id = :t",
            t=str(tenant.tenant_id),
        )
        assert raised == [True, True, True]
        assert sorted(r["payload"]["ref_id"] for r in rows) == sorted(str(d) for d in documents)
        for row in rows:
            assert row["payload"]["syscall"] is None
            assert row["payload"]["syscall_unavailable"] == founder_alerts.SECCOMP_SYSCALL_UNAVAILABLE
            assert row["payload"]["signal"] == "SIGSYS (31)"
        # A seccomp kill is not also a rate-limited document_failed alert.
        assert _parse_alerts(tenant.tenant_id, "DOC-005") == 0


def test_F7_parse_service_unavailable_fires_once_per_hour_across_tenants():
    with WorkerTestTenant("Acme Test Unavailable A") as a, WorkerTestTenant("Acme Test Unavailable B") as b:
        with tenant_session(a.tenant_id) as session:
            founder_alerts.raise_parse_service_unavailable(
                session, tenant_id=a.tenant_id, where="worker", reason="no_connection"
            )
        with tenant_session(b.tenant_id) as session:
            second = founder_alerts.raise_parse_service_unavailable(
                session, tenant_id=b.tenant_id, where="worker", reason="no_connection"
            )
        hour = _row("SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24') AS h")["h"]
        open_this_hour = _rows(
            "SELECT tenant_id, dedupe_key FROM founder_alerts WHERE type = 'parse_service_unavailable' "
            "AND acknowledged_at IS NULL AND dedupe_key LIKE :k",
            k=f"parse_service_unavailable:{hour}%",
        )
        # One for the whole platform this hour (an earlier test run may have
        # raised it first, from another tenant), and B's wrote nothing.
        assert len(open_this_hour) == 1 and second is False
        # The key carries the database's UTC hour, so the next hour gets a new one.
        assert open_this_hour[0]["dedupe_key"].startswith(f"parse_service_unavailable:{hour}")
