"""
Every change to a document's status goes through here (review findings H1, H3;
DECISIONS.md D-158).

A status change is a compare-and-set: `UPDATE ... WHERE status = ANY(<what the
caller expects>)`. If the row has moved on since the caller looked -- another
worker claimed it, a reviewer approved it, a redelivered job arrived late --
nothing is written and the caller is told so, instead of one writer silently
overwriting another. Behind this, the database refuses any transition not in
the list below (migration 0027's trigger), so a path that forgets to come
through here still cannot, for example, move an approved order back to
`processing`.

`ALLOWED` is the same list as `document_status_transition_allowed()` in 0027;
a test asserts the two agree.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow_core.constants import STUCK_PROCESSING_TIMEOUT_MIN
from docflow_core.db import rowcount

STATUSES = (
    "pending",
    "staged",
    "quarantined",
    "processing",
    "needs_review",
    "failed",
    "approved",
    "exported",
    "rejected",
)

ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        ("pending", "processing"),
        ("pending", "quarantined"),
        ("pending", "failed"),
        ("staged", "pending"),
        ("quarantined", "pending"),
        # Stage 3d (0035): waits out a provider, Storage or parse-service outage.
        ("processing", "pending"),
        ("processing", "needs_review"),
        ("processing", "failed"),
        ("needs_review", "approved"),
        ("needs_review", "rejected"),
        ("approved", "exported"),
        ("approved", "needs_review"),
        ("exported", "needs_review"),
        ("rejected", "needs_review"),
    }
)

# Columns a transition may set alongside the status. A fixed list: the names
# are interpolated into SQL, so nothing outside it can ever reach the string.
_SETTABLE: frozenset[str] = frozenset(
    {
        "model_id",
        "prompt_hash",
        "schema_version",
        "input_tokens",
        "output_tokens",
        "est_cost_usd",
        "injection_suspected",
        "overall_confidence",
        "raw_json",
        "field_schema_version",
        "current_extraction_run_id",
        "processed_at",
        "approved_at",
        "approved_by",
        "approved_json",
        "approved_snapshot_hash",
        "quarantine_reason",
        "quarantined_at",
        "released_at",
        "released_by_user_id",
        "released_acting_as_tenant_id",
        "processing_started_at",
        "failure_code",
        "pipeline_issues",
        # Stage 3d: a document leaving the wait loop (for review or failure)
        # clears what its waits recorded.
        "waiting_since",
        "wait_cause",
        "retry_at",
        "last_wait_error",
        # Stage 3d (Q3): set when a release or test-batch run makes a document
        # `pending`.
        "dispatch_lane",
    }
)

# Stage 3d: what a move out of the wait loop sets, so a reviewed or failed
# document never still reads as waiting.
WAIT_CLEARED: dict[str, Any] = {
    "waiting_since": None,
    "wait_cause": None,
    "retry_at": None,
    "last_wait_error": None,
}
# Values that are SQL, not bound parameters.
NOW = object()
NULL = object()


class IllegalTransition(ValueError):
    """A caller asked for a change the state machine doesn't have. A bug, not
    a race: a race is answered by `transition` returning False."""


def is_allowed(old: str, new: str) -> bool:
    return old == new or (old, new) in ALLOWED


def _check(from_statuses: Iterable[str], to: str) -> list[str]:
    sources = list(from_statuses)
    if not sources:
        raise IllegalTransition(f"no source status given for -> {to}")
    for source in sources:
        if source != to and (source, to) not in ALLOWED:
            raise IllegalTransition(f"{source} -> {to}")
    return sources


def _assignments(values: Mapping[str, Any] | None) -> tuple[list[str], dict[str, Any]]:
    parts: list[str] = []
    params: dict[str, Any] = {}
    for name, value in (values or {}).items():
        if name not in _SETTABLE:
            raise IllegalTransition(f"column {name!r} can't be set by a status transition")
        if value is NOW:
            parts.append(f"{name} = now()")
        elif value is NULL:
            parts.append(f"{name} = NULL")
        else:
            parts.append(f"{name} = :v_{name}")
            params[f"v_{name}"] = value
    return parts, params


def transition(
    session: Session,
    document_id: UUID,
    *,
    from_statuses: Iterable[str],
    to: str,
    values: Mapping[str, Any] | None = None,
) -> bool:
    """Move one document from any of `from_statuses` to `to`, setting `values`
    in the same statement. True if it moved; False if it was no longer in any
    of `from_statuses` (someone else moved it first) -- nothing is written."""
    sources = _check(from_statuses, to)
    parts, params = _assignments(values)
    row = session.execute(
        text(
            "UPDATE documents SET "
            + ", ".join(["status = :to", *parts])
            + " WHERE id = :id AND status = ANY(string_to_array(:from_statuses, ',')) RETURNING id"
        ),
        {"id": str(document_id), "to": to, "from_statuses": ",".join(sources), **params},
    ).first()
    return row is not None


def transition_many(
    session: Session,
    tenant_id: UUID,
    document_ids: Iterable[UUID],
    *,
    from_statuses: Iterable[str],
    to: str,
    values: Mapping[str, Any] | None = None,
) -> list[UUID]:
    """The same compare-and-set for a batch (release, run extraction). Returns
    the ids that moved; any that had moved on are left alone."""
    ids = [str(i) for i in document_ids]
    if not ids:
        return []
    sources = _check(from_statuses, to)
    parts, params = _assignments(values)
    rows = session.execute(
        text(
            "UPDATE documents SET "
            + ", ".join(["status = :to", *parts])
            + " WHERE tenant_id = :tenant_id"
            " AND id = ANY(CAST(string_to_array(:ids, ',') AS uuid[]))"
            " AND status = ANY(string_to_array(:from_statuses, ',')) RETURNING id"
        ),
        {
            "tenant_id": str(tenant_id),
            "ids": ",".join(ids),
            "to": to,
            "from_statuses": ",".join(sources),
            **params,
        },
    ).all()
    return [UUID(str(row[0])) for row in rows]


def record_timeout(session: Session, document_id: UUID) -> bool:
    """
    Record that the document's current attempt ended at the document task's
    hard time limit (Stage 3a). Called from the Celery worker's MAIN process
    (app.timeouts), which is told about every timeout even when the task's
    own process was killed in C code and could write nothing. The stuck sweep
    reads it (its `decide`: a timeout gets one retry).

    Idempotent per attempt -- the attempt number (`processing_attempts`, set
    by `claim_for_processing`) is appended only if it isn't there yet, since
    Celery can report a soft and a hard timeout for one attempt -- and only
    while the document is still `processing`. Returns whether it wrote.
    `session` must be the document's tenant session.
    """
    result = session.execute(
        text(
            """
            UPDATE documents
               SET timeout_attempts = array_append(timeout_attempts, processing_attempts)
             WHERE id = :id
               AND status = 'processing'
               AND NOT (processing_attempts = ANY(timeout_attempts))
            """
        ),
        {"id": str(document_id)},
    )
    return bool(rowcount(result))


def record_parse_lost(session: Session, document_id: UUID) -> tuple[int, list[int], list[int]] | None:
    """
    Stage 3c (item 6a): this attempt's parse request got in and never came
    out. Append the attempt number to `parse_lost_attempts` (migration 0034),
    idempotent per attempt like `record_timeout`, and return what `decide()`
    needs: (processing_attempts, timeout_attempts, parse_lost_attempts).
    None when the document is no longer `processing`.
    """
    row = session.execute(
        text(
            """
            UPDATE documents
               SET parse_lost_attempts = CASE
                     WHEN processing_attempts = ANY(parse_lost_attempts) THEN parse_lost_attempts
                     ELSE array_append(parse_lost_attempts, processing_attempts)
                   END
             WHERE id = :id AND status = 'processing' AND deleted_at IS NULL
            RETURNING processing_attempts, timeout_attempts, parse_lost_attempts
            """
        ),
        {"id": str(document_id)},
    ).first()
    if row is None:
        return None
    return int(row[0]), [int(n) for n in (row[1] or [])], [int(n) for n in (row[2] or [])]


def release_claim(session: Session, document_id: UUID) -> bool:
    """
    Stage 3c (item 6a): a lost parse try that `decide()` says to retry. The
    attempt stays counted (it was a real try); only the claim's stamp is
    cleared, so the very next job's `claim_for_processing` takes it over at
    once instead of after STUCK_PROCESSING_TIMEOUT_MIN.
    """
    result = session.execute(
        text(
            """
            UPDATE documents SET processing_started_at = NULL
             WHERE id = :id AND status = 'processing' AND deleted_at IS NULL
            """
        ),
        {"id": str(document_id)},
    )
    return bool(rowcount(result))


def claim_for_processing(
    session: Session, document_id: UUID, *, stale_after_min: int = STUCK_PROCESSING_TIMEOUT_MIN
) -> bool:
    """The worker's claim (H3). Takes a `pending` document, or one left in
    `processing` longer than the stuck timeout by a worker that died. Anything
    else -- already being processed, already in review, approved, exported --
    is not touched, so a redelivered or duplicated job is a no-op.

    Stage 3d: a `pending` document still inside a wait's backoff (`retry_at`
    ahead) is not taken either, so a stray duplicate job can't retry it
    early; the dispatcher clears `retry_at` when it sends a document."""
    row = session.execute(
        text(
            """
            UPDATE documents
               SET status = 'processing',
                   processing_started_at = now(),
                   processing_attempts = processing_attempts + 1
             WHERE id = :id
               AND deleted_at IS NULL
               AND ((status = 'pending' AND (retry_at IS NULL OR retry_at <= now()))
                    OR (status = 'processing'
                        -- NULL: claimed before migration 0027 stamped claims.
                        AND (processing_started_at IS NULL
                             OR processing_started_at < now() - make_interval(mins => :stale))))
            RETURNING id
            """
        ),
        {"id": str(document_id), "stale": stale_after_min},
    ).first()
    return row is not None


WAIT_CAUSES = ("model_provider", "storage", "parse_service")


def to_waiting(
    session: Session,
    document_id: UUID,
    *,
    cause: str,
    retry_after_seconds: int,
    error_label: str,
) -> bool:
    """
    Stage 3d (item 4): the worker couldn't finish this attempt because
    something DocFlow depends on was down -- the model provider, Storage, or
    the parse service (a request that never got in). That is never the
    document's fault, so it waits instead of failing.

    `processing -> pending` (migration 0035), in one compare-and-set:
    - the attempt is given back, so waiting never uses up
      MAX_PROCESSING_ATTEMPTS or looks like a timeout (DOC-022, 3a);
    - `dispatched_at` and the claim stamp are cleared: it is waiting again,
      not a lost job, and only the dispatcher sends it on;
    - `waiting_since` is kept from the first wait of the same cause (the
      6-hour maximum counts from there) and restarts when the cause changes;
    - `retry_at` is when the dispatcher may send it again;
    - `last_wait_error` is a status code or fixed label (Section 7.10).
    Returns False if the document had already left `processing`.
    """
    if cause not in WAIT_CAUSES:
        raise ValueError(f"unknown wait cause {cause!r}")
    result = session.execute(
        text(
            """
            UPDATE documents
               SET status = 'pending',
                   processing_attempts = GREATEST(processing_attempts - 1, 0),
                   processing_started_at = NULL,
                   dispatched_at = NULL,
                   waiting_since = CASE WHEN wait_cause = :cause
                                        THEN coalesce(waiting_since, now()) ELSE now() END,
                   wait_cause = :cause,
                   retry_at = now() + make_interval(secs => :retry_after),
                   last_wait_error = :error_label
             WHERE id = :id AND status = 'processing' AND deleted_at IS NULL
            """
        ),
        {
            "id": str(document_id),
            "cause": cause,
            "retry_after": int(retry_after_seconds),
            "error_label": error_label[:120],
        },
    )
    return bool(rowcount(result))


def fail_provider_waits_past(session: Session, *, hours: int, failure_code: str) -> list[UUID]:
    """
    Stage 3d: every document in the session's tenant that has waited `hours`
    on the model provider, failed with `failure_code` (DOC-024) --
    `pending -> failed`, compare-and-set on the status. While the provider is
    down the dispatcher sends only its probe, so these are never claimed to
    reach the limit themselves; the stuck sweep calls this. Returns the ids.
    """
    rows = session.execute(
        text(
            """
            UPDATE documents
               SET status = 'failed', failure_code = :code, processed_at = now(),
                   retry_at = NULL, dispatched_at = NULL
             WHERE status = 'pending' AND deleted_at IS NULL
               AND wait_cause = 'model_provider'
               AND waiting_since < now() - make_interval(hours => :hours)
            RETURNING id
            """
        ),
        {"code": failure_code, "hours": hours},
    ).all()
    return [UUID(str(row[0])) for row in rows]


def waited_seconds(session: Session, document_id: UUID, cause: str) -> int | None:
    """How long the document has been waiting on `cause`, counted from its
    first wait (None if it isn't waiting on it). Read before `to_waiting` to
    choose the backoff step and to apply the maximum."""
    row = session.execute(
        text(
            """
            SELECT floor(extract(epoch FROM now() - waiting_since))::integer
              FROM documents
             WHERE id = :id AND wait_cause = :cause AND waiting_since IS NOT NULL
            """
        ),
        {"id": str(document_id), "cause": cause},
    ).first()
    return None if row is None else int(row[0])
