"""
Central settings, read once from the environment. Nothing in this codebase
reads os.environ directly outside this module -- see CLAUDE.md Section 6
("never commit secrets") and Section 5 (".env.example documents every var").
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# This file lives at <repo_root>/packages/core/docflow_core/config.py. Resolved
# by path (not cwd) so Settings finds the one root .env regardless of which
# app/worker directory a process is launched from -- see DECISIONS.md.
_REPO_ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_REPO_ROOT_ENV_FILE, extra="ignore")

    docflow_env: str = "staging"
    app_base_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    database_url: str = ""
    # Supabase issues auth JWTs signed with this secret (Project Settings -> API -> JWT Secret).
    supabase_jwt_secret: str = ""

    anthropic_api_key: str = ""
    docflow_extraction_model: str = "claude-sonnet-5"
    docflow_routing_model: str = "claude-haiku-4-5-20251001"

    stripe_publishable_key: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    redis_url: str = "redis://localhost:6379/0"

    email_provider_api_key: str = ""
    email_from_address: str = "notifications@docflow.example"
    intake_email_domain: str = "mail.docflow.example"

    founder_alert_email: str = ""

    sentry_dsn: str = ""
    llm_observability_api_key: str = ""

    session_secret: str = ""

    worker_egress_allowlist: str = "api.anthropic.com,*.supabase.co"

    # Absolute path to the LibreOffice binary used for Tier 2 conversion of
    # legacy binary `.doc` files inside the parsing worker (CLAUDE.md
    # Section 7.11). Blank means "look in the usual install locations and on
    # PATH"; when it is not installed at all, `.doc` documents fail cleanly
    # with catalog code DOC-017 rather than degrading silently. A hosted
    # conversion service is never an option (Section 7.10). See SETUP.md.
    libreoffice_path: str = ""

    # Phase 1 stand-in for Supabase Storage (see DECISIONS.md): no Storage
    # bucket/client wiring exists yet anywhere in this codebase, and adding
    # real object storage is out of scope for this slice. Local filesystem
    # under the same `tenants/{tenant_id}/...` path convention Section 7.5
    # requires, so the storage-path/prefix enforcement logic doesn't change
    # when this is swapped for real Supabase Storage later. Both the API and
    # worker processes must be able to see this path (true in local dev;
    # a real deploy needs shared/object storage -- tracked as a TODO).
    storage_root: str = "storage"

    @property
    def is_staging(self) -> bool:
        return self.docflow_env == "staging"

    @property
    def is_production(self) -> bool:
        return self.docflow_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
