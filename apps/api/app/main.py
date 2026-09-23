from docflow_core.config import get_settings
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import (
    admin,
    auth,
    documents,
    email_intake,
    exports,
    held,
    home,
    review,
    stripe_webhooks,
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

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(documents.router)
app.include_router(held.router)
app.include_router(home.router)
app.include_router(email_intake.router)
app.include_router(review.router)
app.include_router(exports.router)
app.include_router(stripe_webhooks.router)

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
