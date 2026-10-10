"""Phase 12C: an administrator's revocation retires a claim and keeps the history behind it.

The owner asked for a safe explicit revoke (§23.6, §23.7), and the property that makes it safe is
the one these tests hold: exactly one column changes. The picture, its bytes, its provenance and the
audit trail stay, so the retired decision remains provable and a parse run's sighting of the same
content is untouched, while the retired claim stops answering the active reads.
"""

import asyncio
import shutil
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Select, delete, event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session as SyncSession

from office_assistant.application.errors import AuthorizationDenied
from office_assistant.application.ingestion import IngestionSessionService, SessionSummary
from office_assistant.application.reference_images import (
    ReferenceImageResult,
    ReferenceImageService,
    ReferenceImageStatus,
    ReferenceRevokeResult,
    ReferenceRevokeStatus,
)
from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    FileObject,
    FileObjectState,
    ImageAsset,
    ImageAssetOccurrence,
    IngestionItemType,
    IngestionJob,
    IngestionSession,
    IngestionSessionItem,
    IngestionSessionStatus,
    PendingProductImageConfirmation,
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
from office_assistant.repositories.product_image_relations import ProductImageRelationRepository

pytestmark = pytest.mark.integration

CONFIRMER_TELEGRAM_ID = 941204
ADMIN_TELEGRAM_ID = 941205
MEMBER_TELEGRAM_ID = 941206
TELEGRAM_IDS = [CONFIRMER_TELEGRAM_ID, ADMIN_TELEGRAM_ID, MEMBER_TELEGRAM_ID]
CHAT_ID = 481204
MESSAGE_ID = 6101
OTHER_MESSAGE_ID = 6102
PICTURE = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + b"\x22" * 32
SECOND_PICTURE = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + b"\x33" * 32


class _WriteFailure(SQLAlchemyError):
    """A database failure injected at the flush boundary, so a half-applied revoke is observable."""


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _my_users() -> Select[Any]:
    return select(User.id).where(User.telegram_user_id.in_(TELEGRAM_IDS))


def _my_products() -> Select[Any]:
    return select(Product.id).where(Product.created_by_user_id.in_(_my_users()))


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
        my_files = set(
            (
                await session.execute(
                    select(IngestionSessionItem.file_object_id).where(
                        IngestionSessionItem.sender_user_id.in_(_my_users())
                    )
                )
            )
            .scalars()
            .all()
        )
        await session.execute(
            delete(PendingProductImageConfirmation).where(
                PendingProductImageConfirmation.product_id.in_(_my_products())
            )
        )
        await session.execute(
            delete(ProductImageRelation).where(ProductImageRelation.product_id.in_(_my_products()))
        )
        if my_files:
            await session.execute(
                delete(ImageAssetOccurrence).where(
                    ImageAssetOccurrence.source_file_id.in_(my_files)
                )
            )
        await session.execute(
            delete(ImageAsset).where(
                ImageAsset.content_sha256.in_([_digest(PICTURE), _digest(SECOND_PICTURE)])
            )
        )
        await session.execute(delete(Product).where(Product.id.in_(_my_products())))
        await session.execute(
            delete(SourceReference).where(SourceReference.actor_user_id.in_(_my_users()))
        )
        await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(_my_users())))
        await session.execute(
            delete(IngestionSessionItem).where(IngestionSessionItem.sender_user_id.in_(_my_users()))
        )
        if my_files:
            await session.execute(delete(FileObject).where(FileObject.id.in_(my_files)))
        # /saxla queues the processing job too, and that job restricts the session.
        await session.execute(
            delete(IngestionJob).where(
                IngestionJob.session_id.in_(
                    select(IngestionSession.id).where(IngestionSession.user_id.in_(_my_users()))
                )
            )
        )
        await session.execute(
            delete(IngestionSession).where(IngestionSession.user_id.in_(_my_users()))
        )
        await session.execute(delete(User).where(User.telegram_user_id.in_(TELEGRAM_IDS)))


async def _actors(factory: async_sessionmaker[AsyncSession]) -> tuple[int, int, int]:
    """(confirmer, administrator, ordinary member) — a revoke has three roles to tell apart."""

    async with factory.begin() as session:
        ids: dict[int, int] = {}
        for telegram_user_id, role in (
            (CONFIRMER_TELEGRAM_ID, UserRole.USER),
            (ADMIN_TELEGRAM_ID, UserRole.ADMIN),
            (MEMBER_TELEGRAM_ID, UserRole.USER),
        ):
            user = (
                await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
            ).scalar_one_or_none()
            if user is None:
                user = User(telegram_user_id=telegram_user_id, role=role, status=UserStatus.ACTIVE)
                session.add(user)
                await session.flush()
            ids[telegram_user_id] = int(user.id)
        return ids[CONFIRMER_TELEGRAM_ID], ids[ADMIN_TELEGRAM_ID], ids[MEMBER_TELEGRAM_ID]


async def _actor(factory: async_sessionmaker[AsyncSession], telegram_user_id: int) -> User:
    async with factory() as session:
        return (
            await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        ).scalar_one()


async def _save(
    factory: async_sessionmaker[AsyncSession], storage: LocalFileStorage
) -> SessionSummary:
    """Run the real /saxla boundary, the way the command handler does."""

    summary = await IngestionSessionService(factory, storage).commit(
        await _actor(factory, CONFIRMER_TELEGRAM_ID)
    )
    assert summary is not None
    return summary


async def _seed_upload(
    factory: async_sessionmaker[AsyncSession],
    storage: LocalFileStorage,
    *,
    user_id: int,
    data: bytes,
    message_id: int = MESSAGE_ID,
) -> int:
    """Stage one picture exactly as the upload turn does: an open session and unstored bytes."""

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
        return int(file_object.id)


async def _ensure_product(
    factory: async_sessionmaker[AsyncSession], *, user_id: int, code: str
) -> UUID:
    """A product that already exists: §23.7 forbids creating one from a caption or a revoke."""

    async with factory.begin() as session:
        existing = (
            await session.execute(select(Product.id).where(Product.canonical_code == code))
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        source = SourceReference(
            source_type=SourceType.MANUAL_ADMIN,
            actor_user_id=user_id,
            description="Seeded by the Phase 12C revocation integration test",
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


async def _queue(
    service: ReferenceImageService,
    code: str,
    message_id: int = MESSAGE_ID,
    *,
    telegram_user_id: int = CONFIRMER_TELEGRAM_ID,
) -> ReferenceImageResult:
    return await service.record_product_picture(
        telegram_user_id=telegram_user_id,
        telegram_chat_id=CHAT_ID,
        telegram_message_id=message_id,
        canonical_code=code,
    )


async def _revoke(
    service: ReferenceImageService, code: str, *, telegram_user_id: int = ADMIN_TELEGRAM_ID
) -> ReferenceRevokeResult:
    return await service.revoke_product_picture(
        telegram_user_id=telegram_user_id, canonical_code=code
    )


async def _confirm_a_picture(
    factory: async_sessionmaker[AsyncSession],
    storage: LocalFileStorage,
    service: ReferenceImageService,
    *,
    user_id: int,
    code: str,
    data: bytes,
    message_id: int,
) -> tuple[UUID, int]:
    """Walk the real user path — product, upload, caption, /saxla — and name what it wrote."""

    product_id = await _ensure_product(factory, user_id=user_id, code=code)
    file_id = await _seed_upload(
        factory, storage, user_id=user_id, data=data, message_id=message_id
    )
    assert (await _queue(service, code, message_id)).status is ReferenceImageStatus.QUEUED
    await _save(factory, storage)
    return product_id, file_id


async def _rows(factory: async_sessionmaker[AsyncSession]) -> list[ProductImageRelation]:
    """This file's relations, oldest claim first — the uuid4 primary key would order by nothing."""

    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(ProductImageRelation)
                    .where(ProductImageRelation.product_id.in_(_my_products()))
                    .order_by(ProductImageRelation.created_at, ProductImageRelation.id)
                )
            ).scalars()
        )


async def _audits(factory: async_sessionmaker[AsyncSession]) -> list[AuditEvent]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(AuditEvent)
                    .where(
                        AuditEvent.action.in_(["PRODUCT_IMAGE_CONFIRMED", "PRODUCT_IMAGE_REVOKED"]),
                        AuditEvent.actor_user_id.in_(_my_users()),
                    )
                    .order_by(AuditEvent.id)
                )
            ).scalars()
        )


async def _counts(factory: async_sessionmaker[AsyncSession]) -> tuple[int, int, int, int]:
    """(products, relations, assets, provenance) — a silent delete would show up here."""

    async with factory() as session:
        products = (
            await session.execute(
                select(func.count()).select_from(Product).where(Product.id.in_(_my_products()))
            )
        ).scalar_one()
        relations = (
            await session.execute(
                select(func.count())
                .select_from(ProductImageRelation)
                .where(ProductImageRelation.product_id.in_(_my_products()))
            )
        ).scalar_one()
        assets = (
            await session.execute(
                select(func.count())
                .select_from(ImageAsset)
                .where(ImageAsset.content_sha256.in_([_digest(PICTURE), _digest(SECOND_PICTURE)]))
            )
        ).scalar_one()
        sources = (
            await session.execute(
                select(func.count())
                .select_from(SourceReference)
                .where(SourceReference.actor_user_id.in_(_my_users()))
            )
        ).scalar_one()
        return int(products), int(relations), int(assets), int(sources)


def _scenario(
    behavior: Callable[
        [async_sessionmaker[AsyncSession], LocalFileStorage, ReferenceImageService],
        Awaitable[None],
    ],
) -> None:
    async def run() -> None:
        settings = _settings_or_skip()
        root = Path(settings.file_storage_path) / "phase12c-revoke-test"
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        try:
            shutil.rmtree(root, ignore_errors=True)
            storage = LocalFileStorage(root)
            await _clean(factory)
            await behavior(factory, storage, ReferenceImageService(factory, storage))
        finally:
            await _clean(factory)
            await engine.dispose()
            shutil.rmtree(root, ignore_errors=True)

    asyncio.run(run())


@contextmanager
def _fail_on_relation_write() -> Generator[None, None, None]:
    """Make the flush that would carry the revoke fail, the way a real database error does."""

    def before_flush(session: SyncSession, *_: object) -> None:
        if any(isinstance(item, ProductImageRelation) for item in session.dirty):
            raise _WriteFailure("revocation write failed")

    event.listen(SyncSession, "before_flush", before_flush)
    try:
        yield
    finally:
        event.remove(SyncSession, "before_flush", before_flush)


def test_revoking_retires_the_claim_and_keeps_everything_else() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id, admin_id, _ = await _actors(factory)
        _, file_id = await _confirm_a_picture(
            factory,
            storage,
            service,
            user_id=user_id,
            code="RV45",
            data=PICTURE,
            message_id=MESSAGE_ID,
        )
        before = await _counts(factory)
        relation = (await _rows(factory))[0]
        assert relation.superseded_at is None

        result = await _revoke(service, "RV45")

        assert result.status is ReferenceRevokeStatus.REVOKED
        assert result.display_code == "RV45"
        assert result.revoked_count == 1
        rows = await _rows(factory)
        assert rows[0].id == relation.id
        assert rows[0].superseded_at is not None
        # Nothing is deleted: the decision has to stay provable, and the bytes may be shared.
        assert await _counts(factory) == before
        async with factory() as session:
            asset = await session.get(ImageAsset, relation.image_asset_id)
            assert asset is not None
            blob = await session.get(FileObject, file_id)
            assert blob is not None
            # The inviolable original stays in ``blobs/`` and the internal resource in
            # ``image_assets/`` (Q27, §23.6): a revocation is not a deletion.
            assert blob.state == FileObjectState.ACTIVE
            assert blob.storage_key.startswith("blobs/")
            assert asset.storage_key.startswith("image_assets/")
            assert storage.path_for_key(blob.storage_key).read_bytes() == PICTURE
            assert storage.path_for_key(asset.storage_key).read_bytes() == PICTURE
            source = await session.get(SourceReference, relation.source_reference_id)
            assert source is not None
            assert source.source_type == SourceType.TELEGRAM_USER
            assert source.locator == {"telegram_message_id": MESSAGE_ID}
        audits = await _audits(factory)
        assert [audit.action for audit in audits] == [
            "PRODUCT_IMAGE_CONFIRMED",
            "PRODUCT_IMAGE_REVOKED",
        ]
        revoked = audits[1]
        assert revoked.actor_user_id == admin_id
        assert revoked.entity_type == "product_image_relation"
        assert revoked.entity_id == str(relation.id)
        assert revoked.event_metadata == {
            "product_id": str(relation.product_id),
            "image_asset_id": str(relation.image_asset_id),
            "code": "RV45",
        }

    _scenario(behavior)


def test_a_repeated_revocation_writes_nothing() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id, _, _ = await _actors(factory)
        await _confirm_a_picture(
            factory,
            storage,
            service,
            user_id=user_id,
            code="RV45",
            data=PICTURE,
            message_id=MESSAGE_ID,
        )
        assert (await _revoke(service, "RV45")).status is ReferenceRevokeStatus.REVOKED
        retired = (await _rows(factory))[0].superseded_at
        before = await _counts(factory)

        again = await _revoke(service, "RV45")

        # Asking again finds nothing live, so it must not rewrite history or add a second decision.
        assert again.status is ReferenceRevokeStatus.NOTHING_TO_REVOKE
        assert again.revoked_count == 0
        assert again.display_code == "RV45"
        assert [row.superseded_at for row in await _rows(factory)] == [retired]
        assert await _counts(factory) == before
        assert [audit.action for audit in await _audits(factory)] == [
            "PRODUCT_IMAGE_CONFIRMED",
            "PRODUCT_IMAGE_REVOKED",
        ]

    _scenario(behavior)


def test_an_ordinary_member_cannot_revoke() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id, _, member_id = await _actors(factory)
        await _confirm_a_picture(
            factory,
            storage,
            service,
            user_id=user_id,
            code="RV45",
            data=PICTURE,
            message_id=MESSAGE_ID,
        )
        assert (await _actor(factory, MEMBER_TELEGRAM_ID)).id == member_id

        with pytest.raises(AuthorizationDenied):
            await _revoke(service, "RV45", telegram_user_id=MEMBER_TELEGRAM_ID)

        # Refused means untouched, including for the member's own earlier confirmation.
        assert [(row.superseded_at, row.confirmation_type) for row in await _rows(factory)] == [
            (None, str(ProductImageRelationConfirmation.USER_EXPLICIT))
        ]
        assert [audit.action for audit in await _audits(factory)] == ["PRODUCT_IMAGE_CONFIRMED"]

    _scenario(behavior)


def test_revoking_an_unknown_product_writes_nothing() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id, _, _ = await _actors(factory)
        await _confirm_a_picture(
            factory,
            storage,
            service,
            user_id=user_id,
            code="RV45",
            data=PICTURE,
            message_id=MESSAGE_ID,
        )
        before = await _counts(factory)

        result = await _revoke(service, "ZZ99")

        # A code that names nothing retires nothing, and never creates the product to answer it.
        assert result.status is ReferenceRevokeStatus.PRODUCT_NOT_FOUND
        assert result.revoked_count == 0
        assert await _counts(factory) == before
        assert [row.superseded_at for row in await _rows(factory)] == [None]

    _scenario(behavior)


def test_a_product_with_no_live_picture_is_answered_not_an_error() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id, _, _ = await _actors(factory)
        await _ensure_product(factory, user_id=user_id, code="RV45")
        before = await _counts(factory)

        result = await _revoke(service, "RV45")

        assert result.status is ReferenceRevokeStatus.NOTHING_TO_REVOKE
        assert result.display_code == "RV45"
        assert await _counts(factory) == before
        assert await _audits(factory) == []

    _scenario(behavior)


def test_a_revoked_picture_answers_the_active_reads_as_free() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id, _, _ = await _actors(factory)
        retired, _ = await _confirm_a_picture(
            factory,
            storage,
            service,
            user_id=user_id,
            code="RV45",
            data=PICTURE,
            message_id=MESSAGE_ID,
        )
        other = await _ensure_product(factory, user_id=user_id, code="RV46")
        assert (await _revoke(service, "RV45")).status is ReferenceRevokeStatus.REVOKED

        async with factory() as session:
            holder = await ProductImageRelationRepository(
                session
            ).active_confirmation_holder_by_content(_digest(PICTURE))
        # §22.7 guards a live claim; a retired one stops being the claim that blocks the next.
        assert holder is None

        await _seed_upload(
            factory, storage, user_id=user_id, data=PICTURE, message_id=OTHER_MESSAGE_ID
        )
        assert (await _queue(service, "RV46", OTHER_MESSAGE_ID)).status is (
            ReferenceImageStatus.QUEUED
        )
        summary = await _save(factory, storage)

        assert summary.reference_images_linked == 1
        assert [
            (row.product_id, row.superseded_at is not None) for row in await _rows(factory)
        ] == [
            (retired, True),
            (other, False),
        ]

    _scenario(behavior)


def test_every_picture_of_the_product_retires_together() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id, _, _ = await _actors(factory)
        for message_id, data in ((MESSAGE_ID, PICTURE), (OTHER_MESSAGE_ID, SECOND_PICTURE)):
            await _confirm_a_picture(
                factory,
                storage,
                service,
                user_id=user_id,
                code="RV45",
                data=data,
                message_id=message_id,
            )

        result = await _revoke(service, "RV45")

        # §22.4 lets one product hold several pictures, so a revoke names the product and takes them
        # all; leaving one live while the answer says the confirmation was dropped would be a lie.
        assert result.status is ReferenceRevokeStatus.REVOKED
        assert result.revoked_count == 2
        assert [(row.superseded_at is not None, row.role) for row in await _rows(factory)] == [
            (True, str(ProductImageRelationRole.USER_CONFIRMED)),
            (True, str(ProductImageRelationRole.USER_CONFIRMED)),
        ]

    _scenario(behavior)


def test_only_the_named_product_is_touched() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id, _, _ = await _actors(factory)
        first, _ = await _confirm_a_picture(
            factory,
            storage,
            service,
            user_id=user_id,
            code="RV45",
            data=PICTURE,
            message_id=MESSAGE_ID,
        )
        second, _ = await _confirm_a_picture(
            factory,
            storage,
            service,
            user_id=user_id,
            code="RV46",
            data=SECOND_PICTURE,
            message_id=OTHER_MESSAGE_ID,
        )

        result = await _revoke(service, "RV45")

        # A revocation names one product: no silent reassignment, and no collateral retirement.
        assert result.revoked_count == 1
        assert [
            (row.product_id, row.superseded_at is not None) for row in await _rows(factory)
        ] == [
            (first, True),
            (second, False),
        ]
        assert [audit.action for audit in await _audits(factory)] == [
            "PRODUCT_IMAGE_CONFIRMED",
            "PRODUCT_IMAGE_CONFIRMED",
            "PRODUCT_IMAGE_REVOKED",
        ]

    _scenario(behavior)


def test_two_administrators_who_confirm_at_once_revoke_once() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id, _, _ = await _actors(factory)
        await _confirm_a_picture(
            factory,
            storage,
            service,
            user_id=user_id,
            code="RV45",
            data=PICTURE,
            message_id=MESSAGE_ID,
        )

        first, second = await asyncio.gather(_revoke(service, "RV45"), _revoke(service, "RV45"))

        assert sorted([str(first.status), str(second.status)]) == sorted(
            [str(ReferenceRevokeStatus.REVOKED), str(ReferenceRevokeStatus.NOTHING_TO_REVOKE)]
        )
        assert [row.superseded_at is not None for row in await _rows(factory)] == [True]
        assert [audit.action for audit in await _audits(factory)] == [
            "PRODUCT_IMAGE_CONFIRMED",
            "PRODUCT_IMAGE_REVOKED",
        ]

    _scenario(behavior)


def test_a_failed_write_leaves_the_confirmation_in_place() -> None:
    async def behavior(
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        service: ReferenceImageService,
    ) -> None:
        user_id, _, _ = await _actors(factory)
        await _confirm_a_picture(
            factory,
            storage,
            service,
            user_id=user_id,
            code="RV45",
            data=PICTURE,
            message_id=MESSAGE_ID,
        )
        before = await _counts(factory)

        with _fail_on_relation_write(), pytest.raises(_WriteFailure):
            await _revoke(service, "RV45")

        # Half a revocation is worse than none: the claim and its audit row both stay as they were.
        assert [row.superseded_at for row in await _rows(factory)] == [None]
        assert [audit.action for audit in await _audits(factory)] == ["PRODUCT_IMAGE_CONFIRMED"]
        assert await _counts(factory) == before

    _scenario(behavior)
