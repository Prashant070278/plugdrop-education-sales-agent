from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql+psycopg://plugdrop:plugdrop@localhost:5432/plugdrop"
    jwt_secret: str = Field(default="change-me-in-env", min_length=16)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60 * 8
    cookie_name: str = "pd_session"
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:3000"

    # Integration credentials: only their presence is ever reported, never their values.
    smtp_host: str | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    voice_ai_api_key: str | None = None
    whatsapp_api_token: str | None = None
    calendar_api_key: str | None = None

    # --- Stage 2: research agent ---
    research_provider: str = "mock"  # name of a registered research source provider
    research_reasoner: str = "auto"  # "claude", "mock" or "auto" (claude when ANTHROPIC_API_KEY is set)
    research_worker: str = "embedded"  # "embedded" (thread inside the API), "external" (python -m app.research.worker) or "off"
    research_max_attempts: int = 3
    research_retry_base_seconds: int = 30
    research_provider_rate_per_minute: int = 30
    research_daily_budget_usd: float = 5.0
    research_max_items_per_job: int = 100
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_timeout_seconds: float = 60.0
    # USD per million tokens; set these to your contracted prices so cost tracking is accurate.
    anthropic_input_usd_per_mtok: float = 3.0
    anthropic_output_usd_per_mtok: float = 15.0

    # --- Stage 3: outreach agent ---
    outreach_writer: str = "auto"  # "claude", "rules" or "auto" (claude when ANTHROPIC_API_KEY is set)
    # Hard switch for real mailboxes (Gmail / Microsoft). While false, real providers only ever create
    # drafts, whatever the admin policy says. The mock provider is unaffected.
    outreach_external_sending_allowed: bool = False
    outreach_mock_provider_enabled: bool = True
    outreach_worker: str = "embedded"  # "embedded" (thread in the API), "external" or "off"
    token_encryption_key: str | None = None  # Fernet key; required before any OAuth mailbox can be connected
    oauth_redirect_base_url: str = "http://localhost:3000"
    google_client_id: str | None = None
    google_client_secret: str | None = None
    microsoft_client_id: str | None = None
    microsoft_client_secret: str | None = None
    microsoft_tenant: str = "organizations"

    # --- Stage 4: WhatsApp, calendar, engagement ---
    whatsapp_provider: str = "mock"  # "mock" (nothing leaves the system) or "meta" (WhatsApp Business Platform Cloud API)
    # Hard switch, like email: while false the real WhatsApp provider never sends, whatever the admin policy says.
    whatsapp_external_sending_allowed: bool = False
    whatsapp_phone_number_id: str | None = None
    whatsapp_business_account_id: str | None = None
    whatsapp_access_token: str | None = None  # system-user token from Meta Business Manager
    whatsapp_app_secret: str | None = None  # verifies webhook signatures (X-Hub-Signature-256)
    whatsapp_verify_token: str | None = None  # answers Meta's webhook verification challenge
    whatsapp_graph_version: str = "v21.0"
    whatsapp_graph_base_url: str = "https://graph.facebook.com"
    mock_webhook_secret: str = "mock-webhook-secret"  # signs mock-provider webhooks in tests and demos
    calendar_mock_enabled: bool = True
    engagement_worker: str = "embedded"  # "embedded" (thread in the API), "external" or "off"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and s.jwt_secret == "change-me-in-env":
        raise RuntimeError("JWT_SECRET must be set in production")
    return s
