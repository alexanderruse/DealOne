import hashlib
import hmac
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    app_domain: str = "localhost"
    database_url: str = "postgresql+psycopg://dealone:dealone@db:5432/dealone"
    session_secret: str = "local-development-session-key-change-before-deployment"
    demo_admin_token: str = "local-demo-admin"
    xai_api_key: str = ""
    xai_model: str = "grok-4.20"
    resend_api_key: str = ""
    resend_from_email: str = ""
    storefront_url: str = "http://api:8000"
    worker_interval_seconds: int = 10
    real_catalog_enabled: bool = False
    payments_mode: str = "simulation"
    nessie_api_key: str = ""
    nessie_base_url: str = "https://v2-prod-api.nessieisreal.com"
    # Shared fallback account when per-permission card creation is unavailable.
    nessie_card_account_id: str = ""
    # Customer used to mint one sandbox credit card per DealOne permission.
    nessie_customer_id: str = ""
    nessie_merchant_id: str = ""
    nessie_settle_immediately: bool = False
    cancel_window_seconds: int = 50

    @property
    def worker_token(self) -> str:
        return hmac.new(self.session_secret.encode(), b"dealone-internal-worker", hashlib.sha256).hexdigest()

    def validate_runtime(self):
        if self.payments_mode not in ("simulation", "nessie"):
            raise RuntimeError("PAYMENTS_MODE must be simulation or nessie")
        if self.payments_mode == "nessie":
            if not self.nessie_api_key:
                raise RuntimeError("PAYMENTS_MODE=nessie requires NESSIE_API_KEY")
            if not (self.nessie_customer_id or self.nessie_card_account_id):
                raise RuntimeError(
                    "PAYMENTS_MODE=nessie requires NESSIE_CUSTOMER_ID (preferred) or NESSIE_CARD_ACCOUNT_ID"
                )
            if self.app_env == "production" and not self.nessie_merchant_id:
                raise RuntimeError("Production Nessie mode requires NESSIE_MERCHANT_ID")
        if self.app_env == "production":
            if len(self.session_secret) < 32 or self.session_secret.startswith("local-"):
                raise RuntimeError("Set a unique SESSION_SECRET of at least 32 characters")
            if len(self.demo_admin_token) < 24 or self.demo_admin_token.startswith("local-"):
                raise RuntimeError("Set a unique DEMO_ADMIN_TOKEN of at least 24 characters")
            if "sslmode=" not in self.database_url or "sslmode=disable" in self.database_url:
                raise RuntimeError("Tiger Cloud requires a TLS-enabled DATABASE_URL")


@lru_cache
def settings():
    return Settings()
