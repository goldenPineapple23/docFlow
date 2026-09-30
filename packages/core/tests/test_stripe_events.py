"""
Static checks on migration 0029's Stripe event function (H11, D-173). The
behaviour is tested against the real database in
apps/api/tests/test_stripe_webhook_api.py; these hold the parts a reader can't
see from there:

- (The migration's grant and scripts/ci/create_app_role.py's are kept in step
  by test_ci_guards.py, with the other checks on what CI sets up.)
- The function hygiene D-173 requires: SECURITY DEFINER with a pinned
  search_path; EXECUTE revoked from PUBLIC and from Supabase's anon and
  authenticated roles.
- Nothing but the function writes `stripe_webhook_events`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
MIGRATION = REPO / "supabase" / "migrations" / "0029_stripe_event_function.sql"
APPLICATION_CODE = [
    REPO / "packages" / "core" / "docflow_core",
    REPO / "apps" / "api" / "app",
    REPO / "apps" / "worker" / "app",
    REPO / "scripts",
]

SIGNATURE = "(text, text, timestamptz, text, text, timestamptz, text)"


def _collapse(source: str) -> str:
    """Join adjacent string literals ('a' 'b', or "a" "b") and squeeze
    whitespace, so a statement split across lines reads as one."""
    joined = re.sub(r"['\"]\s*\n?\s*['\"]", "", source)
    return re.sub(r"\s+", " ", joined)


# Both Stripe event functions: 0029's subscription events, 0031's card events
# (card billing, D-181). Same hygiene for each.
FUNCTIONS = [
    pytest.param(MIGRATION, "record_stripe_subscription_event", SIGNATURE, id="0029-subscription-events"),
    pytest.param(
        MIGRATION.with_name("0031_card_billing.sql"),
        "record_stripe_card_event",
        "(text, text, text, text, bigint)",
        id="0031-card-events",
    ),
]


@pytest.mark.parametrize(("migration", "function", "signature"), FUNCTIONS)
def test_the_function_is_security_definer_with_a_pinned_search_path(migration, function, signature):
    sql = _collapse(migration.read_text(encoding="utf-8")).lower()
    body = sql[sql.index(f"create function {function}") :]
    header = body[: body.index("as $$")]
    assert "security definer" in header
    assert "set search_path = pg_catalog, public" in header


@pytest.mark.parametrize(("migration", "function", "signature"), FUNCTIONS)
def test_execute_is_revoked_from_public_anon_and_authenticated(migration, function, signature):
    sql = _collapse(migration.read_text(encoding="utf-8")).lower()
    assert f"revoke all on function {function}{signature} from public" in sql
    roles = re.search(r"foreach r in array array\[([^\]]*)\]", sql)
    assert roles is not None
    assert {"'anon'", "'authenticated'"} <= {r.strip() for r in roles.group(1).split(",")}


def test_nothing_but_the_function_writes_the_idempotency_table():
    writes = re.compile(r"\b(insert\s+into|update|delete\s+from)\s+stripe_webhook_events\b", re.IGNORECASE)
    offenders = [
        f"{path.relative_to(REPO)}:{number}"
        for root in APPLICATION_CODE
        for path in root.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if writes.search(line)
    ]
    assert offenders == [], offenders


def test_the_signature_check_and_the_event_time_check_share_one_tolerance():
    """D-176: the bound on Stripe's clock that the signature check enforces is
    the same one the event-time guard relies on. One constant, so they cannot
    drift apart."""
    import inspect

    from docflow_core import external_services
    from docflow_core.constants import STRIPE_CLOCK_TOLERANCE_SECONDS

    parameters = inspect.signature(external_services.verify_webhook_signature).parameters
    default = parameters["tolerance_seconds"].default
    assert default == STRIPE_CLOCK_TOLERANCE_SECONDS == 300
    source = (REPO / "packages" / "core" / "docflow_core" / "billing_webhooks.py").read_text(encoding="utf-8")
    assert "STRIPE_CLOCK_TOLERANCE_SECONDS" in source
