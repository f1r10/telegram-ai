from _pytest.monkeypatch import MonkeyPatch

from office_assistant.core.settings import Settings


def test_settings_load_with_test_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("POSTGRES_PORT", "55432")

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.postgres_port == 55432
