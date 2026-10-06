"""Small logging foundation with basic secret redaction."""

import logging
import re
import sys
from collections.abc import Iterable
from urllib.parse import quote


def _redact(message: str, secrets: Iterable[str]) -> str:
    for secret in secrets:
        for value in (secret, secret.strip(), quote(secret.strip(), safe="")):
            if value:
                message = message.replace(value, "[REDACTED]")
    message = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", message)
    return re.sub(r"(?i)(/bot)\d+(?::|%3a)[A-Za-z0-9_-]+", r"\1[REDACTED]", message)


class SecretRedactionFormatter(logging.Formatter):
    """Redact the final output, including exception chains and encoded URLs."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")
        self._secrets = tuple(secrets)

    def format(self, record: logging.LogRecord) -> str:
        return _redact(super().format(record), self._secrets)


class SecretRedactionFilter(logging.Filter):
    """Redact configured secret values from log messages."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = _redact(message, self._secrets)
        record.args = ()
        return True


def configure_logging(level: str = "INFO", secrets: Iterable[str] = ()) -> None:
    """Configure readable local logging without an observability stack."""

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(SecretRedactionFormatter(secrets))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
