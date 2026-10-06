import logging
import sys
from urllib.parse import quote

from office_assistant.core.logging import SecretRedactionFormatter


def test_logs_redact_encoded_file_urls_and_exception_secrets() -> None:
    token = "123456789:fake-telegram-secret"
    secret = "test-openai-secret"
    formatter = SecretRedactionFormatter([token, secret])
    try:
        raise ValueError(f"Bearer {secret}; sk-unconfigured-secret")
    except ValueError:
        record = logging.LogRecord(
            "test",
            logging.ERROR,
            "",
            0,
            "GET https://api.telegram.org/file/bot%s/file.pdf",
            (quote(token, safe=""),),
            sys.exc_info(),
        )
    output = formatter.format(record)
    assert token not in output
    assert quote(token, safe="") not in output
    assert secret not in output
    assert "sk-unconfigured-secret" not in output
    assert "ValueError" in output
    assert "[REDACTED]" in output
