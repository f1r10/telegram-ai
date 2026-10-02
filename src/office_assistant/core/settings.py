"""Typed application settings loaded from environment variables."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration shared by the application and future worker process."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    log_level: str = "INFO"
    postgres_db: str | None = None
    postgres_user: str | None = None
    postgres_password: SecretStr | None = None
    postgres_host: str | None = None
    postgres_port: int = 5432
    postgres_pool_size: int = 5
    postgres_max_overflow: int = 2
    postgres_pool_timeout: float = 10.0
    postgres_echo: bool = False
    database_check_on_startup: bool = False
    qdrant_url: str | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_access_password: SecretStr | None = None
    telegram_polling_enabled: bool = False
    openai_api_key: SecretStr | None = None
    web_search_primary_provider: str = "searxng"
    searxng_base_url: str | None = None
    tavily_api_key: SecretStr | None = None
    exa_api_key: SecretStr | None = None
    file_storage_path: str = "./var/storage"
    max_upload_bytes: int = 25 * 1024 * 1024
    max_files_per_pending_session: int = 100
    pending_session_ttl_hours: int = 24


def load_settings() -> Settings:
    """Load settings for the current process."""

    return Settings()
