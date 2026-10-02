from _pytest.monkeypatch import MonkeyPatch
from pydantic import SecretStr

from office_assistant.core.settings import Settings
from office_assistant.infrastructure.database.engine import build_database_url


def test_settings_load_with_test_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("POSTGRES_PORT", "55432")

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.postgres_port == 55432


def test_database_credentials_are_redacted_from_settings_and_url() -> None:
    settings = Settings(
        postgres_db="office_assistant",
        postgres_user="assistant",
        postgres_password=SecretStr("do-not-log-me"),
        postgres_host="localhost",
    )

    assert "do-not-log-me" not in repr(settings)
    assert "do-not-log-me" not in str(build_database_url(settings))


def test_telegram_access_password_is_redacted() -> None:
    settings = Settings(telegram_access_password=SecretStr("do-not-log-me"))

    assert "do-not-log-me" not in repr(settings)
