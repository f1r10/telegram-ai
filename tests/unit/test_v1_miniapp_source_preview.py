"""Actor-bound Mini App original image preview cannot leak revoked/cross-user files."""

import asyncio
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.infrastructure.database.models import ImageAssetOrigin, User, UserStatus
from office_assistant.internal_evidence.delivery import FileDeliveryError, SourceFileDeliveryService
from office_assistant.miniapp.backend import MiniAppBackend, SourceTicket
from office_assistant.miniapp.photo_lookup import MiniAppPhotoLookup


def _image() -> bytes:
    output = BytesIO()
    with Image.new("RGB", (32, 32), (30, 80, 120)) as image:
        image.save(output, "PNG")
    return output.getvalue()


class Session:
    def __init__(self, record: tuple[object, ...] | None) -> None:
        self.record = record

    async def __aenter__(self) -> "Session":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, _query: object) -> SimpleNamespace:
        return SimpleNamespace(first=lambda: self.record)


class Factory:
    def __init__(self, record: tuple[object, ...] | None) -> None:
        self.record = record

    def __call__(self) -> Session:
        return Session(self.record)


def _backend(tmp_path: Path, record: tuple[object, ...] | None) -> MiniAppBackend:
    factory = cast(async_sessionmaker[AsyncSession], Factory(record))
    backend = MiniAppBackend(factory, bot_token="test-secret")
    backend.photo_lookup = cast(MiniAppPhotoLookup, SimpleNamespace(storage_root=tmp_path))
    backend.file_delivery = cast(
        SourceFileDeliveryService,
        SimpleNamespace(resolve_catalog_original=AsyncMock(return_value=object())),
    )
    return backend


def test_preview_verifies_ticket_actor_active_source_and_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from office_assistant.miniapp import backend as module

    data = _image()
    (tmp_path / "photo.png").write_bytes(data)
    record: tuple[object, ...] = (
        "catalog.pdf",
        ImageAssetOrigin.PDF_EMBEDDED,
        {"page": 2},
        "photo.png",
        sha256(data).hexdigest(),
    )
    service = _backend(tmp_path, record)
    actor = User(id=4, telegram_user_id=123, status=UserStatus.ACTIVE)
    monkeypatch.setattr(service, "actor", AsyncMock(return_value=actor))
    monkeypatch.setattr(module, "monotonic", lambda: 10.0)
    token = "actor-bound-ticket-token-with-entropy"
    service._source_tickets[token] = SourceTicket(4, 123, 11, 0, 50.0, 21)

    assert asyncio.run(service.photo_source_preview("signed", token=token)) == data
    assert service.file_delivery is not None
    mocked = service.file_delivery.resolve_catalog_original
    assert isinstance(mocked, AsyncMock)
    mocked.assert_awaited_once_with(123, file_id=11, occurrence_id=21, allow_standalone=True)
    service._source_tickets[token] = SourceTicket(99, 123, 11, 0, 50.0, 21)
    with pytest.raises(FileDeliveryError):
        asyncio.run(service.photo_source_preview("signed", token=token))
    service._source_tickets[token] = SourceTicket(4, 123, 11, 0, 9.0, 21)
    with pytest.raises(FileDeliveryError):
        asyncio.run(service.photo_source_preview("signed", token=token))


def test_source_preview_rejects_revocation_and_tampered_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _backend(tmp_path, None)
    actor = User(id=4, telegram_user_id=123, status=UserStatus.ACTIVE)
    monkeypatch.setattr(service, "actor", AsyncMock(return_value=actor))
    from office_assistant.miniapp import backend as module

    monkeypatch.setattr(module, "monotonic", lambda: 10.0)
    token = "long-opaque-source-ticket-value"
    service._source_tickets[token] = SourceTicket(4, 123, 11, 0, 50.0, 21)
    with pytest.raises(FileDeliveryError):
        asyncio.run(service.photo_source_preview("signed", token=token))
    service.session_factory = cast(
        async_sessionmaker[AsyncSession],
        Factory(("catalog.pdf", ImageAssetOrigin.PDF_EMBEDDED, {"page": 1}, "gone.png", "f" * 64)),
    )
    with pytest.raises(FileDeliveryError):
        asyncio.run(service.photo_source_preview("signed", token=token))
