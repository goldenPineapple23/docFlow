"""
The browser only calls the API after a CORS preflight, and a method missing
from the allowlist fails that preflight before the request is sent. The
TestClient never preflights, so no route test can notice -- PUT was missing
and every catalog-import row fix failed in the browser only (DECISIONS.md
D-109). This proves the allowlist covers every route, and nothing more.
"""

import pytest
from fastapi.middleware.cors import CORSMiddleware

from app.main import app

ORIGIN = "http://localhost:3000"


def _allowed_methods() -> set[str]:
    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    return set(cors.kwargs["allow_methods"])


def _route_methods() -> set[str]:
    # From the OpenAPI schema: this FastAPI keeps included routers nested,
    # so `app.routes` only shows the docs routes. No route opts out of the
    # schema (include_in_schema=False); the count guard below would catch
    # the scan going blind again.
    paths = app.openapi()["paths"]
    assert len(paths) > 20, "route scan found too few routes to be trusted"
    return {method.upper() for operations in paths.values() for method in operations}


def test_every_method_a_route_uses_is_allowed_cross_origin():
    missing = _route_methods() - _allowed_methods()
    assert not missing, f"CORS allow_methods is missing {sorted(missing)}"


def test_no_method_is_allowed_that_no_route_uses():
    extra = _allowed_methods() - _route_methods() - {"OPTIONS"}
    assert not extra, f"CORS allow_methods permits unused {sorted(extra)}"


def test_a_put_preflight_from_the_web_app_passes(client):
    if ORIGIN not in next(m for m in app.user_middleware if m.cls is CORSMiddleware).kwargs["allow_origins"]:
        pytest.skip(f"{ORIGIN} is not a configured web origin here; the two tests above still hold")
    response = client.options(
        "/admin/tenants/00000000-0000-0000-0000-000000000000/imports/x/rows/2",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "PUT"},
    )
    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]
