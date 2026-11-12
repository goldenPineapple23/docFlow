import logging
import sys
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from docflow_core import db, worker_starts
from docflow_core.config import Settings, get_settings
from docflow_core.dispatch import health as dispatcher_health
from docflow_core.signed_urls import SigningKeyMissing
from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.deps import ConsoleRefused
from app.errors import catalog_detail
from app.routers import (
    activity,
    admin,
    auth,
    billing,
    documents,
    email_intake,
    exports,
    held,
    home,
    review,
    stripe_webhooks,
    team,
)

logger = logging.getLogger("docflow.api")


def parse_token_startup_refusal() -> str | None:
    """
    Why the API must not start, or None (founder, 2026-10-01, Q12). Only the
    worker talks to the parse service, so the API never holds its token; on
    Fly (FLY_APP_NAME is set) a token here means a secret was put on the
    wrong app. A secrets list is a human control; this makes it a property
    of the code, as the parse service refusing to start without isolation
    does. Checked when this module is imported, before the app exists, so
    the server never opens its port.
    """
    settings = get_settings()
    if settings.fly_app_name and settings.parse_service_token:
        return (
            "PARSE_SERVICE_TOKEN is set on this API machine. The API never holds the parse "
            "service's token; only the worker talks to the parse service. Remove the secret "
            "from this Fly app (RUNBOOK 8.2)."
        )
    return None


# Stage 3e (F-1): the API's tenant sessions run as docflow_api.
db.use_own_login("api")


def login_startup_check() -> list[str]:
    """
    Each database URL the API holds must connect as its own login
    (docflow_api, and docflow_admin / docflow_stripe when set; 3e, A4). A
    wrong one stops the API before it serves a request: a pasted wrong URL
    would otherwise run tenant requests with another service's policies. A
    database that can't be reached is logged, not fatal: /healthz must still
    answer, and it reports the database itself. Returns the logins checked.
    """
    try:
        return db.verify_logins(("api", "admin", "stripe"))
    except db.WrongLoginError:
        raise
    except Exception as exc:  # noqa: BLE001 -- unreachable, not wrong
        logger.error("login_startup_check_unreachable error_type=%s", type(exc).__name__)
        return []


_LOOPBACK_HOSTS = frozenset({"localhost", "::1"})


def _origin_problem(origin: str) -> str | None:
    """Why one CORS origin can't be a deployed API's, or None."""
    if "*" in origin:
        return "is a wildcard"
    parts = urlsplit(origin)
    if parts.scheme != "https":
        return "is not https"
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        return "has a path"
    host = parts.hostname
    if not host:
        return "is not an origin"
    if host in _LOOPBACK_HOSTS or host.startswith("127."):
        return "is a loopback address"
    return None


def cors_startup_refusal(settings: Settings | None = None) -> str | None:
    """
    Why a deployed API must not start with its CORS allowlist, or None
    (founder, 2026-11-12; D-237). Off Fly nothing is checked: local
    development and the tests use the loopback default. On Fly the list must
    be set, and every entry an exact https origin that isn't loopback -- an
    unset value would quietly allow only localhost (D-223 item 7), and a `*`
    or a wrong value would let other pages call the API.
    """
    settings = settings or get_settings()
    if not settings.fly_app_name:
        return None
    origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
    if "cors_allowed_origins" not in settings.model_fields_set or not origins:
        return (
            "CORS_ALLOWED_ORIGINS is not set on this API machine, so it would allow only the "
            "loopback default. Set it to the web app's https origin (RUNBOOK 13)."
        )
    for origin in origins:
        problem = _origin_problem(origin)
        if problem is not None:
            return (
                f"CORS_ALLOWED_ORIGINS holds {origin!r}, which {problem}. Each entry must be an "
                "exact https origin with no path, such as https://app.example.com (RUNBOOK 13)."
            )
    return None


def api_docs_enabled() -> bool:
    """/docs, /redoc and /openapi.json: only when API_DOCS_ENABLED is true and
    never on Fly (D-235 item 2; founder, 2026-11-12; D-237)."""
    settings = get_settings()
    return settings.api_docs_enabled and not settings.fly_app_name


def api_docs_startup_check() -> bool:
    """One startup line on Fly saying the docs are off. Returns whether they're on."""
    settings = get_settings()
    if settings.fly_app_name:
        # A warning, not info: under uvicorn's log setup only a `docflow.api`
        # warning is sure to reach the machine's log (D-220 item 11).
        logger.warning(
            "api_docs_off: /docs, /redoc and /openapi.json are off on Fly; API_DOCS_ENABLED "
            "is ignored here (it is %s) -- D-237.",
            "set" if settings.api_docs_enabled else "unset",
        )
    return api_docs_enabled()


_refusal = parse_token_startup_refusal() or cors_startup_refusal()
if _refusal is not None:
    print(f"DocFlow API refused to start: {_refusal}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def console_mfa_startup_check() -> bool:
    """
    Warn at startup while the Console's MFA enforcement is off (D-177; the
    founder's condition for shipping CONSOLE_MFA_ENFORCED off by default).
    Returns whether it is on. docflow-prod is never deployed with it off after
    the founder has enrolled (RUNBOOK section 4).
    """
    if get_settings().console_mfa_enforced:
        return True
    logger.warning(
        "console_mfa_enforcement_off: the Console accepts sessions without an authenticator "
        "code (CONSOLE_MFA_ENFORCED=false). Turn it on once the founder has enrolled -- RUNBOOK 4."
    )
    return False


def document_signing_startup_check() -> bool:
    """
    Without this secret no original opens and no export downloads, and until
    now the first sign of it was a reviewer's error. Said at startup; never
    a refusal, because everything else works without it (D-223, D-228).
    """
    if get_settings().document_url_signing_secret:
        return True
    logger.warning(
        "document_signing_secret_unset: DOCUMENT_URL_SIGNING_SECRET is not set, so originals "
        "can't be opened and exports can't be downloaded -- RUNBOOK 9.7."
    )
    return False


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        login_startup_check()
    except db.WrongLoginError as exc:
        print(f"DocFlow API refused to start: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    console_mfa_startup_check()
    document_signing_startup_check()
    api_docs_startup_check()
    yield


# With openapi_url None FastAPI adds none of the four doc routes, the OAuth2
# redirect included; `app.openapi()` still builds the schema for the tests
# that walk the routes.
_docs_on = api_docs_enabled()
app = FastAPI(
    title="DocFlow API",
    lifespan=_lifespan,
    docs_url="/docs" if _docs_on else None,
    redoc_url="/redoc" if _docs_on else None,
    openapi_url="/openapi.json" if _docs_on else None,
)


class _CatchUnexpected(BaseHTTPMiddleware):
    """
    A failure nobody anticipated answers SYS-001 in the catalog's words
    (D-136), instead of a bare 500.

    It has to sit *inside* the CORS middleware (it is added before it, and
    Starlette wraps in reverse): Starlette's own last-resort handler runs
    outside CORS, so its 500 carried no CORS headers, the browser discarded it,
    and every screen reported "We couldn't reach DocFlow" for what was really
    DocFlow failing (found when the Console's delete crashed, D-133).

    What reaches the log is the exception type and the code locations only --
    never the exception's message, which for a database error includes the
    bound parameters, i.e. customer data (Section 7.10).
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001 -- answered and logged below
            frames = "".join(traceback.format_tb(exc.__traceback__))
            logger.error(
                "unhandled_error method=%s path=%s error=%s\n%s",
                request.method,
                request.url.path,
                type(exc).__name__,
                frames,
            )
            return JSONResponse(status_code=500, content={"detail": catalog_detail("SYS-001")})


app.add_middleware(_CatchUnexpected)

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


# Headers every API response carries unless its route set its own (D-237).
# The viewer's original, a page image and a download set their own CSP and
# keep it: the original's `frame-ancestors` names the web app, so no
# X-Frame-Options here -- `DENY` would fight that frame. HSTS is ignored by
# browsers over plain http, so local runs are unaffected.
API_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"strict-transport-security", b"max-age=63072000; includeSubDomains"),
)


class _SecurityHeaders:
    """Plain ASGI, outermost of DocFlow's middleware, so preflight answers and
    SYS-001's 500 carry the headers too."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend((n, v) for n, v in API_SECURITY_HEADERS if n not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(_SecurityHeaders)


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


# A Console request refused at the gate (AUTH-006, AUTH-007) is recorded
# before its 403 goes out (D-178); the handler lives in the admin router, the
# only module allowed to write the audit.
app.add_exception_handler(ConsoleRefused, admin.console_refused_handler)


@app.exception_handler(SigningKeyMissing)
async def _signing_key_missing(request: Request, _exc: SigningKeyMissing) -> JSONResponse:
    """Every route that signs or checks a file link ends up here (D-228)."""
    logger.error("document_signing_secret_unset method=%s path=%s", request.method, request.url.path)
    return JSONResponse(status_code=503, content={"detail": catalog_detail("SYS-002")})

app.include_router(auth.router)
app.include_router(activity.router)
app.include_router(admin.router)
app.include_router(documents.router)
app.include_router(billing.router)
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
    """
    The API's liveness answer, and (Stage 3d, gap 1) whether the dispatcher
    is alive: its heartbeat's age and whether that is past
    DISPATCHER_STALE_MIN, and how many times the worker started in the last
    hour (3e). No sign-in, so ages and counts only -- no ids.
    Always HTTP 200: a platform check pointed here must never restart a
    healthy API because the worker is down. The external uptime monitor
    (from the first worker deploy, RUNBOOK 9.4) matches the bytes
    `"stale":false` -- compact JSON, no space (test_healthz_dispatcher.py).
    """
    try:
        dispatcher = dispatcher_health()
    except Exception as exc:  # noqa: BLE001 -- the database is unreachable
        logger.error("healthz_dispatcher_unreadable error_type=%s", type(exc).__name__)
        dispatcher = {"heartbeat_age_seconds": None, "stale": True}
    # Stage 3e (part B): a worker restarting slowly keeps the heartbeat fresh,
    # so the starts in the last hour show it here. null when unreadable.
    try:
        starts: int | None = worker_starts.starts_last_hour()
    except Exception as exc:  # noqa: BLE001 -- the database is unreachable
        logger.error("healthz_worker_starts_unreadable error_type=%s", type(exc).__name__)
        starts = None
    return {"status": "ok", "dispatcher": dispatcher, "worker_starts_last_hour": starts}
