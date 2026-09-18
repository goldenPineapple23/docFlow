from docflow_core.config import get_settings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import admin, auth, documents, email_intake, exports, review

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
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(documents.router)
app.include_router(email_intake.router)
app.include_router(review.router)
app.include_router(exports.router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
