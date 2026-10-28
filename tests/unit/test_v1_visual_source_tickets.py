"""Mini App visual source buttons never authorize by raw file IDs or stale roles."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.infrastructure.database.models import (
    ImageAssetOrigin,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.internal_evidence.delivery import (
    DeliverableFile,
    FileDeliveryError,
    SourceFileDeliveryService,
)
from office_assistant.miniapp.backend import MiniAppBackend
from office_assistant.miniapp.photo_lookup import MiniAppPhotoLookup, PhotoLookupResult
from office_assistant.processing.document_visual_candidates import (
    DocumentImageHit,
    DocumentImageSource,
)


def test_visual_source_download_ticket_is_actor_bound_and_contains_no_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    picture = DocumentImageSource(
        41,
        77,
        "catalog.pdf",
        ImageAssetOrigin.PDF_EMBEDDED,
        {"page": 4},
        "private/asset.png",
        "a" * 64,
    )
    result = PhotoLookupResult(
        "source_candidates",
        (),
        "dino_qdrant_all_active",
        False,
        "Source is evidence only",
        (DocumentImageHit(picture, "semantic", 0.94),),
        (
            {
                "kind": "source",
                "occurrence_id": "41",
                "filename": "catalog.pdf",
                "location": "səhifə 4",
                "score": 0.94,
            },
        ),
    )

    class Lookup:
        async def lookup(self, body: bytes) -> PhotoLookupResult:
            assert body == b"synthetic-photo"
            return result

    delivery = SimpleNamespace(
        resolve_catalog_original=AsyncMock(
            return_value=DeliverableFile(
                tmp_path / "source.pdf", "catalog.pdf", "application/pdf", 77
            )
        )
    )
    actor = User(id=9, telegram_user_id=1209, status=UserStatus.ACTIVE, role=UserRole.USER)

    async def authorized(self: MiniAppBackend, _init_data: str) -> User:
        del self
        return actor

    monkeypatch.setattr(MiniAppBackend, "actor", authorized)
    backend = MiniAppBackend(
        cast(async_sessionmaker[AsyncSession], object()),
        bot_token="synthetic-testing-token",
        photo_lookup=cast(MiniAppPhotoLookup, Lookup()),
        file_delivery=cast(SourceFileDeliveryService, delivery),
    )

    async def run() -> None:
        response = await backend.photo("signed-init-data", image_bytes=b"synthetic-photo")
        entries = response["ranked"]
        assert isinstance(entries, list)
        source_raw = cast(list[object], entries)[0]
        assert isinstance(source_raw, dict)
        source = cast(dict[str, object], source_raw)
        assert source.get("occurrence_id") is None
        assert source.get("storage_key") is None
        token = source.get("source_ticket")
        assert isinstance(token, str) and len(token) >= 20
        delivered = await backend.source_file("signed-init-data", token=token)
        assert delivered.file_id == 77
        assert delivered.filename == "catalog.pdf"
        delivery.resolve_catalog_original.assert_awaited_once_with(
            1209, file_id=77, occurrence_id=41, allow_standalone=True
        )
        # A token from another ACTIVE actor cannot access the same file.
        actor.id = 19
        with pytest.raises(FileDeliveryError):
            await backend.source_file("signed-init-data", token=token)

    asyncio.run(run())
