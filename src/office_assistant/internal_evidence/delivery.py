"""Authorized resolution of recent citations to original committed blobs."""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.errors import AuthorizationDenied
from office_assistant.infrastructure.database.models import (
    AIConversation,
    AIMessage,
    AIMessageSource,
    FileObject,
    FileObjectState,
    ImageAssetOccurrence,
    ImageAssetOrigin,
    User,
    UserStatus,
)
from office_assistant.infrastructure.storage.local import LocalFileStorage, StorageError
from office_assistant.internal_evidence.query_normalization import normalize_user_query

logger = logging.getLogger(__name__)


class FileDeliveryError(RuntimeError):
    pass


class FileDeliveryAmbiguous(FileDeliveryError):
    pass


@dataclass(frozen=True, slots=True)
class DeliverableFile:
    path: Path
    filename: str
    content_type: str
    file_id: int


@dataclass(frozen=True, slots=True)
class ExternalSourceLink:
    url: str
    title: str
    citation_index: int


class SourceFileDeliveryService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        *,
        max_bytes: int,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.max_bytes = max_bytes

    async def resolve_recent(
        self,
        actor_telegram_user_id: int,
        citation_index: int | None,
        *,
        expected_file_id: int | None = None,
    ) -> DeliverableFile:
        """Return an ACTIVE original, optionally pinned to a server-issued source ticket.

        Telegram's unpinned source command intentionally resolves the most recent
        answer. A Mini App button pins both citation number and file ID: a newer
        Telegram or Mini App answer must not silently retarget that button.
        """
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            actor = await session.scalar(
                select(User).where(User.telegram_user_id == actor_telegram_user_id)
            )
            if actor is None or actor.status != UserStatus.ACTIVE:
                raise AuthorizationDenied("File delivery requires an ACTIVE actor")
            if expected_file_id is not None:
                if expected_file_id <= 0 or citation_index is None:
                    raise FileDeliveryError("Invalid pinned source citation")
                # The ticket is application-issued and actor-bound, but recheck its
                # evidence lineage in PostgreSQL on every download. Do not trust
                # either a prior authorization or a now-revoked original file.
                pinned_id = await session.scalar(
                    select(AIMessageSource.file_id)
                    .join(AIMessage, AIMessage.id == AIMessageSource.message_id)
                    .join(AIConversation, AIConversation.id == AIMessage.conversation_id)
                    .where(
                        AIConversation.user_id == actor.id,
                        AIConversation.mode.in_(("internal", "combined")),
                        AIMessage.role == "assistant",
                        AIMessageSource.expires_at > now,
                        AIMessageSource.file_id == expected_file_id,
                        AIMessageSource.citation_index == citation_index,
                    )
                    .limit(1)
                )
                if pinned_id != expected_file_id:
                    raise FileDeliveryError("Pinned source is expired or is not owned by this user")
                file_id = expected_file_id
            else:
                latest_message_id = await session.scalar(
                    select(AIMessageSource.message_id)
                    .join(AIMessage, AIMessage.id == AIMessageSource.message_id)
                    .join(AIConversation, AIConversation.id == AIMessage.conversation_id)
                    .where(
                        AIConversation.user_id == actor.id,
                        AIConversation.mode.in_(("internal", "combined")),
                        AIMessageSource.expires_at > now,
                        AIMessageSource.file_id.is_not(None),
                    )
                    .order_by(AIMessage.created_at.desc(), AIMessage.id.desc())
                    .limit(1)
                )
                if latest_message_id is None:
                    raise FileDeliveryError("No recent deliverable source exists")
                statement = select(AIMessageSource).where(
                    AIMessageSource.message_id == latest_message_id,
                    AIMessageSource.expires_at > now,
                    AIMessageSource.file_id.is_not(None),
                )
                if citation_index is not None:
                    statement = statement.where(AIMessageSource.citation_index == citation_index)
                rows = list((await session.execute(statement)).scalars().all())
                unique = {row.file_id: row for row in rows if row.file_id is not None}
                if not unique:
                    raise FileDeliveryError("Requested source has no original file")
                if len(unique) != 1:
                    raise FileDeliveryAmbiguous("Several recent files match; specify source number")
                file_id = next(iter(unique))
            file = await session.get(FileObject, file_id)
            if file is None or file.state != FileObjectState.ACTIVE:
                raise FileDeliveryError("Original file is not active")
            storage_key = file.storage_key
            filename = _safe_filename(file.original_filename)
            content_type = file.content_type
        try:
            path = self.storage.path_for_key(storage_key)
        except StorageError as error:
            logger.error("Committed source key is invalid | file_id=%s", file_id)
            raise FileDeliveryError("Original file path is invalid") from error
        try:
            path.relative_to(self.storage.blob_root)
        except ValueError as error:
            logger.error("Committed source path is outside blob storage | file_id=%s", file_id)
            raise FileDeliveryError("Original file path is invalid") from error
        if not path.is_file():
            logger.error("Committed source blob is missing | file_id=%s", file_id)
            raise FileDeliveryError("Original file blob is missing")
        if self.storage.size(path) > self.max_bytes:
            raise FileDeliveryError("Original file exceeds the delivery limit")
        return DeliverableFile(path, filename, content_type, file_id)

    async def resolve_catalog_original(
        self,
        actor_telegram_user_id: int,
        *,
        file_id: int,
        occurrence_id: int,
    ) -> DeliverableFile:
        """Deliver only an ACTIVE parent PDF of the exact matched image occurrence.

        Caller must provide a short-lived actor-bound token (not public file IDs).
        The database source lineage and original blob location are rechecked here
        on EVERY request, including repeated button presses.
        """

        if file_id <= 0 or occurrence_id <= 0:
            raise FileDeliveryError("Invalid catalog source reference")
        async with self.session_factory() as session:
            actor = await session.scalar(
                select(User).where(User.telegram_user_id == actor_telegram_user_id)
            )
            if actor is None or actor.status != UserStatus.ACTIVE:
                raise AuthorizationDenied("Catalog source requires an ACTIVE actor")
            original = await session.scalar(
                select(FileObject)
                .join(
                    ImageAssetOccurrence,
                    ImageAssetOccurrence.source_file_id == FileObject.id,
                )
                .where(
                    FileObject.id == file_id,
                    FileObject.state == FileObjectState.ACTIVE,
                    ImageAssetOccurrence.id == occurrence_id,
                    ImageAssetOccurrence.origin_type == ImageAssetOrigin.PDF_EMBEDDED,
                )
            )
            if original is None:
                raise FileDeliveryError("Matched PDF image source is no longer available")
            filename = _safe_filename(original.original_filename)
            content_type = original.content_type
            storage_key = original.storage_key
        if not filename.lower().endswith(".pdf"):
            raise FileDeliveryError("Catalog image parent must be a PDF")
        try:
            path = self.storage.path_for_key(storage_key)
            path.relative_to(self.storage.blob_root)
        except (StorageError, ValueError) as error:
            raise FileDeliveryError("Catalog source path is invalid") from error
        if not path.is_file():
            raise FileDeliveryError("Original PDF blob is missing")
        if self.storage.size(path) > self.max_bytes:
            raise FileDeliveryError("Original PDF exceeds delivery limit")
        return DeliverableFile(path, filename, content_type, file_id)

    async def resolve_recent_url(
        self, actor_telegram_user_id: int, citation_index: int | None
    ) -> ExternalSourceLink:
        async with self.session_factory() as session:
            actor = await session.scalar(
                select(User).where(User.telegram_user_id == actor_telegram_user_id)
            )
            if actor is None or actor.status != UserStatus.ACTIVE:
                raise AuthorizationDenied("Web link delivery requires an ACTIVE actor")
            latest_message_id = await session.scalar(
                select(AIMessageSource.message_id)
                .join(AIMessage, AIMessage.id == AIMessageSource.message_id)
                .join(AIConversation, AIConversation.id == AIMessage.conversation_id)
                .where(
                    AIConversation.user_id == actor.id,
                    AIConversation.mode.in_(("internet", "combined")),
                    AIMessageSource.expires_at > datetime.now(UTC),
                    AIMessageSource.source_type == "EXTERNAL_WEB",
                    AIMessageSource.external_url.is_not(None),
                )
                .order_by(AIMessage.created_at.desc(), AIMessage.id.desc())
                .limit(1)
            )
            if latest_message_id is None:
                raise FileDeliveryError("No recent external source exists")
            statement = select(AIMessageSource).where(
                AIMessageSource.message_id == latest_message_id,
                AIMessageSource.expires_at > datetime.now(UTC),
                AIMessageSource.source_type == "EXTERNAL_WEB",
                AIMessageSource.external_url.is_not(None),
            )
            if citation_index is not None:
                statement = statement.where(AIMessageSource.citation_index == citation_index)
            rows = list((await session.execute(statement)).scalars().all())
            if not rows:
                raise FileDeliveryError("Requested external source is unavailable")
            if len(rows) != 1:
                raise FileDeliveryAmbiguous("Several web sources match; specify source number")
            row = rows[0]
            assert row.external_url is not None
            return ExternalSourceLink(
                row.external_url,
                row.external_title or row.external_url,
                row.citation_index,
            )


def parse_file_delivery_request(text: str) -> int | None | bool:
    folded = normalize_user_query(text)
    # "ver" is a tolerated colloquial delivery verb (Master Spec §20), but the object
    # requirement below keeps "KA45-in qiymətini ver" on the evidence route.
    has_action = bool(re.search(r"\b(?:gonder\w*|at|ver\w*)\b", folded))
    has_object = bool(re.search(r"\b(?:menbe\w*|fayl\w*|pdf\w*|sened\w*|onu|bunu)\b", folded))
    intent = has_action and has_object
    if not intent:
        return False
    match = re.search(r"\b(\d{1,2})(?:ci)?\s*(?:menbe\w*|fayl\w*)", folded)
    if match:
        return int(match.group(1))
    word = re.search(r"\b(birinci|ikinci)\s+(?:menbe\w*|fayl\w*)", folded)
    return {"birinci": 1, "ikinci": 2}.get(word.group(1)) if word else None


def parse_web_source_request(text: str) -> int | None | bool:
    folded = normalize_user_query(text)
    has_web = bool(re.search(r"\b(?:internet\w*|veb\w*|link\w*|url\w*)\b", folded))
    has_action = bool(re.search(r"\b(?:goster\w*|gonder\w*|ac|ver)\b", folded))
    has_source = bool(re.search(r"\b(?:menbe\w*|link\w*|url\w*)\b", folded))
    if not ((has_web and has_action) or (has_action and has_source and "link" in folded)):
        return False
    match = re.search(r"\b(\d{1,2})(?:ci)?\s*(?:internet\w*\s*)?(?:menbe\w*|link\w*)", folded)
    if match:
        return int(match.group(1))
    word = re.search(r"\b(birinci|ikinci)\s+(?:internet\w*\s*)?(?:menbe\w*|link\w*)", folded)
    return {"birinci": 1, "ikinci": 2}.get(word.group(1)) if word else None


def _safe_filename(value: str) -> str:
    name = Path(value.replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        return "source-document.bin"
    return name[:255]
