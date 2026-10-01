"""
Which model-provider errors make a document wait and which fail it (Stage 3d;
BUILD-STATUS "3d -- APPROVED WITH CHANGES", change A).

Classified by the HTTP status the provider answered with, never by the SDK's
exception class name, so an SDK upgrade can't silently move an error between
the groups. Checked against Anthropic's error reference and rate-limits page
(read 2026-10-01) and the installed SDK:

* **Wait, the provider's side:** 500-599 (504 is its `timeout_error`), 529
  overloaded, 429 rate limited (at least its `retry-after`), and the network
  -- a connect, read or silent-stream timeout, or a connection dropped in the
  middle of the answer.
* **Wait, our own configuration** (founder, 2026-10-01): 401 (the key), 402
  `billing_error` (no credit, a payment problem), 403 (the account's access),
  404 (the model retired or renamed), a 400 that is our own spend limit
  ("You have reached your specified ... API usage limits"), and the 429 of the
  tier's monthly spend cap (`error.details.error_code` is
  `enforced_spend_limit_reached`, and it has no `retry-after`). A mistake on
  our side must never fail a customer's order.
* **Fail at once:** every other 4xx -- a request the document itself made
  impossible (a 400, a 413 too large).

An `error` event in the middle of a streamed answer arrives after HTTP 200,
and the SDK raises it with `status_code` 200 (anthropic/_streaming.py). It is
classified by its error `type`, mapped to the status the reference documents
for that type; an unknown type there counts as the provider's side, because
the request itself had already been accepted.

Nothing here keeps response text: `label` is a status code or fixed name,
safe for logs and the `last_wait_error` column (Section 7.10).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import anthropic
import httpx2

Group = Literal["wait", "fail"]

# The causes a provider wait can have. `our_configuration` marks the provider
# down at once; the others need PROVIDER_DOWN_FAILURES within the window.
CAUSE_5XX = "5xx"
CAUSE_OVERLOADED = "overloaded"
CAUSE_RATE_LIMITED = "rate_limited"
CAUSE_NETWORK = "network"
CAUSE_OUR_CONFIGURATION = "our_configuration"

# The reference's status for each error type (for a mid-stream error event).
_STATUS_FOR_TYPE: dict[str, int] = {
    "invalid_request_error": 400,
    "authentication_error": 401,
    "billing_error": 402,
    "permission_error": 403,
    "not_found_error": 404,
    "request_too_large": 413,
    "rate_limit_error": 429,
    "api_error": 500,
    "timeout_error": 504,
    "overloaded_error": 529,
}

_OUR_CONFIGURATION_STATUSES = frozenset({401, 402, 403, 404})
# Our own organisation or workspace spend limit, which the API answers with a
# 400 (rate-limits page, "Setting your own spend limit").
_OWN_SPEND_LIMIT_PREFIX = "You have reached your specified"
_SPEND_CAP_ERROR_CODE = "enforced_spend_limit_reached"


@dataclass(frozen=True)
class ProviderError:
    group: Group
    # One of the CAUSE_* names, or None for a failure.
    cause: str | None
    # A status code or fixed name: http_529, http_400_own_spend_limit,
    # network:ReadTimeout. Never response text.
    label: str
    # Seconds from a 429's retry-after, when it had one.
    retry_after_seconds: int | None = None

    @property
    def is_wait(self) -> bool:
        return self.group == "wait"

    @property
    def is_our_configuration(self) -> bool:
        return self.cause == CAUSE_OUR_CONFIGURATION


def _error_object(body: Any) -> dict[str, Any]:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return error
    return {}


def _retry_after(exc: anthropic.APIStatusError) -> int | None:
    raw = exc.response.headers.get("retry-after") if exc.response is not None else None
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return max(0, int(seconds + 0.999))


def classify(exc: BaseException) -> ProviderError:
    """Wait or fail for one exception raised by a model call."""
    if isinstance(exc, anthropic.APIStatusError):
        return _classify_status(exc)
    # APITimeoutError is an APIConnectionError; a stream dropped or gone
    # silent mid-answer surfaces from the transport, not as an APIError.
    if isinstance(exc, (anthropic.APIConnectionError, httpx2.TransportError)):
        return ProviderError("wait", CAUSE_NETWORK, f"network:{type(exc).__name__}")
    # An SDK error that is neither a status nor a connection: the request
    # never got a usable answer for a reason we can't name. Failed, as before
    # 3d -- waiting on an unknown error could wait forever on a bug.
    return ProviderError("fail", None, f"error:{type(exc).__name__}")


def _classify_status(exc: anthropic.APIStatusError) -> ProviderError:
    error = _error_object(exc.body)
    status = exc.status_code
    mid_stream = status < 400
    if mid_stream:
        # An error event after HTTP 200: the type says what it was.
        status = _STATUS_FOR_TYPE.get(str(error.get("type") or ""), 500)
    label = f"http_{status}" + ("_mid_stream" if mid_stream else "")

    if status in _OUR_CONFIGURATION_STATUSES:
        return ProviderError("wait", CAUSE_OUR_CONFIGURATION, label)
    if status == 400 and str(error.get("message") or "").startswith(_OWN_SPEND_LIMIT_PREFIX):
        return ProviderError("wait", CAUSE_OUR_CONFIGURATION, "http_400_own_spend_limit")
    if status == 429:
        details = error.get("details")
        if isinstance(details, dict) and details.get("error_code") == _SPEND_CAP_ERROR_CODE:
            return ProviderError("wait", CAUSE_OUR_CONFIGURATION, "http_429_spend_cap")
        return ProviderError("wait", CAUSE_RATE_LIMITED, label, retry_after_seconds=_retry_after(exc))
    if status == 529:
        return ProviderError("wait", CAUSE_OVERLOADED, label)
    if 500 <= status <= 599:
        return ProviderError("wait", CAUSE_5XX, label)
    return ProviderError("fail", None, label)
