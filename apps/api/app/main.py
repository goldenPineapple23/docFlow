from docflow_core.config import get_settings
from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.errors import catalog_detail
from app.routers import (
    activity,
    admin,
    auth,
    documents,
    email_intake,
    exports,
    held,
    home,
    review,
    stripe_webhooks,
    team,
)

app = FastAPI(title="DocFlow API")

# The browser app and the API are separate origins -- `localhost:3000` and
# `localhost:8000` in development, and separate hosts once deployed -- so the
# browser will not call the API at all without this. It had been missing
# since Phase 0: every request from the review screen failed preflight, which
# looked to the user like the page simply never loaded (DECISIONS.md D-089).
#
# An explicit allowlist, never `*`:
#   * `allow_credentials` stays False because DocFlow authenticates with a
#     bearer token in a header, not with a cookie. Nothing is sent
#     automatically by the browser, so a hostile page cannot ride an existing
#     session even if it reached this API.
#   * only the headers and methods the app actually uses are permitted, so a
#     future route cannot quietly become cross-origin reachable in a way
#     nobody chose.
_settings = get_settings()
_allowed_origins = [
    origin.strip().rstrip("/")
    for origin in _settings.cors_allowed_origins.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    # Must list every method a route uses -- tests/test_cors.py fails the
    # build otherwise. PUT was missing and every catalog-import fix silently
    # failed preflight in the browser (DECISIONS.md D-109).
    allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)


@app.exception_handler(RequestValidationError)
async def _malformed_document_id(request: Request, exc: RequestValidationError) -> JSONResponse:
    """An order address with something that is not an order id in it -- a
    mistyped link, or `/review/team` -- is "not found", in the same words as
    an order that does not exist or belongs to another account (REV-006). It
    used to answer 422, which no screen could explain, and the order screen
    said "We couldn't reach DocFlow" (found in the 5.8d walkthrough).

    Only a bad `document_id` in the path is treated this way; every other
    validation failure keeps FastAPI's own answer. On the Console's acting-as
    mount the admin gate runs first, so a non-admin still gets a bare 404.
    """
    errors = exc.errors()
    if errors and all(tuple(e.get("loc", ())) == ("path", "document_id") for e in errors):
        return JSONResponse(status_code=404, content={"detail": catalog_detail("REV-006")})
    return await request_validation_exception_handler(request, exc)


app.include_router(auth.router)
app.include_router(activity.router)
app.include_router(admin.router)
app.include_router(documents.router)
app.include_router(held.router)
app.include_router(home.router)
app.include_router(email_intake.router)
app.include_router(review.router)
app.include_router(exports.router)
app.include_router(stripe_webhooks.router)
app.include_router(team.router)

# The same review and export routes, a second time, for the founder acting in
# one tenant from the Console (Section 7.15.1; D-111). Same code, different
# actor: the gate 404s anyone who is not a platform admin, audits the
# request, and hands `current_actor` the founder-as-support Actor. There is
# no second review implementation to drift (Section 10).
for _router in (review.router, exports.router):
    app.include_router(
        _router,
        prefix="/admin/tenants/{acting_tenant_id}/act",
        dependencies=[Depends(admin.acting_as_gate)],
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
