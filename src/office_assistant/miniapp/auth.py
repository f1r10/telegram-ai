"""Strict Telegram Web App initData verifier.

This is NOT a Telegram Login Widget verifier: WebAppData uses a bot-token-
derived HMAC secret. Never trust Telegram.WebApp.initDataUnsafe or a client-
submitted role, user ID, stock, price, or source file ID.
"""

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import cast
from urllib.parse import parse_qsl

_MAX_INIT_DATA = 8192
_HASH_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


class MiniAppAuthenticationError(ValueError):
    """Untrusted request: no reason text is suitable for the HTTP response."""


@dataclass(frozen=True, slots=True)
class VerifiedTelegramUser:
    telegram_user_id: int
    auth_date: int
    query_id: str | None


def verify_telegram_init_data(
    init_data: str,
    bot_token: str,
    *,
    now: int | None = None,
    max_age_seconds: int = 3600,
    allowed_clock_skew_seconds: int = 30,
) -> VerifiedTelegramUser:
    """Verify Telegram's signed raw query string, with replay-age bounds.

    This proves Telegram signed the identity, NOT that it is ACTIVE in our
    database. The database authorization check is mandatory on every request.
    """

    if not 0 < len(init_data) <= _MAX_INIT_DATA:
        raise MiniAppAuthenticationError("Invalid signed data")
    if not bot_token or max_age_seconds <= 0 or allowed_clock_skew_seconds < 0:
        raise ValueError("Invalid server verifier configuration")
    try:
        pairs = parse_qsl(
            init_data,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=32,
        )
    except (ValueError, UnicodeError) as error:
        raise MiniAppAuthenticationError("Invalid signed data") from error
    if not pairs or len(pairs) != len({name for name, _ in pairs}):
        raise MiniAppAuthenticationError("Duplicate or missing parameters")
    fields = dict(pairs)
    signed_hash = fields.get("hash")
    if signed_hash is None or not _HASH_RE.fullmatch(signed_hash):
        raise MiniAppAuthenticationError("Missing signature")
    if any(not name or any(character in name for character in "\r\n=") for name in fields):
        raise MiniAppAuthenticationError("Invalid parameter name")
    canonical = "\n".join(
        f"{name}={value}"
        for name, value in sorted(fields.items())
        if name not in {"hash", "signature"}
    )
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signed_hash.lower()):
        raise MiniAppAuthenticationError("Invalid signature")
    date_value = fields.get("auth_date")
    if date_value is None or not re.fullmatch(r"[0-9]{1,12}", date_value):
        raise MiniAppAuthenticationError("Invalid authentication time")
    authenticated_at = int(date_value)
    current = int(time.time()) if now is None else now
    if (
        authenticated_at <= 0
        or authenticated_at > current + allowed_clock_skew_seconds
        or current - authenticated_at > max_age_seconds
    ):
        raise MiniAppAuthenticationError("Expired or future authentication")
    user_json = fields.get("user")
    if user_json is None or len(user_json) > 4096:
        raise MiniAppAuthenticationError("Missing signed user")
    try:
        raw: object = json.loads(user_json)
    except (ValueError, TypeError) as error:
        raise MiniAppAuthenticationError("Invalid signed user") from error
    if not isinstance(raw, dict):
        raise MiniAppAuthenticationError("Invalid signed user")
    fields_user = cast(dict[str, object], raw)
    claimed_user: object = fields_user.get("id")
    if (
        not isinstance(claimed_user, int)
        or isinstance(claimed_user, bool)
        or not 0 < claimed_user < 2**63
    ):
        raise MiniAppAuthenticationError("Invalid signed user ID")
    return VerifiedTelegramUser(
        telegram_user_id=claimed_user,
        auth_date=authenticated_at,
        query_id=fields.get("query_id"),
    )
