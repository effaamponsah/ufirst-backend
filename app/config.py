from __future__ import annotations

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "U-FirstSupport API"
    port: int = 8000
    debug: bool = False
    # When True, accepts "Bearer dev:<user_id>:<role>" tokens without Supabase.
    # NEVER enable in production.
    dev_mode: bool = False

    # Database
    database_url: PostgresDsn = Field(..., description="PostgreSQL DSN")

    # Redis
    redis_url: RedisDsn = Field(..., description="Redis DSN")

    # Supabase auth — at least one of these must be set in non-dev mode
    supabase_jwt_secret: str = ""        # HS256 — from Supabase dashboard → API → JWT Secret
    supabase_jwks_url: str = ""          # RS256 — preferred for production
    supabase_webhook_secret: str = ""    # For verifying Supabase webhook payloads

    # Field encryption (AES-256-GCM)
    encryption_key: str = Field(
        ...,
        description="32-byte hex string. Generate: openssl rand -hex 32",
    )

    # Open Banking
    openbanking_provider: str = "TRUELAYER"
    truelayer_client_id: str = ""
    truelayer_client_secret: str = ""
    truelayer_webhook_secret: str = ""
    truelayer_base_url: str = "https://api.truelayer-sandbox.com"

    # Celery (defaults to redis_url when blank)
    celery_broker_url: str = ""
    celery_result_backend: str = ""

    # ---------------------------------------------------------------------------
    # Derived properties
    # ---------------------------------------------------------------------------

    @property
    def async_database_url(self) -> str:
        """Replace scheme so asyncpg is used instead of psycopg2."""
        url = str(self.database_url)
        return url.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
            "postgres://", "postgresql+asyncpg://", 1
        )

    @property
    def effective_celery_broker(self) -> str:
        return self.celery_broker_url or str(self.redis_url)

    @property
    def effective_celery_backend(self) -> str:
        return self.celery_result_backend or str(self.redis_url)


settings = Settings()  # type: ignore[call-arg]
