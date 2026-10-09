"""
One `extraction_runs` row per model call (Section 9; slice 5.10, D-142).

The table has existed since 0004 and two things already read it -- the
per-tenant daily cost circuit breaker (`usage.daily_ai_spend`, Section 7.9 /
7.16.2) and the Console health strip's model error rate -- but until this
slice nothing wrote to it, so the breaker could never trip on real spend.
Every call is recorded now, succeeded or not, the cheap routing read included
(`run_kind = 'buyer_routing'`): a model call that cost money counts, whichever
model made it.

The raw response is stored because Section 7.1 keeps the model's output
immutably; it is customer data and is never logged (Section 7.10).

Since Stage 3c (D-163, migration 0034) every paid call is two rows, never an
update: a `started` row committed before the call (`record_started`, with
the input tokens counted beforehand), and the outcome row after it, which
points back at it. A started row with no outcome means the worker died
during the call; `close_lost_runs` writes its outcome so the call's cost is
on the record as a lower bound instead of lost. Every reader that counts runs
or sums their cost reads `run_state = 'finished'` rows only.
"""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core.extraction import (
    EXTRACTION_MODEL,
    ExtractionResult,
    RoutingResult,
    extraction_input_cost,
    routing_input_cost,
)

_INSERT = text(
    """
    INSERT INTO extraction_runs
        (id, tenant_id, document_id, run_kind, model_id, prompt_hash, schema_version,
         raw_response, input_tokens, output_tokens, example_input_tokens, est_cost_usd,
         latency_ms, succeeded, error_code, examples_used, started_run_id, created_at)
    VALUES
        (:id, :tenant_id, :document_id, :run_kind, :model_id, :prompt_hash, :schema_version,
         CAST(:raw_response AS jsonb), :input_tokens, :output_tokens, :example_input_tokens,
         :est_cost_usd, :latency_ms, :succeeded, :error_code, CAST(:examples_used AS jsonb),
         :started_run_id, clock_timestamp())
    """
)
# clock_timestamp(), not the column default now(): the routing run and the
# extraction run are written in ONE transaction, and now() is frozen for the
# whole transaction -- both rows would share a created_at and "ORDER BY
# created_at" could put the extraction first (found on the 5.10 drive; the
# same trap as D-123).


def _money(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def record_started(
    session: Session,
    tenant_id: UUID,
    document_id: UUID,
    *,
    run_kind: str,
    model_id: str,
    counted_input_tokens: int | None,
) -> UUID:
    """The row committed before a paid call (D-163): which model, which kind
    of call, and the input it is about to pay for, counted beforehand (None
    when the count itself failed: the call is still on the record)."""
    run_id = uuid4()
    session.execute(
        text(
            """
            INSERT INTO extraction_runs
                (id, tenant_id, document_id, run_kind, model_id, run_state, succeeded,
                 counted_input_tokens, created_at)
            VALUES (:id, :tenant_id, :document_id, :run_kind, :model_id, 'started', NULL,
                    :counted, clock_timestamp())
            """
        ),
        {
            "id": str(run_id),
            "tenant_id": str(tenant_id),
            "document_id": str(document_id),
            "run_kind": run_kind,
            "model_id": model_id,
            "counted": counted_input_tokens,
        },
    )
    return run_id


def set_document_cost(session: Session, document_id: UUID) -> None:
    """
    The document's own est_cost_usd, set from the cost record: the sum of its
    finished runs (outcome rows only, D-163), NULL while none has a cost.
    Called in the transaction that writes an outcome row, so the figure the
    Console, the rollup and cost per document read can never drop a paid call
    -- a lost one, a failed one, an earlier try's. Never set it any other way.
    """
    session.execute(
        text(
            """
            UPDATE documents
               SET est_cost_usd = (SELECT sum(r.est_cost_usd) FROM extraction_runs r
                                    WHERE r.document_id = :id AND r.run_state = 'finished')
             WHERE id = :id
            """
        ),
        {"id": str(document_id)},
    )


LOST_CALL_CODE = "worker_lost_during_call"


def close_lost_runs(session: Session, tenant_id: UUID, document_id: UUID) -> Decimal:
    """
    Outcome rows for this document's started calls that have none: the worker
    died during the call (a crash, a lost machine; D-163). Not succeeded, the
    counted input priced as a lower bound, `cost_complete: false` (the output
    written before the death is unknown, as for a dropped stream). Called by
    the stuck sweep when it takes the document over, and by the next claim.
    Puts the cost on the document's own est_cost_usd (`set_document_cost`)
    and returns it. Safe to run twice: one outcome per start (unique index),
    ON CONFLICT does nothing.
    """
    rows = session.execute(
        text(
            """
            SELECT s.id, s.run_kind, s.model_id, s.counted_input_tokens
              FROM extraction_runs s
             WHERE s.document_id = :document_id
               AND s.run_state = 'started'
               AND NOT EXISTS (SELECT 1 FROM extraction_runs o WHERE o.started_run_id = s.id)
            """
        ),
        {"document_id": str(document_id)},
    ).mappings().all()
    added = Decimal(0)
    for row in rows:
        counted = row["counted_input_tokens"]
        if counted is None:
            cost = None
        elif row["run_kind"] == "extraction" and row["model_id"] == EXTRACTION_MODEL:
            cost = extraction_input_cost(int(counted))
        elif row["run_kind"] == "buyer_routing":
            cost = routing_input_cost(int(counted))
        else:
            cost = None  # a model this code doesn't price any more: tokens only
        inserted = session.execute(
            text(
                """
                INSERT INTO extraction_runs
                    (id, tenant_id, document_id, run_kind, model_id, raw_response, input_tokens,
                     est_cost_usd, succeeded, error_code, started_run_id, created_at)
                VALUES (:id, :tenant_id, :document_id, :run_kind, :model_id, CAST(:raw AS jsonb),
                        :input_tokens, :cost, false, :code, :started, clock_timestamp())
                ON CONFLICT (started_run_id) WHERE started_run_id IS NOT NULL DO NOTHING
                RETURNING id
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": str(tenant_id),
                "document_id": str(document_id),
                "run_kind": row["run_kind"],
                "model_id": row["model_id"],
                "raw": json.dumps({"lost": True, "cost_complete": False}),
                "input_tokens": counted,
                "cost": _money(cost),
                "code": LOST_CALL_CODE,
                "started": str(row["id"]),
            },
        ).first()
        if inserted is not None and cost is not None:
            added += cost
    if added:
        # The document's own cost (cost per document, the rollup) sees it too.
        set_document_cost(session, document_id)
    return added


def record_extraction(
    session: Session,
    tenant_id: UUID,
    document_id: UUID,
    result: ExtractionResult,
    *,
    started_run_id: UUID | None = None,
) -> UUID:
    run_id = uuid4()
    session.execute(
        _INSERT,
        {
            "id": str(run_id),
            "tenant_id": str(tenant_id),
            "document_id": str(document_id),
            "run_kind": "extraction",
            "model_id": result.model_id,
            "prompt_hash": result.prompt_hash,
            "schema_version": result.schema_version,
            "raw_response": json.dumps(result.raw_response),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "example_input_tokens": result.example_input_tokens,
            "est_cost_usd": _money(result.est_cost_usd),
            "latency_ms": result.latency_ms,
            "succeeded": result.ok,
            "error_code": result.error_code,
            "examples_used": json.dumps(list(result.examples_used)),
            "started_run_id": str(started_run_id) if started_run_id else None,
        },
    )
    return run_id


def record_routing(
    session: Session,
    tenant_id: UUID,
    document_id: UUID,
    routing: RoutingResult,
    *,
    started_run_id: UUID | None = None,
) -> UUID:
    run_id = uuid4()
    session.execute(
        _INSERT,
        {
            "id": str(run_id),
            "tenant_id": str(tenant_id),
            "document_id": str(document_id),
            "run_kind": "buyer_routing",
            "model_id": routing.model_id,
            "prompt_hash": routing.prompt_hash,
            "schema_version": routing.schema_version,
            "raw_response": json.dumps(routing.raw_response),
            "input_tokens": routing.input_tokens,
            "output_tokens": routing.output_tokens,
            "example_input_tokens": None,
            "est_cost_usd": _money(routing.est_cost_usd),
            "latency_ms": routing.latency_ms,
            "succeeded": routing.ok,
            "error_code": routing.error_code,
            "examples_used": "[]",
            "started_run_id": str(started_run_id) if started_run_id else None,
        },
    )
    return run_id
