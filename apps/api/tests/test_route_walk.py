"""
Every API route, classified (founder, 2026-11-12; D-237). Once the API is
public, a route added without a sign-in check would answer anyone. So every
route is listed here as either PUBLIC, with what protects it instead, or
SIGNED_IN; a route on neither list fails the build, and so does a listed one
that no longer exists.

SIGNED_IN routes are called with no Authorization header and must answer 401
(a tenant route, AUTH-005) or 404 (the Console and its acting-as mount,
Section 7.15.1). Nothing they do before refusing touches the database, so
this runs anywhere. The PUBLIC routes' own credentials are tested where they
are built; here only the ones that need no outside state are called.
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app

# Answer without a Supabase session. Each says what protects it instead.
PUBLIC: dict[tuple[str, str], str] = {
    ("GET", "/healthz"): "liveness: ages and counts only, no ids",
    ("POST", "/intake/email/{token}"): "Postmark's HTTP Basic credentials; refused when unset (H8)",
    ("POST", "/webhooks/stripe"): "Stripe's signature, idempotent on event id",
    ("GET", "/review/documents/{document_id}/original/content"): "a short-lived signed token (original)",
    ("GET", "/review/documents/{document_id}/pages/{index}"): "a short-lived signed token, purpose page",
    ("GET", "/review/exports/{export_id}/download"): "a short-lived signed token, purpose export",
}

# Answer 401 or 404 without a Supabase session.
SIGNED_IN: set[tuple[str, str]] = {
    ("GET", "/activity"),
    ("GET", "/admin/alerts"),
    ("POST", "/admin/alerts/{alert_id}/acknowledge"),
    ("GET", "/admin/dashboard"),
    ("GET", "/admin/intakes"),
    ("POST", "/admin/intakes"),
    ("GET", "/admin/intakes/{intake_id}"),
    ("POST", "/admin/intakes/{intake_id}/files"),
    ("GET", "/admin/lifecycle/ready-to-delete"),
    ("GET", "/admin/lifecycle/wind-down"),
    ("GET", "/admin/outbox"),
    ("POST", "/admin/rollup/recompute"),
    ("GET", "/admin/setup-fee-presets"),
    ("GET", "/admin/tenants"),
    ("POST", "/admin/tenants/new"),
    ("GET", "/admin/tenants/{acting_tenant_id}/act/review/documents"),
    ("GET", "/admin/tenants/{acting_tenant_id}/act/review/documents/{document_id}"),
    ("PATCH", "/admin/tenants/{acting_tenant_id}/act/review/documents/{document_id}"),
    ("POST", "/admin/tenants/{acting_tenant_id}/act/review/documents/{document_id}/approve"),
    ("GET", "/admin/tenants/{acting_tenant_id}/act/review/documents/{document_id}/exports"),
    ("POST", "/admin/tenants/{acting_tenant_id}/act/review/documents/{document_id}/exports"),
    ("POST", "/admin/tenants/{acting_tenant_id}/act/review/documents/{document_id}/mapping"),
    ("GET", "/admin/tenants/{acting_tenant_id}/act/review/documents/{document_id}/original"),
    ("GET", "/admin/tenants/{acting_tenant_id}/act/review/documents/{document_id}/original/content"),
    ("GET", "/admin/tenants/{acting_tenant_id}/act/review/documents/{document_id}/pages/{index}"),
    ("POST", "/admin/tenants/{acting_tenant_id}/act/review/documents/{document_id}/reject"),
    ("POST", "/admin/tenants/{acting_tenant_id}/act/review/documents/{document_id}/reopen"),
    ("GET", "/admin/tenants/{acting_tenant_id}/act/review/documents/{document_id}/text"),
    ("GET", "/admin/tenants/{acting_tenant_id}/act/review/documents/{document_id}/warnings/open"),
    ("GET", "/admin/tenants/{acting_tenant_id}/act/review/exports/{export_id}"),
    ("GET", "/admin/tenants/{acting_tenant_id}/act/review/exports/{export_id}/download"),
    ("GET", "/admin/tenants/{acting_tenant_id}/act/review/items"),
    ("GET", "/admin/tenants/{tenant_id}"),
    ("GET", "/admin/tenants/{tenant_id}/audit"),
    ("GET", "/admin/tenants/{tenant_id}/buyer-merges"),
    ("POST", "/admin/tenants/{tenant_id}/buyer-merges/{candidate_id}/dismiss"),
    ("POST", "/admin/tenants/{tenant_id}/buyer-merges/{candidate_id}/merge"),
    ("POST", "/admin/tenants/{tenant_id}/cancel"),
    ("GET", "/admin/tenants/{tenant_id}/cancel/preview"),
    ("POST", "/admin/tenants/{tenant_id}/card-request"),
    ("PUT", "/admin/tenants/{tenant_id}/deal-terms"),
    ("POST", "/admin/tenants/{tenant_id}/delete"),
    ("GET", "/admin/tenants/{tenant_id}/example-prompting"),
    ("PUT", "/admin/tenants/{tenant_id}/example-prompting"),
    ("GET", "/admin/tenants/{tenant_id}/field-schema"),
    ("PUT", "/admin/tenants/{tenant_id}/field-schema"),
    ("GET", "/admin/tenants/{tenant_id}/go-live"),
    ("POST", "/admin/tenants/{tenant_id}/go-live"),
    ("GET", "/admin/tenants/{tenant_id}/imports"),
    ("POST", "/admin/tenants/{tenant_id}/imports"),
    ("POST", "/admin/tenants/{tenant_id}/imports/from-intake"),
    ("GET", "/admin/tenants/{tenant_id}/imports/{import_id}"),
    ("POST", "/admin/tenants/{tenant_id}/imports/{import_id}/commit"),
    ("POST", "/admin/tenants/{tenant_id}/imports/{import_id}/discard"),
    ("PUT", "/admin/tenants/{tenant_id}/imports/{import_id}/mapping"),
    ("PUT", "/admin/tenants/{tenant_id}/imports/{import_id}/rows/{row_number}"),
    ("POST", "/admin/tenants/{tenant_id}/intake-address/rotate"),
    ("GET", "/admin/tenants/{tenant_id}/intake-files"),
    ("POST", "/admin/tenants/{tenant_id}/invite"),
    ("GET", "/admin/tenants/{tenant_id}/lifecycle"),
    ("GET", "/admin/tenants/{tenant_id}/metrics"),
    ("GET", "/admin/tenants/{tenant_id}/overview"),
    ("GET", "/admin/tenants/{tenant_id}/quarantine"),
    ("POST", "/admin/tenants/{tenant_id}/quarantine/clear"),
    ("POST", "/admin/tenants/{tenant_id}/quarantine/release"),
    ("POST", "/admin/tenants/{tenant_id}/reactivate"),
    ("GET", "/admin/tenants/{tenant_id}/rules"),
    ("POST", "/admin/tenants/{tenant_id}/rules/{rule_id}/delete"),
    ("POST", "/admin/tenants/{tenant_id}/rules/{rule_id}/disable"),
    ("POST", "/admin/tenants/{tenant_id}/rules/{rule_id}/enable"),
    ("PUT", "/admin/tenants/{tenant_id}/sender-settings"),
    ("GET", "/admin/tenants/{tenant_id}/test-batch"),
    ("POST", "/admin/tenants/{tenant_id}/test-batch"),
    ("POST", "/admin/tenants/{tenant_id}/test-batch/complete"),
    ("POST", "/admin/tenants/{tenant_id}/test-batch/run"),
    ("POST", "/admin/tenants/{tenant_id}/tier"),
    ("GET", "/admin/tiers"),
    ("GET", "/allowance"),
    ("GET", "/auth/me"),
    ("GET", "/billing"),
    ("POST", "/billing/card-page"),
    ("POST", "/billing/card-update-page"),
    ("POST", "/documents/upload"),
    ("GET", "/held"),
    ("POST", "/held/release"),
    ("GET", "/home"),
    ("GET", "/ignored-mail"),
    ("GET", "/review/documents"),
    ("GET", "/review/documents/{document_id}"),
    ("PATCH", "/review/documents/{document_id}"),
    ("POST", "/review/documents/{document_id}/approve"),
    ("GET", "/review/documents/{document_id}/exports"),
    ("POST", "/review/documents/{document_id}/exports"),
    ("POST", "/review/documents/{document_id}/mapping"),
    ("GET", "/review/documents/{document_id}/original"),
    ("POST", "/review/documents/{document_id}/reject"),
    ("POST", "/review/documents/{document_id}/reopen"),
    ("GET", "/review/documents/{document_id}/text"),
    ("GET", "/review/documents/{document_id}/warnings/open"),
    ("GET", "/review/exports/{export_id}"),
    ("GET", "/review/items"),
    ("GET", "/team"),
    ("POST", "/team/invite"),
    ("POST", "/team/{user_id}/remove"),
    ("POST", "/team/{user_id}/resend"),
}

_INT_PARAMS = {"index", "row_number"}
_ANY_ID = "00000000-0000-0000-0000-000000000001"


def _walk() -> set[tuple[str, str]]:
    # Included routers: `app.routes` alone hides them in this FastAPI (the
    # same walk as test_console_mfa.py).
    found: set[tuple[str, str]] = set()
    for route in app.router.routes:
        if hasattr(route, "original_router"):
            prefix = getattr(route.include_context, "prefix", "") or ""
            for r in route.original_router.routes:
                if isinstance(r, APIRoute):
                    found |= {(m, prefix + r.path) for m in r.methods}
        elif isinstance(route, APIRoute):
            found |= {(m, route.path) for m in route.methods}
    return found


def _concrete(path: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: "1" if m.group(1) in _INT_PARAMS else _ANY_ID, path)


def test_every_route_is_classified_and_every_classified_route_exists():
    routes = _walk()
    assert len(routes) > 100, "the route walk found too few routes to be trusted"
    assert not PUBLIC.keys() & SIGNED_IN, "a route is on both lists"
    unclassified = routes - PUBLIC.keys() - SIGNED_IN
    assert not unclassified, f"new routes, not yet classified here: {sorted(unclassified)}"
    gone = (PUBLIC.keys() | SIGNED_IN) - routes
    assert not gone, f"listed routes that no longer exist: {sorted(gone)}"


@pytest.mark.parametrize(("method", "path"), sorted(SIGNED_IN))
def test_a_signed_in_route_refuses_a_request_with_no_session(method, path):
    response = TestClient(app).request(method, _concrete(path))
    expected = 404 if path.startswith("/admin") else 401
    assert response.status_code == expected, response.text[:300]


def test_healthz_answers_without_a_session():
    assert TestClient(app).get("/healthz").status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/review/documents/{document_id}/original/content",
        "/review/documents/{document_id}/pages/{index}",
        "/review/exports/{export_id}/download",
    ],
)
def test_a_token_route_without_its_token_opens_nothing(path):
    response = TestClient(app).get(_concrete(path))
    assert response.status_code >= 400
    assert response.headers.get("content-type", "").startswith("application/json")
