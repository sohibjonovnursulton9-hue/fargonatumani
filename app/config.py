"""
Application configuration loaded from environment variables.
No secrets are hardcoded. All sensitive values come from .env file.
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings. All secrets loaded from environment variables."""

    # --- Telegram ---
    telegram_bot_token: str = Field(..., description="Telegram Bot API token")
    telegram_webhook_url: str = Field(default="", description="Reserved; the runtime uses polling")
    telegram_webhook_secret: str = Field(
        default="change-me", description="Secret for webhook verification"
    )

    # --- Database ---
    database_url: str = Field(
        default="sqlite+aiosqlite:///./ftmt.db",
        description="Database connection URL",
    )

    # --- Admin ---
    admin_secret_key: str = Field(..., description="Secret key for session signing")
    pii_encryption_key: str = Field(default="", description="Fernet key for identity fields")
    admin_session_lifetime: int = Field(default=28800, description="Session lifetime in seconds")

    # --- Initial Admin ---
    initial_admin_telegram_id: str = Field(default="", description="Initial super admin Telegram ID")
    initial_admin_username: str = Field(default="admin", description="Initial admin username")
    initial_admin_password: str = Field(default="", description="Initial admin password")

    # --- Application ---
    app_env: str = Field(default="development", description="Environment: development/staging/production")
    log_level: str = Field(default="INFO", description="Logging level")
    display_timezone: str = Field(default="Asia/Tashkent", description="Display timezone")

    # --- Rate Limiting ---
    max_complaints_per_day: int = Field(default=10, description="Max complaints per user per 24h")
    max_login_attempts: int = Field(default=5, description="Max login attempts before lockout")
    login_lockout_seconds: int = Field(default=900, description="Lockout duration in seconds")

    # --- File Uploads ---
    max_file_size: int = Field(default=20_971_520, description="Max file size in bytes")
    allowed_file_extensions: str = Field(
        default="jpg,jpeg,png,pdf,doc,docx,mp4",
        description="Comma-separated allowed extensions",
    )

    # --- Deadlines ---
    default_ariza_deadline_days: int = Field(
        default=15,
        description="PROVISIONAL: Default deadline for ariza/shikoyat in calendar days",
    )
    default_shikoyat_deadline_days: int = Field(
        default=15,
        description="PROVISIONAL: Default deadline for shikoyat in calendar days",
    )
    default_taklif_deadline_days: int = Field(
        default=30,
        description="PROVISIONAL: Default deadline for taklif in calendar days",
    )
    forwarding_deadline_days: int = Field(
        default=5,
        description="PROVISIONAL: Deadline for forwarding to correct agency",
    )

    # --- Feature Flags ---
    seed_demo_data: bool = Field(
        default=False,
        description="Seed provisional demo catalogs only when explicitly enabled in development.",
    )

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def use_webhook(self) -> bool:
        return bool(self.telegram_webhook_url)

    @property
    def allowed_extensions_list(self) -> list[str]:
        return [ext.strip().lower() for ext in self.allowed_file_extensions.split(",")]

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        for prefix in ("postgres://", "postgresql://"):
            if isinstance(value, str) and value.startswith(prefix):
                return "postgresql+asyncpg://" + value[len(prefix):]
        return value

    @field_validator("app_env")
    @classmethod
    def validate_app_env(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        if v not in allowed:
            msg = f"app_env must be one of {allowed}"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.telegram_webhook_url:
            raise ValueError("Webhook mode is not implemented; leave TELEGRAM_WEBHOOK_URL empty")
        if self.app_env == "production":
            invalid = (
                len(self.telegram_bot_token) < 30
                or self.telegram_bot_token.startswith("0000000000:")
                or len(self.admin_secret_key) < 32
                or len(self.pii_encryption_key) < 40
                or self.admin_secret_key.startswith("change-me")
                or not self.database_url.startswith(("postgresql+asyncpg://", "postgres://"))
            )
            if invalid:
                raise ValueError(
                    "Production requires a real Telegram token, a strong admin secret, "
                    "and a PostgreSQL DATABASE_URL."
                )
        return self

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


# Singleton settings instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get application settings singleton. Loads from .env on first call."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Reset settings singleton. Useful for testing."""
    global _settings
    _settings = None
