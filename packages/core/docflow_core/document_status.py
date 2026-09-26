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
    }
)
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


def claim_for_processing(
    session: Session, document_id: UUID, *, stale_after_min: int = STUCK_PROCESSING_TIMEOUT_MIN
) -> bool:
    """The worker's claim (H3). Takes a `pending` document, or one left in
    `processing` longer than the stuck timeout by a worker that died. Anything
    else -- already being processed, already in review, approved, exported --
    is not touched, so a redelivered or duplicated job is a no-op."""
    row = session.execute(
        text(
            """
            UPDATE documents
               SET status = 'processing',
                   processing_started_at = now(),
                   processing_attempts = processing_attempts + 1
             WHERE id = :id
               AND deleted_at IS NULL
               AND (status = 'pending'
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
