"""
Is the model provider up? (Stage 3d; BUILD-STATUS "3d detailed design" item
5 and "3d -- APPROVED WITH CHANGES" change A.)

One global row per provider (`model_provider_state`, migration 0035), read and
written only through 0035's SECURITY DEFINER functions:

* A waiting-class failure (`provider_errors`) is recorded. The provider is
  marked **down** at the PROVIDER_DOWN_FAILURES-th failure within
  PROVIDER_DOWN_WINDOW_MIN minutes, from any tenants, with no success between
  them -- or at once for our own configuration (a wrong key, no credit, a
  retired model), which no amount of waiting fixes by itself. Being marked
  down raises ONE high-severity `model_api_failure` alert for the outage
  (keyed by its start), with no tenant.
* The first success marks it **up**, releases every document waiting on it
  at once (their `retry_at` is cleared, so the backlog goes out through the
  normal turn-taking) and raises an info `model_api_recovered` notice: how
  long it was down, how many documents waited, how many reached the limit.
  Recovery does not acknowledge the down alert: that stays a human action.
* While it is down the dispatcher holds everything and sends one probe every
  PROVIDER_PROBE_MINUTES (docflow_core.dispatch).

Also here: `routing_model_failure` (founder, 2026-10-01), for the routing
call's own model refusing us -- extraction goes ahead without examples, so
nothing is held, but the founder is told once a day with a running count.

Alert payloads carry causes, labels, times and counts only (Section 7.10).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text

from docflow_core import founder_alerts
from docflow_core.constants import (
    PROVIDER_DOWN_FAILURES,
    PROVIDER_DOWN_WINDOW_MIN,
    PROVIDER_PROBE_MINUTES,
    PROVIDER_RETRY_MINUTES,
)
from docflow_core.db import dispatcher_session
from docflow_core.provider_errors import CAUSE_OUR_CONFIGURATION, ProviderError

logger = logging.getLogger(__name__)

PROVIDER = "anthropic"


def retry_delay_seconds(waited_seconds: int | None, retry_after_seconds: int | None = None) -> int:
    """
    How long a document waits before its next try, stepped by how long it has
    already waited: 1, 2, 4, 8 and then 15 minutes (PROVIDER_RETRY_MINUTES),
    so the tries fall at about 1, 3, 7, 15, 30, 45... minutes. Derived from
    the time since the first wait, so no counter is stored. A 429's
    Retry-After is a floor.
    """
    elapsed_min = (waited_seconds or 0) / 60
    starts = 0.0
    step = PROVIDER_RETRY_MINUTES[0]
    for minutes in PROVIDER_RETRY_MINUTES:
        if elapsed_min < starts:
            break
        step = minutes
        starts += minutes
    return max(step * 60, retry_after_seconds or 0)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


def is_down() -> bool:
    with dispatcher_session() as session:
        row = session.execute(
            text("SELECT status FROM public.provider_state(:p)"), {"p": PROVIDER}
        ).first()
    return row is not None and row[0] == "down"


def take_probe() -> bool:
    """True for the one pass that may send the probe now (while down)."""
    with dispatcher_session() as session:
        return bool(
            session.execute(
                text("SELECT public.provider_take_probe(:p, :minutes)"),
                {"p": PROVIDER, "minutes": PROVIDER_PROBE_MINUTES},
            ).scalar_one()
        )


def record_failure(error: ProviderError) -> bool:
    """A waiting-class failure. Returns True if it marked the provider down
    (and the outage's one alert was raised). Never raises: the document's own
    wait has already been recorded by the caller."""
    try:
        with dispatcher_session() as session:
            row = session.execute(
                text(
                    "SELECT newly_down, down_since, waiting FROM public.provider_record_failure"
                    "(:p, :cause, :error, :immediate, :threshold, :window)"
                ),
                {
                    "p": PROVIDER,
                    "cause": error.cause,
                    "error": error.label,
                    "immediate": error.cause == CAUSE_OUR_CONFIGURATION,
                    "threshold": PROVIDER_DOWN_FAILURES,
                    "window": PROVIDER_DOWN_WINDOW_MIN,
                },
            ).mappings().one()
            if not row["newly_down"]:
                return False
            down_since = _iso(row["down_since"])
            founder_alerts.raise_alert(
                session,
                alert_type="model_api_failure",
                severity="high",
                tenant_id=None,
                payload={
                    "provider": PROVIDER,
                    "cause": error.cause,
                    "last_error": error.label,
                    "down_since": down_since,
                    "waiting_documents": int(row["waiting"]),
                },
                dedupe_key=f"model_api_failure:{PROVIDER}:{down_since}",
            )
        logger.error("model_provider_down provider=%s cause=%s label=%s", PROVIDER, error.cause, error.label)
        return True
    except Exception:  # noqa: BLE001 -- logged; see the docstring
        logger.exception("model_provider_failure_not_recorded provider=%s", PROVIDER)
        return False


def record_success() -> bool:
    """A model call answered. Returns True if this marked the provider up
    again (the recovery notice was raised and the backlog released). Never
    raises: the document's answer is what matters here."""
    try:
        with dispatcher_session() as session:
            row = session.execute(
                text(
                    "SELECT recovered, down_since, down_cause, waiting, failed_at_limit, "
                    "floor(extract(epoch FROM now() - down_since) / 60)::integer AS down_minutes "
                    "FROM public.provider_record_success(:p)"
                ),
                {"p": PROVIDER},
            ).mappings().one()
            if not row["recovered"]:
                return False
            down_since = _iso(row["down_since"])
            founder_alerts.raise_alert(
                session,
                alert_type="model_api_recovered",
                severity="info",
                tenant_id=None,
                payload={
                    "provider": PROVIDER,
                    "cause": row["down_cause"],
                    "down_since": down_since,
                    "down_for_minutes": int(row["down_minutes"] or 0),
                    "documents_waited": int(row["waiting"]),
                    "documents_failed_at_limit": int(row["failed_at_limit"]),
                },
                dedupe_key=f"model_api_recovered:{PROVIDER}:{down_since}",
            )
        logger.info("model_provider_recovered provider=%s waited=%d", PROVIDER, int(row["waiting"]))
        return True
    except Exception:  # noqa: BLE001 -- logged; see the docstring
        logger.exception("model_provider_success_not_recorded provider=%s", PROVIDER)
        return False


def alert_routing_failure(error: ProviderError) -> None:
    """
    The routing call's model refused us for a reason on our side (founder,
    2026-10-01: "alert, don't hold"). One warning a UTC day; every later one
    that day adds to `documents_without_examples` on the open alert (0035's
    count_routing_model_failure), so the Console shows the running count and
    the email the count when it was raised. Never raises.
    """
    try:
        with dispatcher_session() as session:
            raised = founder_alerts.raise_alert(
                session,
                alert_type="routing_model_failure",
                severity="warning",
                tenant_id=None,
                payload=_routing_payload(error, session),
                dedupe_key="routing_model_failure",
                dedupe_per_utc_day=True,
            )
            if not raised:
                session.execute(text("SELECT public.count_routing_model_failure()"))
        logger.error("routing_model_failure label=%s", error.label)
    except Exception:  # noqa: BLE001 -- logged; see the docstring
        logger.exception("routing_model_failure_not_recorded label=%s", error.label)


def _routing_payload(error: ProviderError, session: Any) -> dict[str, Any]:
    first = session.execute(text("SELECT now()")).scalar_one()
    return {
        "provider": PROVIDER,
        "cause": error.label,
        "first_failure_at": _iso(first),
        "documents_without_examples": 1,
    }
