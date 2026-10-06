"""Typed application settings loaded from environment variables."""

from pydantic import Field, SecretStr, field_validator
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
    telegram_bot_token: SecretStr | None = None
    telegram_access_password: SecretStr | None = None
    telegram_polling_enabled: bool = False
    openai_api_key: SecretStr | None = None
    web_search_primary_provider: str = "searxng"
    searxng_base_url: str | None = "http://searxng:8080"
    searxng_request_timeout_seconds: float = 12.0
    tavily_api_key: SecretStr | None = None

    @field_validator("tavily_api_key", mode="before")
    @classmethod
    def normalize_tavily_key(cls, value: str | SecretStr | None) -> str | None:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if value is None:
            return None
        return value.strip() or None

    tavily_request_timeout_seconds: float = 10.0
    exa_api_key: SecretStr | None = None
    web_search_default_limit: int = 6
    web_search_max_limit: int = 8
    web_search_cache_ttl_seconds: int = 86400
    web_search_fresh_cache_ttl_seconds: int = 900
    web_fetch_timeout_seconds: float = 10.0
    web_fetch_max_bytes: int = 2 * 1024 * 1024
    web_fetch_max_redirects: int = 3
    web_fetch_max_pages: int = 2
    web_context_max_tokens: int = 3200
    file_storage_path: str = "./var/storage"
    max_upload_bytes: int = 25 * 1024 * 1024
    max_files_per_pending_session: int = 100
    pending_session_ttl_hours: int = 24
    worker_poll_interval_seconds: float = 2.0
    worker_lease_seconds: int = 300
    worker_max_attempts: int = 3
    qdrant_url: str | None = None
    qdrant_collection_alias: str = "document_chunks"
    qdrant_collection_name: str = "document_chunks_v1"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_cache_path: str = "./var/models"
    embedding_batch_size: int = 32
    chunking_version: str = "v1-medium-1800-180"
    chunk_max_chars: int = 1800
    chunk_overlap_chars: int = 180
    retrieval_default_limit: int = 5
    openai_default_model: str = "gpt-6-luna"
    openai_strong_model: str = "gpt-6.1-sol"
    openai_request_timeout_seconds: float = 30.0
    openai_max_output_tokens: int = 700
    rag_retrieval_limit: int = 5
    rag_max_context_tokens: int = 3200
    chat_history_max_messages: int = 6
    chat_history_max_tokens: int = 1200
    ai_rate_limit_per_minute: int = 12
    telegram_max_message_length: int = 4096
    structured_extraction_enabled: bool = True
    structured_extraction_model: str = "gpt-6-luna"
    structured_extraction_max_input_chars: int = 12000
    structured_extraction_max_output_tokens: int = 2200
    structured_extraction_batch_size: int = 30
    structured_review_ttl_hours: int = 72
    internal_tool_max_calls: int = 4
    internal_rag_limit: int = 5
    internal_rag_min_score: float = Field(default=0.55, ge=0.0, le=1.0)
    internal_rag_identifier_min_score: float = Field(default=0.30, ge=0.0, le=1.0)
    recent_source_context_messages: int = 6
    recent_source_context_ttl_minutes: int = 60
    file_delivery_max_bytes: int = 25 * 1024 * 1024


def load_settings() -> Settings:
    """Load settings for the current process."""

    return Settings()
