"""Minimal Phase 0 application entrypoint."""

import asyncio
import logging

from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.ingestion import IngestionSessionService
from office_assistant.application.user_management import UserManagementService
from office_assistant.core.logging import configure_logging
from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.health import check_database_health
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.integrations.telegram.app import create_telegram_application
from office_assistant.integrations.telegram.handlers import TelegramServices
from office_assistant.integrations.telegram.state import PasswordAttemptLimiter, PasswordEntryState


def bootstrap(settings: Settings | None = None) -> Settings:
    """Load configuration and initialize the application foundation."""

    resolved_settings = settings or load_settings()
    secrets = (
        secret.get_secret_value()
        for secret in (
            resolved_settings.postgres_password,
            resolved_settings.telegram_bot_token,
            resolved_settings.telegram_access_password,
            resolved_settings.openai_api_key,
            resolved_settings.tavily_api_key,
            resolved_settings.exa_api_key,
        )
        if secret is not None
    )
    configure_logging(resolved_settings.log_level, secrets)
    logging.getLogger(__name__).info(
        "Office Assistant application starting | environment=%s",
        resolved_settings.app_env,
    )
    return resolved_settings


def main() -> None:
    """Start the configured application process."""

    settings = bootstrap()
    if settings.telegram_polling_enabled:
        if settings.telegram_bot_token is None:
            raise SystemExit("TELEGRAM_BOT_TOKEN is required when polling is enabled")
        engine = create_database_engine(settings)
        session_factory = create_session_factory(engine)
        access_password = (
            settings.telegram_access_password.get_secret_value()
            if settings.telegram_access_password is not None
            else None
        )
        storage = LocalFileStorage(settings.file_storage_path)
        ingestion = IngestionSessionService(
            session_factory,
            storage,
            max_files=settings.max_files_per_pending_session,
            ttl_hours=settings.pending_session_ttl_hours,
            max_upload_bytes=settings.max_upload_bytes,
        )
        services = TelegramServices(
            authentication=AuthenticationService(session_factory, access_password),
            users=UserManagementService(session_factory),
            password_state=PasswordEntryState(),
            limiter=PasswordAttemptLimiter(),
            ingestion=ingestion,
            storage=storage,
        )
        application = create_telegram_application(
            settings.telegram_bot_token.get_secret_value(), services
        )
        try:
            application.run_polling()
        finally:
            asyncio.run(engine.dispose())
    elif settings.database_check_on_startup:
        asyncio.run(check_database_on_startup(settings))


async def check_database_on_startup(settings: Settings) -> None:
    """Optionally verify PostgreSQL and close resources for smoke checks."""

    engine = create_database_engine(settings)
    try:
        health = await check_database_health(engine)
        if not health.is_healthy:
            logging.getLogger(__name__).error(
                "Database health check failed | detail=%s", health.detail
            )
            raise SystemExit(1)
        logging.getLogger(__name__).info("Database health check passed")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    main()
