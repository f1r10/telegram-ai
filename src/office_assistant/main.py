"""Minimal Phase 0 application entrypoint."""

import logging

from office_assistant.core.logging import configure_logging
from office_assistant.core.settings import Settings, load_settings


def bootstrap(settings: Settings | None = None) -> Settings:
    """Load configuration and initialize the application foundation."""

    resolved_settings = settings or load_settings()
    secrets = (
        secret.get_secret_value()
        for secret in (
            resolved_settings.postgres_password,
            resolved_settings.telegram_bot_token,
            resolved_settings.bootstrap_admin_password,
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
    """Start the Phase 0 process."""

    bootstrap()


if __name__ == "__main__":
    main()
