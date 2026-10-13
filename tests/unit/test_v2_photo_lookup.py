"""Telegram V2 recognizes photo lookup without silently saving incoming pictures."""

from office_assistant.integrations.telegram.handlers import (
    _is_photo_lookup_request,  # pyright: ignore[reportPrivateUsage]
)


def test_photo_lookup_in_azerbaijani() -> None:
    assert _is_photo_lookup_request("Bu məhsul bizdə varmı?")
    assert _is_photo_lookup_request("Şəkli yoxla")
    assert _is_photo_lookup_request("Bazadan bax, bu bizde var?")
    assert not _is_photo_lookup_request(None)
    assert not _is_photo_lookup_request("Bu KA45-dir, yadda saxla")
    assert not _is_photo_lookup_request("Bu məhsulun yeni nümunəsidir")
