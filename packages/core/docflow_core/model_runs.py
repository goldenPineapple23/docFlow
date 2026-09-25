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
"""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core.extraction import ExtractionResult, RoutingResult

_INSERT = text(
    """
    INSERT INTO extraction_runs
        (id, tenant_id, document_id, run_kind, model_id, prompt_hash, schema_version,
         raw_response, input_tokens, output_tokens, example_input_tokens, est_cost_usd,
         latency_ms, succeeded, error_code, examples_used, created_at)
    VALUES
        (:id, :tenant_id, :document_id, :run_kind, :model_id, :prompt_hash, :schema_version,
         CAST(:raw_response AS jsonb), :input_tokens, :output_tokens, :example_input_tokens,
         :est_cost_usd, :latency_ms, :succeeded, :error_code, CAST(:examples_used AS jsonb),
         clock_timestamp())
    """
)
# clock_timestamp(), not the column default now(): the routing run and the
# extraction run are written in ONE transaction, and now() is frozen for the
# whole transaction -- both rows would share a created_at and "ORDER BY
# created_at" could put the extraction first (found on the 5.10 drive; the
# same trap as D-123).


def _money(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def record_extraction(
    session: Session, tenant_id: UUID, document_id: UUID, result: ExtractionResult
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
        },
    )
    return run_id


def record_routing(
    session: Session, tenant_id: UUID, document_id: UUID, routing: RoutingResult
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
        },
    )
    return run_id
