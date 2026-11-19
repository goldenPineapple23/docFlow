"""
The stuck sweep and an email's text (B1; docs/designs/b1.md 2.2 step 9;
D-246), against the real database, Storage and parse service. The model is
a STUB, as in test_label_mail_body_db.py. All data is fictional.

A text still `pending` past STUCK_PROCESSING_TIMEOUT_MIN -- aged from its
label call's started row, or from the email's arrival before any claim --
becomes a document with no label call, `labelled` with no label. A text in
any other state is never touched, and a decided text is never picked up
again.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import anthropic
import httpx
import pytest
from docflow_core import mail_bodies, stuck_documents, usage
from docflow_core.constants import STUCK_PROCESSING_TIMEOUT_MIN
from docflow_core.db import platform_session, tenant_session
from docflow_core.mail_labels import mail_label_input_cost
from docflow_core.model_runs import LOST_CALL_CODE
from sqlalchemy import text

from tests.conftest import requires_documents_schema, requires_mail_body_schema
from tests.db_helpers import WorkerTestTenant
from tests.test_label_mail_body_db import (
    COVER_NOTE,
    COVER_TEXT,
    ORDER,
    ORDER_TEXT,
    StubLabelClient,
    _assert_decided,
    _assert_no_label,
    _email,
    _email_row,
    _ignored,
    _label_runs,
    _row,
    _run,
    _text_documents,
)

pytestmark = [requires_documents_schema, requires_mail_body_schema]

PAST = STUCK_PROCESSING_TIMEOUT_MIN + 10


def _sweep(tenant: WorkerTestTenant) -> stuck_documents.SweepResult:
    return stuck_documents.sweep_tenant(tenant.tenant_id, lambda tid, did: None)


def _age_email(raw_email_id: UUID, minutes: int) -> None:
    with platform_session() as session:
        session.execute(
            text("UPDATE raw_emails SET created_at = now() - make_interval(mins => :m) WHERE id = :e"),
            {"m": minutes, "e": str(raw_email_id)},
        )


def _age_started_runs(raw_email_id: UUID, minutes: int) -> None:
    with platform_session() as session:
        session.execute(
            text(
                "UPDATE extraction_runs SET created_at = now() - make_interval(mins => :m) "
                "WHERE raw_email_id = :e AND run_state = 'started'"
            ),
            {"m": minutes, "e": str(raw_email_id)},
        )


def _spend(tenant: WorkerTestTenant) -> Decimal:
    with tenant_session(tenant.tenant_id) as session:
        return usage.daily_ai_spend(session, tenant.tenant_id)[0]


def _document_cost(document_id) -> Decimal | None:
    return _row("SELECT est_cost_usd FROM documents WHERE id = :d", d=str(document_id))["est_cost_usd"]


def test_a_pending_text_past_the_timeout_becomes_a_document_with_no_call_and_is_never_taken_again():
    with WorkerTestTenant("Acme Test Sweep") as tenant:
        email = _email(tenant, ORDER_TEXT)
        _age_email(email, PAST)

        assert _sweep(tenant).texts_made_documents == [email]
        row = _assert_decided(email)
        _assert_no_label(row)
        assert row["outcome"] == "processed"
        [document] = _text_documents(email)
        assert document["status"] == "pending"  # the dispatcher sends it
        assert _label_runs(email) == [] and _ignored(email) == []

        assert _sweep(tenant).texts_made_documents == []
        assert len(_text_documents(email)) == 1


def test_a_pending_text_inside_the_timeout_is_left_for_its_task():
    with WorkerTestTenant("Acme Test Sweep") as tenant:
        email = _email(tenant, ORDER_TEXT)
        _age_email(email, STUCK_PROCESSING_TIMEOUT_MIN - 5)

        assert _sweep(tenant).texts_made_documents == []
        assert _email_row(email)["body_state"] == "pending" and _text_documents(email) == []


def test_a_claimed_text_is_aged_from_its_started_row_and_its_lost_call_is_on_the_record_once():
    with WorkerTestTenant("Acme Test Sweep") as tenant:
        email = _email(tenant, ORDER_TEXT)
        _age_email(email, 120)  # arrived long ago ...
        with tenant_session(tenant.tenant_id) as session:
            assert mail_bodies.claim_label_call(session, tenant.tenant_id, email, counted_input_tokens=900)
        # ... but its call started just now: still running, not taken.
        assert _sweep(tenant).texts_made_documents == []

        _age_started_runs(email, PAST)
        assert _sweep(tenant).texts_made_documents == [email]
        started, lost = _label_runs(email)
        assert lost["started_run_id"] is not None and lost["error_code"] == LOST_CALL_CODE
        assert lost["succeeded"] is False and lost["document_id"] is None
        assert lost["est_cost_usd"] == mail_label_input_cost(900)
        [document] = _text_documents(email)
        assert _document_cost(document["id"]) == lost["est_cost_usd"]
        assert _spend(tenant) == lost["est_cost_usd"]


@pytest.mark.parametrize("state", ["none", "held", "released"])
def test_a_text_that_is_not_pending_is_never_touched_however_old(state):
    with WorkerTestTenant("Acme Test Sweep") as tenant:
        email = _email(tenant, ORDER_TEXT)
        with platform_session() as session:
            session.execute(
                text("UPDATE raw_emails SET body_state = :s WHERE id = :e"), {"s": state, "e": str(email)}
            )
        _age_email(email, PAST * 10)

        assert _sweep(tenant).texts_made_documents == []
        assert _email_row(email)["body_state"] == state and _text_documents(email) == []


class _SweepDuringTheCall(StubLabelClient):
    """A STUB whose call outlives the timeout: the sweep runs while it is in
    flight, then the call answers."""

    def __init__(self, payload, tenant, email):
        super().__init__(payload)
        self.tenant, self.email = tenant, email

    def create(self, **kwargs):
        _age_started_runs(self.email, PAST)
        assert _sweep(self.tenant).texts_made_documents == [self.email]
        return super().create(**kwargs)


def test_the_sweep_then_a_late_task_with_a_non_order_label_leaves_one_document_and_one_outcome(monkeypatch):
    """Founder, 2026-11-18, item a: whichever comes second does nothing."""
    with WorkerTestTenant("Acme Test Sweep") as tenant:
        email = _email(tenant, COVER_TEXT)  # text only: the task alone would write INT-012
        _run(monkeypatch, tenant, email, _SweepDuringTheCall(COVER_NOTE, tenant, email))

        assert len(_text_documents(email)) == 1
        assert _ignored(email) == []
        row = _assert_decided(email)
        _assert_no_label(row)  # the late task's label never reached the row
        runs = _label_runs(email)
        assert [r["run_state"] for r in runs] == ["started", "finished"]  # one outcome per start
        assert _spend(tenant) == runs[1]["est_cost_usd"]  # the call counted once


def test_a_text_the_sweep_takes_after_a_failed_final_write_carries_that_call_once(monkeypatch):
    with WorkerTestTenant("Acme Test Sweep") as tenant:
        email = _email(tenant, ORDER_TEXT)

        def broken_settle(*args, **kwargs):
            raise RuntimeError("settle failed")

        with monkeypatch.context() as patch:
            patch.setattr(mail_bodies, "settle_body", broken_settle)
            with pytest.raises(RuntimeError):
                _run(patch, tenant, email, StubLabelClient(ORDER))
        [_, written_alone] = _label_runs(email)
        assert written_alone["succeeded"] is True and written_alone["est_cost_usd"] > 0

        _age_started_runs(email, PAST)
        assert _sweep(tenant).texts_made_documents == [email]
        assert len(_label_runs(email)) == 2  # nothing lost to close
        [document] = _text_documents(email)
        assert _document_cost(document["id"]) == written_alone["est_cost_usd"]
        assert _spend(tenant) == written_alone["est_cost_usd"]


def test_a_text_that_cannot_be_decided_never_stops_the_rest_of_the_tenants_sweep(monkeypatch):
    with WorkerTestTenant("Acme Test Sweep") as tenant:
        broken = _email(tenant, ORDER_TEXT)
        fine = _email(tenant, COVER_TEXT)
        _age_email(broken, PAST + 5)  # older: taken first
        _age_email(fine, PAST)
        stuck = tenant.create_pending_document()
        with platform_session() as session:
            session.execute(
                text(
                    "UPDATE documents SET status = 'processing', processing_attempts = 1, "
                    "processing_started_at = now() - make_interval(mins => :m) WHERE id = :d"
                ),
                {"m": PAST, "d": str(stuck)},
            )
        real = mail_bodies.settle_stuck_text

        def settle_or_fail(session, tenant_id, raw_email_id):
            if raw_email_id == broken:
                raise RuntimeError("cannot settle")
            return real(session, tenant_id, raw_email_id)

        monkeypatch.setattr(mail_bodies, "settle_stuck_text", settle_or_fail)
        enqueued: list = []
        result = stuck_documents.sweep_tenant(tenant.tenant_id, lambda tid, did: enqueued.append(did))

        assert result.requeued == [stuck] and enqueued == [stuck]  # the document still goes on
        assert result.texts_made_documents == [fine]
        assert _email_row(broken)["body_state"] == "pending" and _text_documents(broken) == []


@pytest.mark.parametrize("path", ["call_failed", "hash_mismatch", "text_unreadable"])
def test_a_text_decided_with_no_label_is_never_picked_up_again(monkeypatch, path):
    """Founder, 2026-11-18, answer 1: each no-label path ends `labelled`
    with one document, and the sweep never takes the text again."""
    with WorkerTestTenant("Acme Test Sweep") as tenant:
        client = StubLabelClient(ORDER)
        if path == "call_failed":
            email = _email(tenant, ORDER_TEXT)
            client.error = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
        elif path == "hash_mismatch":
            email = _email(tenant, ORDER_TEXT, sha256="0" * 64)
        else:
            email = _email(tenant, ORDER_TEXT)

            def never_got_in(content, filename):
                from docflow_core import parse_client

                raise parse_client.ParseUnavailable("connect")

            monkeypatch.setattr("docflow_core.parse_client.parse_document", never_got_in)
        _run(monkeypatch, tenant, email, client)
        _assert_no_label(_assert_decided(email))
        assert len(_text_documents(email)) == 1

        _age_email(email, PAST * 2)
        _age_started_runs(email, PAST * 2)
        assert _sweep(tenant).texts_made_documents == []
        assert len(_text_documents(email)) == 1
