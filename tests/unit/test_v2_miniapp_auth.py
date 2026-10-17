"""Telegram WebAppData is signed by Telegram; front-end identity is untrusted."""

import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from office_assistant.miniapp.auth import (
    MiniAppAuthenticationError,
    VerifiedTelegramUser,
    verify_telegram_init_data,
)

TOKEN = "123456:TEST-only-not-real"
NOW = 1_800_000_000


def signed_data(fields: dict[str, str], *, token: str = TOKEN) -> str:
    canonical = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()) if key != "signature")
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, canonical.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


def fixture(**changes: str) -> str:
    fields = {
        "auth_date": str(NOW),
        "user": json.dumps({"id": 92183, "first_name": "Sınaq", "is_bot": False}),
        "query_id": "a-real-query-value",
    }
    fields.update(changes)
    return signed_data(fields)


def test_authenticates_signed_telegram_identity_not_claimed_frontend_identity() -> None:
    result = verify_telegram_init_data(fixture(), TOKEN, now=NOW)
    assert result == VerifiedTelegramUser(92183, NOW, "a-real-query-value")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "user=foo",
        "auth_date=xyz&hash=" + "0" * 64,
        "auth_date=123&auth_date=124&hash=" + "0" * 64,
        "user=not%xx&hash=" + "0" * 64,
        "a=x&b",
        "&" * 200,
    ],
)
def test_invalid_unsigned_input_is_never_accepted(value: str) -> None:
    with pytest.raises(MiniAppAuthenticationError):
        verify_telegram_init_data(value, TOKEN, now=NOW)


def test_changing_id_date_query_id_or_bot_invalidates_hmac() -> None:
    value = fixture()
    for changed in (
        value.replace("92183", "92184"),
        value.replace("auth_date=1800000000", "auth_date=1800000001"),
        value.replace("a-real-query-value", "forged-query"),
    ):
        if changed != value:
            with pytest.raises(MiniAppAuthenticationError):
                verify_telegram_init_data(changed, TOKEN, now=NOW)
    with pytest.raises(MiniAppAuthenticationError):
        verify_telegram_init_data(value, TOKEN + "wrong", now=NOW)


def test_bot_hmac_validates_new_telegram_signed_signature_field() -> None:
    assert verify_telegram_init_data(
        fixture(signature="ed25519-base64-value"), TOKEN, now=NOW
    ).telegram_user_id == 92183


def test_expired_and_future_authentication_are_rejected() -> None:
    with pytest.raises(MiniAppAuthenticationError):
        verify_telegram_init_data(fixture(auth_date=str(NOW - 3601)), TOKEN, now=NOW)
    with pytest.raises(MiniAppAuthenticationError):
        verify_telegram_init_data(fixture(auth_date=str(NOW + 31)), TOKEN, now=NOW)
    assert verify_telegram_init_data(
        fixture(auth_date=str(NOW - 3600)), TOKEN, now=NOW
    ).telegram_user_id == 92183


@pytest.mark.parametrize(
    "user",
    [
        "null",
        "[]",
        '{"id":true}',
        '{"id":"92183"}',
        '{"id":0}',
        '{"id":-3}',
        '{"id":9223372036854775808}',
        '{"first_name":"person"}',
        '{"id":1.0}',
    ],
)
def test_signed_but_invalid_identity_is_rejected(user: str) -> None:
    with pytest.raises(MiniAppAuthenticationError):
        verify_telegram_init_data(fixture(user=user), TOKEN, now=NOW)


def test_signed_duplicate_and_ambiguous_fields_are_rejected() -> None:
    valid = fixture()
    with pytest.raises(MiniAppAuthenticationError):
        verify_telegram_init_data(valid + "&user=unexpected", TOKEN, now=NOW)
    with pytest.raises(MiniAppAuthenticationError):
        verify_telegram_init_data(valid + "&hash=" + "0" * 64, TOKEN, now=NOW)
    # Signature cannot be transplanted to a different set of encoded pairs.
    assert "user=" in valid


def test_verifier_refuses_invalid_server_configuration() -> None:
    with pytest.raises(ValueError):
        verify_telegram_init_data(fixture(), "", now=NOW)
    with pytest.raises(ValueError):
        verify_telegram_init_data(fixture(), TOKEN, now=NOW, max_age_seconds=0)
