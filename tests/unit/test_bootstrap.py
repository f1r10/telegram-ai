import logging

from _pytest.capture import CaptureFixture
from pydantic import SecretStr

from office_assistant.core.settings import Settings
from office_assistant.main import bootstrap


def test_package_bootstrap_initializes_logging() -> None:
    settings = Settings(app_env="test", log_level="INFO")

    result = bootstrap(settings)

    assert result is settings
    assert logging.getLogger().level == logging.INFO


def test_logging_redacts_secret_values(capsys: CaptureFixture[str]) -> None:
    settings = Settings(
        app_env="test",
        log_level="INFO",
        openai_api_key=SecretStr("super-secret-key"),
    )
    bootstrap(settings)

    logger = logging.getLogger("test.secret")
    logger.info("token=%s", "super-secret-key")

    output = capsys.readouterr().out
    assert "super-secret-key" not in output
    assert "[REDACTED]" in output
