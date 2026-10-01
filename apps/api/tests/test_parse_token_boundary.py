"""
The API never holds the parse service's token (founder, 2026-10-01).

Only the worker talks to the parse service (Stage 3c). The API is the
public-facing process, so a token in its settings would let anything that
reaches its memory or environment send files to the service. On Fly the token
is a secret on the worker's app and the parse service's app, never the API's.

In CI the API job's test step has no PARSE_SERVICE_TOKEN. The token reaches
only the harness that runs worker code (`as_the_worker` in tests/conftest.py),
under WORKER_HARNESS_PARSE_TOKEN. The first test also reads the root .env, so
a token put there for the worker would fail it: in dev the API and the worker
share that file.
"""

from __future__ import annotations

import os

import pytest
from docflow_core import parse_client
from docflow_core.config import Settings, get_settings

from tests.conftest import WORKER_HARNESS_PARSE_TOKEN, as_the_worker

_TABLE = b"sku,description\nTEST-1001,Acme Test Widget\n"


def test_the_api_settings_carry_no_parse_token():
    assert "PARSE_SERVICE_TOKEN" not in os.environ
    get_settings.cache_clear()
    assert get_settings().parse_service_token == ""
    assert Settings().parse_service_token == ""


@pytest.mark.skipif(
    not os.environ.get(WORKER_HARNESS_PARSE_TOKEN),
    reason="needs the real parse service with a token (CI's API job); the dev service has none",
)
def test_a_request_carrying_the_api_settings_is_refused():
    get_settings.cache_clear()
    with pytest.raises(parse_client.ParseUnavailable) as refused:
        parse_client.parse_table(_TABLE, "catalog.csv")
    assert refused.value.reason == "unauthorized"

    # Control: the same request with the worker's token gets in, so the
    # refusal above was the missing token, not a service that was down.
    with as_the_worker():
        answer = parse_client.parse_table(_TABLE, "catalog.csv")
    assert answer.outcome == "ok"
    assert answer.columns == ["sku", "description"]
