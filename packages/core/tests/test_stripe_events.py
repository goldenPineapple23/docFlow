"""
Static checks on migration 0029's Stripe event function (H11, D-173). The
behaviour is tested against the real database in
apps/api/tests/test_stripe_webhook_api.py; these hold the parts a reader can't
see from there:

- CI creates `docflow_app` after the migrations run, so the migration's grant is
  skipped there and scripts/ci/create_app_role.py grants it again. The two must
  name the same signature, or CI tests a function the app can't call (or one
  staging can't).
- The function hygiene D-173 requires: SECURITY DEFINER with a pinned
  search_path; EXECUTE revoked from PUBLIC and from Supabase's anon and
  authenticated roles.
- Nothing but the function writes `stripe_webhook_events`.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MIGRATION = REPO / "supabase" / "migrations" / "0029_stripe_event_function.sql"
CI_ROLE = REPO / "scripts" / "ci" / "create_app_role.py"
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


def test_the_migration_and_ci_grant_the_same_function_signature_to_the_app_role():
    grant = re.compile(
        r"grant execute on function record_stripe_subscription_event\s*(\([^)]*\))\s*to docflow_app",
        re.IGNORECASE,
    )
    in_migration = grant.findall(_collapse(MIGRATION.read_text(encoding="utf-8")))
    in_ci = grant.findall(_collapse(CI_ROLE.read_text(encoding="utf-8")))
    assert in_migration == [SIGNATURE]
    assert in_ci == [SIGNATURE]


def test_the_function_is_security_definer_with_a_pinned_search_path():
    sql = _collapse(MIGRATION.read_text(encoding="utf-8")).lower()
    body = sql[sql.index("create function record_stripe_subscription_event") :]
    header = body[: body.index("as $$")]
    assert "security definer" in header
    assert "set search_path = pg_catalog, public" in header


def test_execute_is_revoked_from_public_anon_and_authenticated():
    sql = _collapse(MIGRATION.read_text(encoding="utf-8")).lower()
    assert f"revoke all on function record_stripe_subscription_event{SIGNATURE} from public" in sql
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
