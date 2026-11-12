"""
Central settings, read once from the environment. Nothing in this codebase
reads os.environ directly outside this module -- see CLAUDE.md Section 6
("never commit secrets") and Section 5 (".env.example documents every var").
"""

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# This file lives at <repo_root>/packages/core/docflow_core/config.py. Resolved
# by path (not cwd) so Settings finds the one root .env regardless of which
# app/worker directory a process is launched from -- see DECISIONS.md.
_REPO_ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

# The pinned models (CLAUDE.md Section 3; D-239). This is the only place in
# the product's code a model ID is written: a CI guard fails on one anywhere
# else (packages/core/tests/test_model_config.py). Verify an ID against the
# current Anthropic models documentation before changing it, and re-run the
# golden set (Section 7.1).
EXTRACTION_MODEL_DEFAULT = "claude-sonnet-5"  # since 2026-09-16
ROUTING_MODEL_DEFAULT = "claude-haiku-4-5"  # since 2026-09-25 (D-141)


@dataclass(frozen=True)
class ModelPrice:
    """A model's price in US dollars per million tokens, and the roles it may
    fill: "extraction" (the final extraction) and/or "routing" (the cheaper
    classification pass; Section 3 never lets it extract)."""

    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    roles: frozenset[str]


# Not environment variables (D-239): exactly the models DocFlow runs, at their
# prices. A configured model that isn't here, or is set for a role its entry
# doesn't allow, stops Settings from loading. Changing a model means adding
# it here with its price, in the same PR, and pricing lost calls by their own
# model first (D-239 item 7).
MODEL_PRICES: dict[str, ModelPrice] = {
    EXTRACTION_MODEL_DEFAULT: ModelPrice(Decimal("2.00"), Decimal("10.00"), frozenset({"extraction"})),
    ROUTING_MODEL_DEFAULT: ModelPrice(Decimal("1.00"), Decimal("5.00"), frozenset({"routing"})),
}

_MODEL_SETTINGS = (("DOCFLOW_EXTRACTION_MODEL", "docflow_extraction_model", "extraction"),
                   ("DOCFLOW_ROUTING_MODEL", "docflow_routing_model", "routing"))


class Settings(BaseSettings):
    # hide_input_in_errors: a ValidationError's text would otherwise quote the
    # settings it was given, secrets among them, into a traceback or a log
    # (D-239). The values stay in exc.errors() for code that needs one.
    model_config = SettingsConfigDict(env_file=_REPO_ROOT_ENV_FILE, extra="ignore", hide_input_in_errors=True)

    docflow_env: str = "staging"
    app_base_url: str = "http://localhost:3000"

    # Origins the browser app is served from, comma-separated. The API and
    # the web app are always separate origins, so without this the browser
    # refuses every call (see apps/api/app/main.py). Both spellings of "this
    # machine" are included by default because a developer may open either
    # and the failure is invisible when they get it wrong.
    #
    # Port 3101 is the live end-to-end suite's own server (D-166). It is
    # here rather than in a test-only override for the same reason as the
    # two spellings above: a CORS refusal shows up as a page that loads and
    # then does nothing, which costs an hour to diagnose every time. Every
    # origin in this default is a loopback address and can never be a
    # deployed one; staging and prod set CORS_ALLOWED_ORIGINS explicitly.
    cors_allowed_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3101,http://127.0.0.1:3101"
    )
    # Read by nothing, and no phase plans to: the API never needs its own
    # address, and the web app reads NEXT_PUBLIC_API_BASE_URL (D-239).
    api_base_url: str = "http://localhost:8000"

    supabase_url: str = ""
    # Read by scripts/seed_live_e2e.py and packages/core/tests/test_storage_bucket_live.py;
    # the apps don't, and the browser reads NEXT_PUBLIC_SUPABASE_ANON_KEY (D-239).
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    # Stage 3e (F-1): this process's own database login -- docflow_api on the
    # API, docflow_worker on the worker (docflow_core.db.use_own_login).
    database_url: str = ""
    # The other logins, each only where it is used (RUNBOOK 10):
    # ADMIN_DATABASE_URL (docflow_admin) and STRIPE_DATABASE_URL
    # (docflow_stripe) on the API only, and ADMIN_DATABASE_URL on the
    # founder's machine for the scripts. API_DATABASE_URL and
    # WORKER_DATABASE_URL name those two logins separately where one machine
    # holds both (the founder's .env, the test suites); deployed, each is
    # simply that app's DATABASE_URL.
    admin_database_url: str = ""
    stripe_database_url: str = ""
    api_database_url: str = ""
    worker_database_url: str = ""
    # Supabase issues auth JWTs signed with this secret (Project Settings -> API -> JWT Secret).
    supabase_jwt_secret: str = ""

    anthropic_api_key: str = ""
    # Leave both unset: the defaults are the pinned models, and any other
    # value must be in MODEL_PRICES for its role or Settings won't load (D-239).
    docflow_extraction_model: str = EXTRACTION_MODEL_DEFAULT
    docflow_routing_model: str = ROUTING_MODEL_DEFAULT

    # Read by nothing, and no phase plans to: the web app has no Stripe.js,
    # and billing runs server side with the secret key (D-239).
    stripe_publishable_key: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    # 127.0.0.1, not localhost: see .env.example (a ~2 s IPv6 fallback per
    # connection on Windows made the Console dashboard take ~10 s).
    redis_url: str = "redis://127.0.0.1:6379/0"

    # Signs the short-lived URLs the review screen's document viewer loads
    # from (Section 7.4 / 7.12). Separate from the Supabase secrets on
    # purpose: rotating it invalidates in-flight viewer URLs and nothing
    # else, so it can be rotated freely without signing anyone out.
    document_url_signing_secret: str = ""

    email_provider_api_key: str = ""
    email_from_address: str = "notifications@docflow.example"
    intake_email_domain: str = "mail.docflow.example"

    # Authenticates the *provider* of inbound mail, separately from the
    # address (review finding H8). The per-tenant token in the intake URL
    # identifies the tenant and nothing more: it is the local part of an
    # address the customer hands to its buyers, so it is public by design and
    # cannot also be a credential. Postmark puts HTTP Basic credentials on the
    # webhook URL; these are that pair, and they are not derivable from any
    # address.
    #
    # Blank means the inbound webhook refuses everything. That is deliberate
    # and it is why RUNBOOK section 2 states a cutover order: set these, point
    # Postmark at the URL carrying them, and only then is inbound mail live.
    # The alternative -- falling back to token-only when unset -- would make a
    # missing environment variable silently reopen the hole H8 describes.
    postmark_webhook_username: str = ""
    postmark_webhook_password: str = ""

    # Source addresses Postmark's inbound webhook really posts from
    # (comma-separated). **Log-only, never enforcing**, until the real
    # addresses are confirmed against Postmark's published list by the
    # RUNBOOK section 2 procedure (D-155): an allowlist that refuses before it
    # has been verified silently drops customers' purchase orders. The
    # credentials above are the control that actually holds; this is
    # corroboration.
    postmark_inbound_ip_allowlist: str = ""

    founder_alert_email: str = ""
    # The address customers write to -- to cancel, among other things (card
    # billing, founder 2026-09-29). Never hard-coded: every email that tells a
    # customer how to cancel reads it, and "Ask for a card" refuses while it is
    # blank (ONB-018) rather than send an email with no way to cancel.
    support_email: str = ""
    # D-151 / D-177: the Console requires an aal2 (TOTP-verified) session, and
    # destructive actions a challenge under MFA_STEP_UP_MAX_AGE_SECONDS old --
    # but only once this is true. Off by default so the founder can enrol
    # before it can lock anyone out; while it is off the API logs a warning at
    # startup, the Console shows a banner and a founder alert is raised.
    # RUNBOOK: docflow-prod is never deployed with this off after enrolment.
    console_mfa_enforced: bool = False
    # FastAPI's /docs, /redoc and /openapi.json (D-235 item 2; D-237). Off
    # unless set true, for local development only. On Fly (FLY_APP_NAME set)
    # it is ignored: the routes stay off and the API says so at startup.
    api_docs_enabled: bool = False

    # Phase 6 wires it: no Sentry SDK is installed yet (D-239).
    sentry_dsn: str = ""
    # Phase 6 wires it: no LLM observability tool is integrated yet (D-239).
    llm_observability_api_key: str = ""

    # Phase 6 wires it: nothing enforces it in code; parse's no-network rule
    # is its network namespace (D-150), not this list (D-239).
    worker_egress_allowlist: str = "api.anthropic.com,*.supabase.co"

    # Absolute path to the LibreOffice binary used for Tier 2 conversion of
    # legacy binary `.doc` files inside the parsing worker (CLAUDE.md
    # Section 7.11). Blank means "look in the usual install locations and on
    # PATH"; when it is not installed at all, `.doc` documents fail cleanly
    # with catalog code DOC-017 rather than degrading silently. A hosted
    # conversion service is never an option (Section 7.10). See SETUP.md.
    libreoffice_path: str = ""
    # Stage 3c: the parse service, the only place a hostile file is opened.
    # Dev: `python -m parse_service.server` in apps/parse (PARSE_ISOLATION=off).
    # Fly: http://docflow-parse-<env>.flycast (private, over Flycast).
    parse_service_url: str = "http://127.0.0.1:8100"
    parse_service_token: str = ""
    # Stage 3d (founder, Q2): how many documents may be in flight (on the
    # queue or being read) across all tenants -- the worker's document slots.
    # The worker's Celery concurrency is set from this same value, so the two
    # can't drift (one worker machine; RUNBOOK 9.2 when adding workers).
    dispatch_in_flight_target: int = 1
    # Stage 3e (founder, Q1): the worker's external heartbeat, a Healthchecks.io
    # ping URL (https://hc-ping.com/<uuid>), one per environment. A secret: it
    # is never logged. Blank (locally, in CI) means no ping is ever sent.
    heartbeat_url: str = ""
    # Set by Fly on every machine it runs (never in .env). The API uses it to
    # know it is deployed: on Fly it refuses to start holding the parse token
    # (apps/api/app/main.py, founder Q12), as the parse service refuses to
    # start without isolation.
    fly_app_name: str = ""

    # Supabase Storage through its S3-compatible endpoint (Stage 3b, Q1): an
    # access key that reaches Storage and nothing else, never the service
    # role key. The key is project-wide (every bucket, bypasses RLS), so
    # tenant isolation for files is docflow_core.storage's own prefix checks.
    # Endpoint: https://<project_ref>.storage.supabase.co/storage/v1/s3;
    # region: the project's region. See .env.example.
    storage_s3_endpoint: str = ""
    storage_s3_region: str = ""
    storage_s3_access_key_id: str = ""
    storage_s3_secret_access_key: str = ""

    # The pre-3b local folder. Product code no longer reads or writes it; only
    # scripts/copy_storage_to_bucket.py reads it, to copy staging's files
    # into the bucket (Stage 3b item 5). Kept until the founder drops it.
    storage_root: str = "storage"

    @model_validator(mode="after")
    def _models_are_priced_for_their_role(self) -> "Settings":
        """The model check runs whenever Settings loads (D-239), so the API,
        the worker, the scripts and the tests all refuse the same way."""
        for env_name, field, role in _MODEL_SETTINGS:
            model = getattr(self, field)
            entry = MODEL_PRICES.get(model)
            if entry is None:
                raise ValueError(
                    f"{env_name}={model!r} is not in MODEL_PRICES ({', '.join(sorted(MODEL_PRICES))}); "
                    "leave it unset, or add the model and its price in config.py in the same PR "
                    "and re-run the golden set (D-239)"
                )
            if role not in entry.roles:
                raise ValueError(
                    f"{env_name}={model!r} may not fill the {role} role "
                    f"(MODEL_PRICES allows it only for {', '.join(sorted(entry.roles))}; D-239)"
                )
        return self

    @property
    def is_staging(self) -> bool:
        return self.docflow_env == "staging"

    @property
    def is_production(self) -> bool:
        return self.docflow_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def settings_refusal() -> str | None:
    """Why Settings won't load, as one line with no setting's value in it
    except a model ID, or None. For the API's and the worker's start, which
    print it instead of a traceback (D-239)."""
    try:
        get_settings()
    except ValidationError as exc:
        reasons = []
        for error in exc.errors():
            message = str(error.get("msg", "")).removeprefix("Value error, ")
            where = ".".join(str(part) for part in error.get("loc", ()))
            reasons.append(f"{where.upper()}: {message}" if where else message)
        return "; ".join(reasons)
    return None


def model_ids_line(settings: Settings | None = None) -> str:
    """The start line the API and the worker print (D-239): which models run."""
    settings = settings or get_settings()
    return (
        f"model_ids: extraction={settings.docflow_extraction_model} "
        f"routing={settings.docflow_routing_model}"
    )
