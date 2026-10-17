"""Phase 12C: a caption queues the claim and /saxla is what makes it true (§22.3-§22.7, §23.7)."""

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.ingestion import IngestionSessionService, SessionSummary
from office_assistant.application.reference_images import (
    ReferenceImageDenialReason,
    ReferenceImageResult,
    ReferenceImageSaveDenied,
    ReferenceImageService,
    ReferenceImageStatus,
)
from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    FileObject,
    FileObjectState,
    ImageAsset,
    ImageAssetOccurrence,
    ImageAssetOrigin,
    IngestionItemType,
    IngestionJob,
    IngestionSession,
    IngestionSessionItem,
    IngestionSessionStatus,
    PendingProductImageConfirmation,
    PendingProductImageState,
    Product,
    ProductImageRelation,
    ProductImageRelationConfirmation,
    ProductImageRelationRole,
    SourceReference,
    SourceType,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.processing.image_assets import ImageAssetRegistry
from office_assistant.repositories.product_image_relations import ProductImageRelationRepository

pytestmark = pytest.mark.integration

TELEGRAM_USER_ID = 941203
CHAT_ID = 481203
MESSAGE_ID = 6001
OTHER_MESSAGE_ID = 6002
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + b"\x00" * 32
SECOND_PNG = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + b"\x11" * 32
NOT_A_PICTURE = b"this is a text file wearing a .jpg name"


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _settings_or_skip() -> Settings:
    settings = load_settings()
    if not all(
        (
            settings.postgres_db,
            settings.postgres_user,
            settings.postgres_password,
            settings.postgres_host,
        )
    ):
        pytest.skip("PostgreSQL integration settings are not configured")
    return settings


async def _clean(factory: async_sessionmaker[AsyncSession]) -> None:
    """Remove only this file's universe, in dependency order, so leftovers cannot mislead a run."""

    async with factory.begin() as session:
        mine = select(User.id).where(User.telegram_user_id == TELEGRAM_USER_ID)
        # Read the ids first: once the items are gone nothing remembers which files were theirs.
        my_files = set(
            (
                await session.execute(
                    select(IngestionSessionItem.file_object_id).where(
                        IngestionSessionItem.sender_user_id.in_(mine)
                    )
                )
            )
            .scalars()
            .all()
        )
        await session.execute(
            delete(PendingProductImageConfirmation).where(
                PendingProductImageConfirmation.product_id.in_(
                    select(Product.id).where(Product.created_by_user_id.in_(mine))
                )
            )
        )
        await session.execute(
            delete(ProductImageRelation).where(ProductImageRelation.created_by_user_id.in_(mine))
        )
        if my_files:
            await session.execute(
                delete(ImageAssetOccurrence).where(
                    ImageAssetOccurrence.source_file_id.in_(my_files)
                )
            )
        await session.execute(
            delete(ImageAsset).where(
                ImageAsset.content_sha256.in_([_digest(PNG_BYTES), _digest(SECOND_PNG)])
            )
        )
        await session.execute(delete(Product).where(Product.created_by_user_id.in_(mine)))
        await session.execute(
            delete(SourceReference).where(SourceReference.actor_user_id.in_(mine))
        )
        await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(mine)))
        await session.execute(
            delete(IngestionSessionItem).where(IngestionSessionItem.sender_user_id.in_(mine))
        )
        if my_files:
            await session.execute(delete(FileObject).where(FileObject.id.in_(my_files)))
        # /saxla queues the processing job too, and that job restricts the session.
        await session.execute(
            delete(IngestionJob).where(
                IngestionJob.session_id.in_(
                    select(IngestionSession.id).where(IngestionSession.user_id.in_(mine))
                )
            )
        )
        await session.execute(delete(IngestionSession).where(IngestionSession.user_id.in_(mine)))
        await session.execute(delete(User).where(User.telegram_user_id == TELEGRAM_USER_ID))


async def _queue(
    service: ReferenceImageService, code: str, message_id: int = MESSAGE_ID
) -> ReferenceImageResult:
    return await service.record_product_picture(
        telegram_user_id=TELEGRAM_USER_ID,
        telegram_chat_id=CHAT_ID,
        telegram_message_id=message_id,
        canonical_code=code,
    )


async def _actors(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory.begin() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == TELEGRAM_USER_ID))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                telegram_user_id=TELEGRAM_USER_ID,
                role=UserRole.USER,
                status=UserStatus.ACTIVE,
            )
            session.add(user)
            await session.flush()
        return int(user.id)


async def _actor(factory: async_sessionmaker[AsyncSession]) -> User:
    """The persisted user, detached, because the ingestion service reads its identity from it."""

    async with factory() as session:
        return (
            await session.execute(select(User).where(User.telegram_user_id == TELEGRAM_USER_ID))
        ).scalar_one()


async def _save(
    factory: async_sessionmaker[AsyncSession], storage: LocalFileStorage
) -> SessionSummary:
    """Run the real /saxla boundary, the way the command handler does."""

    summary = await IngestionSessionService(factory, storage).commit(await _actor(factory))
    assert summary is not None
    return summary


async def _discard(factory: async_sessionmaker[AsyncSession], storage: LocalFileStorage) -> bool:
    return await IngestionSessionService(factory, storage).cancel(await _actor(factory))


async def _seed_upload(
    factory: async_sessionmaker[AsyncSession],
    storage: LocalFileStorage,
    *,
    user_id: int,
    data: bytes,
    message_id: int = MESSAGE_ID,
) -> tuple[int, str]:
    """Stage one picture exactly as the upload turn does: an open session and unstored bytes.

    The file stays STAGED under its staging key, because activation is what ``/saxla`` does; a test
    that skipped that would not be testing the boundary the owner decided on.
    """

    staged_path, staged_key = storage.create_staging_path(".jpg")
    staged_path.write_bytes(data)
    async with factory.begin() as session:
        ingestion_session = (
            await session.execute(
                select(IngestionSession).where(
                    IngestionSession.user_id == user_id,
                    IngestionSession.status == IngestionSessionStatus.OPEN,
                )
            )
        ).scalar_one_or_none()
        if ingestion_session is None:
            ingestion_session = IngestionSession(
                user_id=user_id,
                status=IngestionSessionStatus.OPEN,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
            session.add(ingestion_session)
            await session.flush()
        file_object = (
            await session.execute(select(FileObject).where(FileObject.sha256 == _digest(data)))
        ).scalar_one_or_none()
        if file_object is None:
            file_object = FileObject(
                storage_key=staged_key,
                original_filename="telegram_photo.jpg",
                content_type="image/jpeg",
                byte_size=len(data),
                sha256=_digest(data),
                state=FileObjectState.STAGED,
            )
            session.add(file_object)
            await session.flush()
        else:
            storage.delete(staged_key)
        session.add(
            IngestionSessionItem(
                session_id=ingestion_session.id,
                item_type=IngestionItemType.FILE,
                file_object_id=file_object.id,
                submitted_filename="telegram_photo.jpg",
                sender_user_id=user_id,
                telegram_chat_id=CHAT_ID,
                telegram_message_id=message_id,
                sequence_no=message_id,
            )
        )
        return int(file_object.id), staged_key


async def _seed_product(
    factory: async_sessionmaker[AsyncSession], *, user_id: int, code: str
) -> UUID:
    """A product that already exists: §23.7 forbids creating one from a caption."""

    async with factory.begin() as session:
        source = SourceReference(
            source_type=SourceType.MANUAL_ADMIN,
            actor_user_id=user_id,
            description="Seeded by the Phase 12C integration test",
        )
        session.add(source)
        await session.flush()
        product = Product(
            canonical_code=code,
            display_code=code,
            name=f"{code} test məhsulu",
            description=None,
            is_active=True,
            created_by_user_id=user_id,
            source_reference_id=source.id,
        )
        session.add(product)
        await session.flush()
        return product.id


async def _relations(factory: async_sessionmaker[AsyncSession]) -> list[ProductImageRelation]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(ProductImageRelation).where(
                        ProductImageRelation.created_by_user_id.in_(
                            select(User.id).where(User.telegram_user_id == TELEGRAM_USER_ID)
                        )
                    )
                )
            ).scalars()
        )


async def _queued(
    factory: async_sessionmaker[AsyncSession],
) -> list[PendingProductImageConfirmation]:
    """Every claim this test queued, pending or resolved, newest state included."""

    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(PendingProductImageConfirmation)
                    .where(
                        PendingProductImageConfirmation.product_id.in_(
                            select(Product.id).where(
                                Product.created_by_user_id.in_(
                                    select(User.id).where(User.telegram_user_id == TELEGRAM_USER_ID)
                                )
                            )
                        )
                    )
                    .order_by(PendingProductImageConfirmation.id)
                )
            ).scalars()
        )


async def _counts(factory: async_sessionmaker[AsyncSession]) -> tuple[int, int, int, int, int]:
    """(products, relations, assets, occurrences, source references) owned by this test."""

    async with factory() as session:
        mine = select(User.id).where(User.telegram_user_id == TELEGRAM_USER_ID)
        products = (
            await session.execute(
                select(func.count())
                .select_from(Product)
                .where(Product.created_by_user_id.in_(mine))
            )
        ).scalar_one()
        relations = (
            await session.execute(
                select(func.count())
                .select_from(ProductImageRelation)
                .where(ProductImageRelation.created_by_user_id.in_(mine))
            )
        ).scalar_one()
        assets = (
            await session.execute(
                select(func.count())
                .select_from(ImageAsset)
                .where(ImageAsset.content_sha256.in_([_digest(PNG_BYTES), _digest(SECOND_PNG)]))
            )
        ).scalar_one()
        occurrences = (
            await session.execute(
                select(func.count())
                .select_from(ImageAssetOccurrence)
                .where(
                    ImageAssetOccurrence.source_file_id.in_(
                        select(IngestionSessionItem.file_object_id).where(
                            IngestionSessionItem.sender_user_id.in_(mine)
                        )
                    )
                )
            )
        ).scalar_one()
        sources = (
            await session.execute(
                select(func.count())
                .select_from(SourceReference)
                .where(SourceReference.actor_user_id.in_(mine))
            )
        ).scalar_one()
        return (
            int(products),
            int(relations),
            int(assets),
            int(occurrences),
            int(sources),
        )


def _scenario(
    behavior: Callable[
        [async_sessionmaker[AsyncSession], LocalFileStorage, ReferenceImageService], Awaitable[None]
    ],
) -> None:
    async def run() -> None:
        settings = _settings_or_skip()
        root = Path(settings.file_storage_path) / "phase12c-test"
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        try:
            # A run that died before its cleanup would leave bytes that look like this test's work.
            shutil.rmtree(root, ignore_errors=True)
            # Constructed after the wipe because the storage namespaces are created on construction.
            storage = LocalFileStorage(root)
            await _clean(factory)
            await behavior(factory, storage, ReferenceImageService(factory, storage))
        finally:
            await _clean(factory)
            await engine.dispose()
            shutil.rmtree(root, ignore_errors=True)

    asyncio.run(run())


def test_a_naming_caption_queues_the_claim_and_saxla_links_it() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id = await _actors(factory)
        file_id, _ = await _seed_upload(factory, storage, user_id=user_id, data=PNG_BYTES)
        product_id = await _seed_product(factory, user_id=user_id, code="KA45")
        before = await _counts(factory)

        result = await _queue(service, "KA45")

        # The caption is the user's intent; /saxla is the save. Until then nothing is authoritative.
        assert result.status is ReferenceImageStatus.QUEUED
        assert result.display_code == "KA45"
        assert await _counts(factory) == before
        assert [row.status for row in await _queued(factory)] == [PendingProductImageState.PENDING]
        assert await _relations(factory) == []
        image_asset_root = Path(storage.image_asset_root)
        assert not any(image_asset_root.rglob("*"))

        summary = await _save(factory, storage)

        assert summary.reference_images_linked == 1
        relations = await _relations(factory)
        assert len(relations) == 1
        relation = relations[0]
        assert relation.product_id == product_id
        assert relation.role == ProductImageRelationRole.USER_CONFIRMED
        assert relation.is_reference is True
        assert relation.confirmation_type == ProductImageRelationConfirmation.USER_EXPLICIT
        assert relation.superseded_at is None
        assert relation.created_by_user_id == user_id
        queued = await _queued(factory)
        assert [(row.status, row.resolved_at is not None) for row in queued] == [
            (PendingProductImageState.LINKED, True)
        ]

        async with factory() as session:
            asset = (
                await session.execute(
                    select(ImageAsset).where(ImageAsset.content_sha256 == _digest(PNG_BYTES))
                )
            ).scalar_one()
            assert relation.image_asset_id == asset.id
            # §23.5: nothing here claims a locator, because no parser sighted this picture yet.
            occurrences = (
                await session.execute(
                    select(func.count())
                    .select_from(ImageAssetOccurrence)
                    .where(ImageAssetOccurrence.image_asset_id == asset.id)
                )
            ).scalar_one()
            assert int(occurrences) == 0

            file = await session.get(FileObject, file_id)
            assert file is not None
            # The owner's isolation rule: the inviolable original stays in ``blobs/`` while the
            # internal resource lives in ``image_assets/``, so pruning either keeps both true.
            blob_path = storage.path_for_key(file.storage_key)
            asset_path = storage.path_for_key(asset.storage_key)
            assert file.storage_key.startswith("blobs/")
            assert asset.storage_key.startswith("image_assets/")
            assert blob_path.read_bytes() == PNG_BYTES == asset_path.read_bytes()

            source = await session.get(SourceReference, relation.source_reference_id)
            assert source is not None
            assert source.file_id == file_id
            assert source.source_type == SourceType.TELEGRAM_USER
            assert source.locator == {"telegram_message_id": MESSAGE_ID}

            audited = (
                (
                    await session.execute(
                        select(AuditEvent).where(AuditEvent.action == "PRODUCT_IMAGE_CONFIRMED")
                    )
                )
                .scalars()
                .all()
            )
            assert [event.entity_id for event in audited] == [str(relation.id)]
            assert audited[0].event_metadata == {
                "product_id": str(product_id),
                "image_asset_id": str(asset.id),
                "code": "KA45",
                "ingestion_session_id": str(summary.session_id),
            }

    _scenario(behavior)


def test_deactivated_product_photo_is_not_returned_as_active_identity() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id = await _actors(factory)
        await _seed_upload(factory, storage, user_id=user_id, data=PNG_BYTES)
        product_id = await _seed_product(factory, user_id=user_id, code="KA45")
        assert (await _queue(service, "KA45")).status is ReferenceImageStatus.QUEUED
        await _save(factory, storage)
        async with factory() as session:
            relation = ProductImageRelationRepository(session)
            assert (
                await relation.active_confirmation_holder_by_content(
                    _digest(PNG_BYTES), active_product_only=True
                )
            ) == (product_id, "KA45")
        async with factory.begin() as session:
            product = await session.get(Product, product_id, with_for_update=True)
            assert product is not None
            product.is_active = False
        async with factory() as session:
            relation = ProductImageRelationRepository(session)
            # The photo-question lookup must NOT describe a retired SKU
            # as presently active, even with a byte-exact identical picture.
            assert (
                await relation.active_confirmation_holder_by_content(
                    _digest(PNG_BYTES), active_product_only=True
                )
                is None
            )
            # The historical ownership rule still blocks silently
            # reassigning this picture to a different company product.
            assert (await relation.active_confirmation_holder_by_content(_digest(PNG_BYTES))) == (
                product_id,
                "KA45",
            )

    _scenario(behavior)


def test_cancel_discards_the_queued_claim_and_the_staged_picture() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id = await _actors(factory)
        _, staged_key = await _seed_upload(factory, storage, user_id=user_id, data=PNG_BYTES)
        await _seed_product(factory, user_id=user_id, code="KA45")
        assert (await _queue(service, "KA45")).status is ReferenceImageStatus.QUEUED

        assert await _discard(factory, storage) is True

        # §23.7: a cancel must not create an authoritative relation, and the claim goes with it.
        assert await _relations(factory) == []
        assert [row.status for row in await _queued(factory)] == [
            PendingProductImageState.CANCELLED
        ]
        assert not storage.path_for_key(staged_key).exists()
        assert not any(Path(storage.image_asset_root).rglob("*"))
        async with factory() as session:
            file = (
                await session.execute(
                    select(FileObject).where(FileObject.sha256 == _digest(PNG_BYTES))
                )
            ).scalar_one()
            assert file.state == FileObjectState.DELETED
            session_summary = (
                (
                    await session.execute(
                        select(IngestionSession.status).where(IngestionSession.user_id == user_id)
                    )
                )
                .scalars()
                .one()
            )
            assert session_summary == IngestionSessionStatus.CANCELLED

    _scenario(behavior)


def test_a_committed_relation_survives_a_later_unrelated_cancel() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id = await _actors(factory)
        await _seed_upload(factory, storage, user_id=user_id, data=PNG_BYTES)
        await _seed_product(factory, user_id=user_id, code="KA45")
        await _queue(service, "KA45")
        await _save(factory, storage)
        linked = await _relations(factory)
        assert len(linked) == 1

        # A second package the user walks away from: the earlier decision is already business truth.
        await _seed_upload(
            factory, storage, user_id=user_id, data=SECOND_PNG, message_id=OTHER_MESSAGE_ID
        )
        assert await _discard(factory, storage) is True

        assert [(row.product_id, row.superseded_at) for row in await _relations(factory)] == [
            (linked[0].product_id, None)
        ]

    _scenario(behavior)


def test_renaming_the_picture_before_the_save_moves_the_queued_claim() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id = await _actors(factory)
        await _seed_upload(factory, storage, user_id=user_id, data=PNG_BYTES)
        first = await _seed_product(factory, user_id=user_id, code="KA45")
        second = await _seed_product(factory, user_id=user_id, code="KA46")

        assert (await _queue(service, "KA45")).status is ReferenceImageStatus.QUEUED
        assert (await _queue(service, "KA46")).status is ReferenceImageStatus.QUEUED

        # One message carries one claim, and until /saxla the user's own words still outrank it.
        queued = await _queued(factory)
        assert [(row.status, row.product_id) for row in queued] == [
            (PendingProductImageState.PENDING, second)
        ]
        assert first != second

        await _save(factory, storage)
        assert [(row.product_id, row.role) for row in await _relations(factory)] == [
            (second, ProductImageRelationRole.USER_CONFIRMED)
        ]

    _scenario(behavior)


def test_an_unknown_product_is_not_created_and_nothing_is_written() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id = await _actors(factory)
        await _seed_upload(factory, storage, user_id=user_id, data=PNG_BYTES)
        before = await _counts(factory)

        result = await _queue(service, "ZZ99")

        assert result.status is ReferenceImageStatus.PRODUCT_NOT_FOUND
        assert result.display_code is None
        assert await _counts(factory) == before
        assert await _queued(factory) == []
        # §23.7: no product, no asset, and not one byte copied into the internal namespace.
        assert not any(Path(storage.image_asset_root).rglob("*"))

    _scenario(behavior)


def test_a_picture_already_confirmed_for_another_product_is_not_relinked() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id = await _actors(factory)
        await _seed_upload(factory, storage, user_id=user_id, data=PNG_BYTES)
        first = await _seed_product(factory, user_id=user_id, code="KA45")
        second = await _seed_product(factory, user_id=user_id, code="KA46")
        await _queue(service, "KA45")
        await _save(factory, storage)
        # The same bytes arrive again in a second message: ingestion reuses the stored file.
        await _seed_upload(
            factory, storage, user_id=user_id, data=PNG_BYTES, message_id=OTHER_MESSAGE_ID
        )
        before = await _counts(factory)

        result = await _queue(service, "KA46", OTHER_MESSAGE_ID)

        # §22.7: the conflict is named, not silently overwritten, and nothing is queued for it.
        assert result.status is ReferenceImageStatus.CONFLICT
        assert result.display_code == "KA45"
        assert await _counts(factory) == before
        # The only claim here is the one /saxla already honored; the rejected caption added none.
        assert [row.status for row in await _queued(factory)] == [PendingProductImageState.LINKED]
        assert [(row.product_id, row.superseded_at) for row in await _relations(factory)] == [
            (first, None)
        ]
        assert second != first

    _scenario(behavior)


def test_a_picture_already_linked_to_the_same_product_queues_nothing_new() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id = await _actors(factory)
        await _seed_upload(factory, storage, user_id=user_id, data=PNG_BYTES)
        await _seed_product(factory, user_id=user_id, code="KA45")
        await _queue(service, "KA45")
        await _save(factory, storage)
        # The same picture arrives again in a new package and is named for the product it already
        # belongs to: the user's second confirmation must not double-book the first.
        await _seed_upload(
            factory, storage, user_id=user_id, data=PNG_BYTES, message_id=OTHER_MESSAGE_ID
        )
        after_first = await _counts(factory)

        result = await _queue(service, "KA45", OTHER_MESSAGE_ID)

        assert result.status is ReferenceImageStatus.ALREADY_LINKED
        assert result.display_code == "KA45"
        assert await _counts(factory) == after_first
        assert [row.status for row in await _queued(factory)] == [PendingProductImageState.LINKED]

    _scenario(behavior)


def test_a_conflict_that_appears_before_the_save_stops_the_whole_save() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id = await _actors(factory)
        _, staged_key = await _seed_upload(factory, storage, user_id=user_id, data=PNG_BYTES)
        mine = await _seed_product(factory, user_id=user_id, code="KA45")
        other = await _seed_product(factory, user_id=user_id, code="KA46")
        assert (await _queue(service, "KA45")).status is ReferenceImageStatus.QUEUED
        # Another package confirms the same content for KA46 while this one waits.
        async with factory.begin() as session:
            asset_id = await ImageAssetRegistry(session, storage).ensure_content(PNG_BYTES)
            assert asset_id is not None
            session.add(
                ProductImageRelation(
                    product_id=other,
                    image_asset_id=asset_id,
                    role=ProductImageRelationRole.USER_CONFIRMED,
                    is_reference=True,
                    confirmation_type=str(ProductImageRelationConfirmation.USER_EXPLICIT),
                    source_reference_id=(
                        await session.execute(
                            select(Product.source_reference_id).where(Product.id == other)
                        )
                    ).scalar_one(),
                    created_by_user_id=user_id,
                )
            )

        with pytest.raises(ReferenceImageSaveDenied) as denied:
            await _save(factory, storage)

        assert denied.value.reason is ReferenceImageDenialReason.CONFLICT
        assert denied.value.canonical_code == "KA45"
        assert denied.value.holder_code == "KA46"
        # The save rolled back rather than half-applying: the file is still staged, unclaimed.
        assert [(row.product_id, row.superseded_at) for row in await _relations(factory)] == [
            (other, None)
        ]
        assert [row.status for row in await _queued(factory)] == [PendingProductImageState.PENDING]
        async with factory() as session:
            state = (
                (
                    await session.execute(
                        select(IngestionSession.status).where(IngestionSession.user_id == user_id)
                    )
                )
                .scalars()
                .one()
            )
            assert state == IngestionSessionStatus.OPEN
            file = (
                await session.execute(
                    select(FileObject).where(FileObject.sha256 == _digest(PNG_BYTES))
                )
            ).scalar_one()
            assert file.state == FileObjectState.STAGED
            assert file.storage_key == staged_key
        assert storage.path_for_key(staged_key).is_file()
        assert mine != other

    _scenario(behavior)


def test_bytes_that_state_no_picture_stop_the_save_rather_than_faking_one() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id = await _actors(factory)
        await _seed_upload(factory, storage, user_id=user_id, data=NOT_A_PICTURE)
        await _seed_product(factory, user_id=user_id, code="KA45")
        # §23.5 honesty: the caption turn does not read the bytes, so it cannot know yet.
        assert (await _queue(service, "KA45")).status is ReferenceImageStatus.QUEUED
        before = await _counts(factory)

        with pytest.raises(ReferenceImageSaveDenied) as denied:
            await _save(factory, storage)

        assert denied.value.reason is ReferenceImageDenialReason.UNREADABLE_PICTURE
        assert await _counts(factory) == before
        assert await _relations(factory) == []
        assert not any(Path(storage.image_asset_root).rglob("*"))

    _scenario(behavior)


def test_a_picture_the_worker_already_registered_is_reused_not_sighted_twice() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id = await _actors(factory)
        file_id, _ = await _seed_upload(factory, storage, user_id=user_id, data=PNG_BYTES)
        await _seed_product(factory, user_id=user_id, code="KA45")
        async with factory.begin() as session:
            registered = await ImageAssetRegistry(session, storage).register_bytes(
                PNG_BYTES,
                origin=ImageAssetOrigin.STANDALONE_UPLOAD,
                source_file_id=file_id,
                locator={"telegram_message_id": MESSAGE_ID},
            )
        assert registered is not None
        await _queue(service, "KA45")

        summary = await _save(factory, storage)

        assert summary.reference_images_linked == 1
        # §23.4: one content row, and the sighting the parser made is still the only one.
        assert await _counts(factory) == (1, 1, 1, 1, 2)

    _scenario(behavior)


def test_an_expired_session_discards_its_queued_claim() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id = await _actors(factory)
        _, staged_key = await _seed_upload(factory, storage, user_id=user_id, data=PNG_BYTES)
        await _seed_product(factory, user_id=user_id, code="KA45")
        await _queue(service, "KA45")
        async with factory.begin() as session:
            ingestion_session = (
                await session.execute(
                    select(IngestionSession).where(IngestionSession.user_id == user_id)
                )
            ).scalar_one()
            ingestion_session.expires_at = datetime.now(UTC) - timedelta(minutes=1)

        await IngestionSessionService(factory, storage).cleanup_expired()

        # Nobody is left to press /saxla, so the claim must not survive as a promise either.
        assert await _relations(factory) == []
        assert [row.status for row in await _queued(factory)] == [
            PendingProductImageState.CANCELLED
        ]
        assert not storage.path_for_key(staged_key).exists()

    _scenario(behavior)


def test_the_schema_keeps_one_explicit_confirmation_per_picture() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id = await _actors(factory)
        first_product = await _seed_product(factory, user_id=user_id, code="KA45")
        second_product = await _seed_product(factory, user_id=user_id, code="KA46")
        async with factory.begin() as session:
            registry = ImageAssetRegistry(session, storage)
            one = await registry.ensure_content(PNG_BYTES)
            two = await registry.ensure_content(SECOND_PNG)
            assert one is not None and two is not None

            async def confirm_for(product_id: UUID, asset_id: UUID) -> None:
                session.add(
                    ProductImageRelation(
                        product_id=product_id,
                        image_asset_id=asset_id,
                        role=ProductImageRelationRole.USER_CONFIRMED,
                        is_reference=True,
                        confirmation_type=str(ProductImageRelationConfirmation.USER_EXPLICIT),
                        source_reference_id=(
                            await session.execute(
                                select(Product.source_reference_id).where(Product.id == product_id)
                            )
                        ).scalar_one(),
                        created_by_user_id=user_id,
                    )
                )
                await session.flush()

            await confirm_for(first_product, one)
            # §22.4: one product may hold several reference pictures, so that is allowed.
            await confirm_for(first_product, two)
            # §22.7: one picture may answer to one product only, and the index enforces it.
            with pytest.raises(IntegrityError):
                await confirm_for(second_product, one)

    _scenario(behavior)


def test_the_schema_keeps_one_queued_claim_per_message_item() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id = await _actors(factory)
        await _seed_upload(factory, storage, user_id=user_id, data=PNG_BYTES)
        first = await _seed_product(factory, user_id=user_id, code="KA45")
        second = await _seed_product(factory, user_id=user_id, code="KA46")
        async with factory.begin() as session:
            item = (
                await session.execute(
                    select(IngestionSessionItem).where(
                        IngestionSessionItem.telegram_message_id == MESSAGE_ID
                    )
                )
            ).scalar_one()
            session.add(
                PendingProductImageConfirmation(
                    session_id=item.session_id,
                    session_item_id=item.id,
                    product_id=first,
                    canonical_code="KA45",
                    status=PendingProductImageState.PENDING,
                )
            )
            await session.flush()
            session.add(
                PendingProductImageConfirmation(
                    session_id=item.session_id,
                    session_item_id=item.id,
                    product_id=second,
                    canonical_code="KA46",
                    status=PendingProductImageState.PENDING,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()

    _scenario(behavior)


def test_a_superseded_confirmation_does_not_block_the_next_one() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id = await _actors(factory)
        first_product = await _seed_product(factory, user_id=user_id, code="KA45")
        second_product = await _seed_product(factory, user_id=user_id, code="KA46")
        async with factory.begin() as session:
            registry = ImageAssetRegistry(session, storage)
            asset_id = await registry.ensure_content(PNG_BYTES)
            assert asset_id is not None
            holder = ProductImageRelation(
                product_id=first_product,
                image_asset_id=asset_id,
                role=ProductImageRelationRole.USER_CONFIRMED,
                is_reference=True,
                confirmation_type=str(ProductImageRelationConfirmation.USER_EXPLICIT),
                source_reference_id=(
                    await session.execute(
                        select(Product.source_reference_id).where(Product.id == first_product)
                    )
                ).scalar_one(),
                created_by_user_id=user_id,
            )
            session.add(holder)
            await session.flush()
            holder_id = holder.id
            holder.superseded_at = datetime.now(UTC)

        async with factory.begin() as session:
            session.add(
                ProductImageRelation(
                    product_id=second_product,
                    image_asset_id=asset_id,
                    role=ProductImageRelationRole.USER_CONFIRMED,
                    is_reference=True,
                    confirmation_type=str(ProductImageRelationConfirmation.USER_EXPLICIT),
                    source_reference_id=(
                        await session.execute(
                            select(Product.source_reference_id).where(Product.id == second_product)
                        )
                    ).scalar_one(),
                    created_by_user_id=user_id,
                )
            )
            await session.flush()
            superseded = await session.get(ProductImageRelation, holder_id)
            assert superseded is not None
            # History is kept rather than deleted (§23.6): the old claim is still provable.
            assert superseded.superseded_at is not None

    _scenario(behavior)
