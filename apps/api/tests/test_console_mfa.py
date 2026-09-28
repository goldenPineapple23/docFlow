# ruff: noqa: F811 -- pytest fixtures imported from other test modules look like redefinitions.
"""
The Console's MFA (DECISIONS.md D-151, D-177), against the real database.

With CONSOLE_MFA_ENFORCED on:
- every /admin route needs an aal2 session; a platform admin without one gets
  AUTH-006, and anyone else still gets the 404 (7.15.1);
- the seven destructive actions -- hard delete, clear quarantine, cancel,
  intake-address rotation, buyer merge, go live, tier change -- also need the
  TOTP challenge on the token to be at most 300 s old, with 30 s allowed for
  GoTrue's clock either way (D-170): 329 s passes, 331 s does not, 29 s in the
  future passes, 31 s does not;
- the challenge's age is recorded in the action's admin_actions payload.

With it off (the default): nothing is refused, and the founder is told three
ways -- a startup warning, the /auth/me flag the Console banner reads, and one
founder alert per process.

Session tokens here carry `aal` and `amr` exactly as GoTrue issues them after
a real TOTP challenge, read from a real staging token (D-177):
  amr = [{"method": "totp", "timestamp": ...}, {"method": "password", ...}]

All data is fictional (CLAUDE.md Section 0 rule 4).
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4

import jwt
import pytest
from docflow_core.config import get_settings
from docflow_core.constants import MFA_CLOCK_TOLERANCE_SECONDS, MFA_STEP_UP_MAX_AGE_SECONDS
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import deps
from app.main import app, console_mfa_startup_check
from app.routers import admin
from tests.test_console_api import JWT_SECRET, _Console, _environment, _scalar, stripe  # noqa: F401

LIMIT = MFA_STEP_UP_MAX_AGE_SECONDS + MFA_CLOCK_TOLERANCE_SECONDS  # 330


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setenv("CONSOLE_MFA_ENFORCED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def not_enforced(monkeypatch):
    monkeypatch.setenv("CONSOLE_MFA_ENFORCED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _headers(auth_user_id: str, *, aal: str = "aal1", totp_age: int | None = None) -> dict:
    now = int(time.time())
    amr = [{"method": "password", "timestamp": now - 600}]
    if totp_age is not None:
        amr.insert(0, {"method": "totp", "timestamp": now - totp_age})
    token = jwt.encode(
        {"sub": auth_user_id, "email": "c@example.com", "aud": "authenticated", "aal": aal, "amr": amr},
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _code(response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


# ── The seven destructive routes (D-151 named four; the founder added three) ──

_X = "00000000-0000-4000-8000-00000000abcd"
DESTRUCTIVE = [
    ("/admin/tenants/{t}/delete", {"confirm_name": "x", "reason": "a reason for the test"}),
    ("/admin/tenants/{t}/quarantine/clear", {"document_ids": [], "confirm_name": "x"}),
    ("/admin/tenants/{t}/cancel", {"reason": "customer_requested"}),
    ("/admin/tenants/{t}/intake-address/rotate", None),
    ("/admin/tenants/{t}/buyer-merges/" + _X + "/merge", {"keep_buyer_id": _X}),
    ("/admin/tenants/{t}/go-live", None),
    ("/admin/tenants/{t}/tier", {"tier": "growth"}),
]
EXPECTED_STEP_UP_PATHS = {
    "/admin/tenants/{tenant_id}/delete",
    "/admin/tenants/{tenant_id}/quarantine/clear",
    "/admin/tenants/{tenant_id}/cancel",
    "/admin/tenants/{tenant_id}/intake-address/rotate",
    "/admin/tenants/{tenant_id}/buyer-merges/{candidate_id}/merge",
    "/admin/tenants/{tenant_id}/go-live",
    "/admin/tenants/{tenant_id}/tier",
}


def _depends_on(dependant, target) -> bool:
    return any(d.call is target or _depends_on(d, target) for d in dependant.dependencies)


def test_exactly_the_seven_destructive_routes_require_a_recent_code():
    """A new destructive route that forgets the step-up, or a harmless one that
    gains it, changes this set -- so the list is decided here, on purpose."""
    # Walk included routers: `app.routes` alone hides them in this FastAPI
    # (the same walk as test_console_shared_paths.py).
    routes: list[tuple[str, APIRoute]] = []
    for route in app.router.routes:
        if hasattr(route, "original_router"):
            prefix = getattr(route.include_context, "prefix", "") or ""
            routes += [(prefix + r.path, r) for r in route.original_router.routes if isinstance(r, APIRoute)]
        elif isinstance(route, APIRoute):
            routes.append((route.path, route))
    assert len(routes) > 100, "the route walk found too few routes to be trusted"
    stepped = {path for path, r in routes if _depends_on(r.dependant, deps.require_recent_mfa)}
    assert stepped == EXPECTED_STEP_UP_PATHS


# ── Enforcement on: the Console gate ──────────────────────────────────────────


def test_a_platform_admin_without_an_authenticator_code_gets_auth_006(client, enforced, _environment):
    with _Console() as console:
        response = client.get("/admin/tiers", headers=_headers(console.auth_user_id, aal="aal1"))
    assert response.status_code == 403
    assert _code(response) == "AUTH-006"


def test_anyone_else_still_gets_a_404_not_an_mfa_answer(client, enforced, _environment):
    """7.15.1: the Console must not be confirmable by a stranger -- the MFA
    answer is only ever given to a real platform admin."""
    assert client.get("/admin/tiers").status_code == 404
    stranger = client.get("/admin/tiers", headers=_headers(str(uuid4()), aal="aal2", totp_age=5))
    assert stranger.status_code == 404


def test_a_confirmed_session_opens_the_console(client, enforced, _environment):
    with _Console() as console:
        headers = _headers(console.auth_user_id, aal="aal2", totp_age=600)
        response = client.get("/admin/tiers", headers=headers)
    assert response.status_code == 200  # browsing needs aal2, not a recent code


# ── Enforcement on: step-up for destructive actions ───────────────────────────


@pytest.mark.parametrize("path,body", DESTRUCTIVE)
def test_every_destructive_action_refuses_a_stale_code_with_auth_007(
    client, enforced, _environment, path, body
):
    with _Console() as console:
        headers = _headers(console.auth_user_id, aal="aal2", totp_age=LIMIT + 1)
        response = client.post(path.format(t=uuid4()), headers=headers, json=body)
    assert response.status_code == 403, response.text
    assert _code(response) == "AUTH-007"


@pytest.mark.parametrize(
    "age,passes",
    [
        (LIMIT - 1, True),  # 329 s: inside the window plus GoTrue's allowance
        (LIMIT + 1, False),  # 331 s
        (-(MFA_CLOCK_TOLERANCE_SECONDS - 1), True),  # stamped 29 s in our future
        (-(MFA_CLOCK_TOLERANCE_SECONDS + 1), False),  # 31 s in our future: a clock fault
        (None, False),  # aal2 but no TOTP challenge on the token at all
    ],
)
def test_the_challenge_age_is_judged_with_gotrues_clock_allowance(
    client, enforced, _environment, age, passes
):
    """D-170: the challenge time is GoTrue's clock and the age is judged on
    ours, so the window carries a named allowance, pinned either side. A pass
    here reaches the route itself, which then 404s the made-up tenant."""
    with _Console() as console:
        headers = _headers(console.auth_user_id, aal="aal2", totp_age=age)
        response = client.post(f"/admin/tenants/{uuid4()}/intake-address/rotate", headers=headers)
    if passes:
        assert response.status_code == 404, response.text
    else:
        assert response.status_code == 403 and _code(response) == "AUTH-007", response.text


def test_a_destructive_action_with_a_fresh_code_runs_and_records_the_codes_age(
    client, stripe, monkeypatch, _environment
):
    with _Console() as console:
        # Setup through the Console's own (code-less) helper, with enforcement off.
        monkeypatch.setenv("CONSOLE_MFA_ENFORCED", "false")
        get_settings.cache_clear()
        tenant_id = console.create_tenant(client).json()["tenant_id"]

        monkeypatch.setenv("CONSOLE_MFA_ENFORCED", "true")
        get_settings.cache_clear()
        try:
            response = client.post(
                f"/admin/tenants/{tenant_id}/intake-address/rotate",
                headers=_headers(console.auth_user_id, aal="aal2", totp_age=10),
            )
        finally:
            get_settings.cache_clear()
        assert response.status_code == 200, response.text
        age = _scalar(
            "SELECT (payload->>'mfa_challenge_age_seconds')::int FROM admin_actions "
            "WHERE platform_admin_user_id = :u AND action = 'intake_address_rotate'",
            u=str(console.user_id),
        )
    assert 10 <= age <= 15


# ── Enforcement off (the default) ─────────────────────────────────────────────


def test_while_off_nothing_is_refused(client, not_enforced, _environment):
    with _Console() as console:
        headers = _headers(console.auth_user_id, aal="aal1")  # no code at all
        assert client.get("/admin/tiers", headers=headers).status_code == 200
        response = client.post(f"/admin/tenants/{uuid4()}/intake-address/rotate", headers=headers)
    assert response.status_code == 404  # reached the route; the tenant doesn't exist


def test_auth_me_tells_a_platform_admin_and_nobody_else(client, not_enforced, _environment):
    with _Console() as console:
        me = client.get("/auth/me", headers=_headers(console.auth_user_id, aal="aal1")).json()
    assert me["console_mfa"] == {"enforced": False, "aal": "aal1"}
    stranger = client.get("/auth/me", headers=_headers(str(uuid4()), aal="aal1")).json()
    assert stranger["console_mfa"] is None


def test_auth_me_reports_enforcement_on(client, enforced, _environment):
    with _Console() as console:
        me = client.get("/auth/me", headers=_headers(console.auth_user_id, aal="aal2", totp_age=5)).json()
    assert me["console_mfa"] == {"enforced": True, "aal": "aal2"}


def test_startup_warns_while_off_and_is_quiet_when_on(monkeypatch, caplog):
    monkeypatch.setenv("CONSOLE_MFA_ENFORCED", "false")
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING, logger="docflow.api"):
        assert console_mfa_startup_check() is False
    assert "console_mfa_enforcement_off" in caplog.text

    caplog.clear()
    monkeypatch.setenv("CONSOLE_MFA_ENFORCED", "true")
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING, logger="docflow.api"):
        assert console_mfa_startup_check() is True
    assert "console_mfa_enforcement_off" not in caplog.text
    get_settings.cache_clear()


def test_the_warning_runs_at_application_startup(monkeypatch, caplog):
    monkeypatch.setenv("CONSOLE_MFA_ENFORCED", "false")
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING, logger="docflow.api"):
        with TestClient(app):  # entering runs the lifespan
            pass
    get_settings.cache_clear()
    assert "console_mfa_enforcement_off" in caplog.text


@pytest.fixture
def notice_cleanup():
    """Remove the enforcement-off alerts (and their emails) this test raises,
    and nothing that existed before it."""
    def open_ids() -> set[str]:
        from docflow_core.db import platform_session
        from sqlalchemy import text

        with platform_session() as session:
            return {
                str(r[0])
                for r in session.execute(
                    text("SELECT id FROM founder_alerts WHERE type = 'console_mfa_enforcement_off'")
                )
            }

    before = open_ids()
    yield
    from docflow_core.db import platform_session
    from sqlalchemy import text

    with platform_session() as session:
        for alert_id in sorted(open_ids() - before):
            outbox_id = session.execute(
                text("DELETE FROM founder_alerts WHERE id = :id RETURNING email_outbox_id"), {"id": alert_id}
            ).scalar()
            if outbox_id is not None:
                session.execute(text("DELETE FROM email_outbox WHERE id = :id"), {"id": outbox_id})


@pytest.mark.console_mfa_notice
def test_while_off_the_first_console_request_raises_one_founder_alert(
    client, not_enforced, monkeypatch, notice_cleanup, _environment
):
    monkeypatch.setattr(admin, "_console_mfa_off_noticed", False)
    calls = []
    real = admin.admin_data_access.note_console_mfa_enforcement_off
    monkeypatch.setattr(
        admin.admin_data_access,
        "note_console_mfa_enforcement_off",
        lambda **kw: calls.append(kw) or real(**kw),
    )
    with _Console() as console:
        headers = _headers(console.auth_user_id)
        assert client.get("/admin/tiers", headers=headers).status_code == 200
        assert client.get("/admin/tiers", headers=headers).status_code == 200
        audit = _scalar(
            "SELECT count(*) FROM admin_actions WHERE platform_admin_user_id = :u "
            "AND action = 'console_mfa_enforcement_off_noticed'",
            u=str(console.user_id),
        )
    assert len(calls) == 1  # once per process, not once per request
    assert audit == 1
    open_alerts = _scalar(
        "SELECT count(*) FROM founder_alerts WHERE type = 'console_mfa_enforcement_off' "
        "AND acknowledged_at IS NULL AND tenant_id IS NULL AND severity = 'high'"
    )
    assert open_alerts == 1


@pytest.mark.console_mfa_notice
def test_with_enforcement_on_no_notice_is_raised(client, enforced, monkeypatch, _environment):
    monkeypatch.setattr(admin, "_console_mfa_off_noticed", False)
    calls = []
    monkeypatch.setattr(
        admin.admin_data_access, "note_console_mfa_enforcement_off", lambda **kw: calls.append(kw)
    )
    with _Console() as console:
        client.get("/admin/tiers", headers=_headers(console.auth_user_id, aal="aal2", totp_age=5))
    assert calls == []
