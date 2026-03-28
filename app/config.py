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
    kyc_webhook_secret: str = ""         # For verifying KYC provider webhook payloads

    # Supabase Storage — for KYC document uploads
    supabase_url: str = ""               # e.g. https://<project>.supabase.co
    supabase_service_role_key: str = ""  # Service role key (never expose to clients)
    kyc_bucket: str = "kyc-documents"   # Bucket name in Supabase Storage

    # Field encryption (AES-256-GCM)
    encryption_key: str = Field(
        ...,
        description="32-byte hex string. Generate: openssl rand -hex 32",
    )

    # Open Banking — TrueLayer
    openbanking_provider: str = "TRUELAYER"   # "TRUELAYER" | "YAPILY"
    truelayer_client_id: str = ""
    truelayer_client_secret: str = ""
    truelayer_webhook_secret: str = ""
    truelayer_base_url: str = "https://api.truelayer-sandbox.com"
    truelayer_auth_url: str = "https://auth.truelayer-sandbox.com"
    # Merchant account that receives open banking payments (from TrueLayer dashboard)
    truelayer_merchant_account_id: str = ""
    # Override redirect URI — defaults to {app_base_url}/api/v1/webhooks/openbanking/...
    truelayer_redirect_uri: str = ""

    # Open Banking — Yapily (alternative to TrueLayer)
    # Set OPENBANKING_PROVIDER=YAPILY and fill these to activate.
    yapily_application_id: str = ""
    yapily_application_secret: str = ""
    yapily_webhook_secret: str = ""
    yapily_base_url: str = "https://api.yapily.com"
    # Payee/merchant account that receives payments — required for PIS
    yapily_payee_name: str = ""
    yapily_payee_sort_code: str = ""          # UK domestic payments
    yapily_payee_account_number: str = ""     # UK domestic payments
    yapily_payee_iban: str = ""               # SEPA / international payments

    # Card payments — Stripe (Phase 3.8 fallback)
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    # Override Stripe redirect URI — defaults to {app_base_url}/api/v1/webhooks/stripe/...
    stripe_redirect_uri: str = ""

    # Card processor — UP Nigeria (up-ng.com)
    # Leave blank to use the dev stub (DevCardProcessorClient).
    # Set UP_NIGERIA_API_KEY to activate UPNigeriaClient automatically.
    up_nigeria_api_key: str = ""
    up_nigeria_base_url: str = "https://api.up-ng.com"      # confirm with UP Nigeria
    up_nigeria_card_program_id: str = "ufirst_prepaid_v1"   # set after UP Nigeria onboarding
    up_nigeria_webhook_secret: str = ""                      # for verifying UP Nigeria webhooks

    # Application base URL — used to build redirect / webhook URIs
    app_base_url: str = "http://localhost:8000"
    # Frontend URL — used to redirect the browser after OAuth/bank-link callbacks
    frontend_url: str = "http://localhost:5173"

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
