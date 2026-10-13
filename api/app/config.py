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

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and s.jwt_secret == "change-me-in-env":
        raise RuntimeError("JWT_SECRET must be set in production")
    return s
