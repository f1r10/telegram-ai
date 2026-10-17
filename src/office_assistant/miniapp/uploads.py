"""Short-lived, explicitly confirmed Mini App uploads using the existing ingestion ledger.

The first HTTP transfer writes only a private temporary staging blob, NOT a
business file or indexing job. The second explicit action uses the existing
IngestionSessionService. This workflow is intentionally one file at a time.
"""

import secrets
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from office_assistant.application.errors import InvalidStateTransition
from office_assistant.application.file_validation import validate_upload
from office_assistant.application.ingestion import IngestionSessionService, UploadMetadata
from office_assistant.infrastructure.database.models import User
from office_assistant.infrastructure.storage.hashing import sha256_file
from office_assistant.infrastructure.storage.local import LocalFileStorage


class MiniAppUploadConflict(RuntimeError):
    """A bot upload session or another pending upload must be resolved first."""


class MiniAppUploadExpired(RuntimeError):
    """The preview is no longer an eligible confirmation target."""


@dataclass(frozen=True, slots=True)
class UploadPreview:
    actor_id: int
    key: str
    filename: str
    mime: str
    size: int
    sha256: str
    expires_at: float


class MiniAppUploadWorkflow:
    """No browser-controlled path, no automatic business writes, no implicit commit."""

    MAX_BYTES = 8 * 1024 * 1024
    TTL_SECONDS = 600

    def __init__(self, storage: LocalFileStorage, ingestion: IngestionSessionService) -> None:
        self.storage = storage
        self.ingestion = ingestion
        self._previews: dict[str, UploadPreview] = {}

    @staticmethod
    def validated_name(name: str, mime: str, size: int) -> tuple[str, str]:
        if (
            not 1 <= len(name) <= 160
            or Path(name).name != name
            or "/" in name
            or "\\" in name
            or any(ord(char) < 32 for char in name)
            or name.startswith(".")
            or name in {".", ".."}
            or not 1 <= size <= MiniAppUploadWorkflow.MAX_BYTES
        ):
            raise ValueError("Invalid or oversized file")
        normalized = validate_upload(name, mime, size, MiniAppUploadWorkflow.MAX_BYTES)
        return name, normalized

    def _purge_expired(self) -> None:
        for token, preview in tuple(self._previews.items()):
            if preview.expires_at <= monotonic():
                self._previews.pop(token, None)
                self.storage.delete(preview.key)

    async def permission(self, actor: User) -> None:
        self._purge_expired()
        if await self.ingestion.has_open_session(actor):
            raise MiniAppUploadConflict(
                "Telegram botunda açıq sənəd sessiyası var. Əvvəl /saxla və ya /cancel edin."
            )
        if any(preview.actor_id == actor.id for preview in self._previews.values()):
            raise MiniAppUploadConflict("Əvvəlki faylı təsdiqləyin və ya ləğv edin.")

    async def register(
        self,
        actor: User,
        *,
        key: str,
        filename: str,
        mime: str,
        size: int,
    ) -> dict[str, object]:
        """Called only after bounded HTTP streaming into a server-generated key."""

        try:
            clean_name, normalized = self.validated_name(filename, mime, size)
            await self.permission(actor)
            path = self.storage.path_for_key(key)
            if (
                not key.startswith("staged/")
                or path.parent != self.storage.staged_root
                or not path.is_file()
                or self.storage.size(path) != size
            ):
                raise ValueError("Uploaded bytes are incomplete or outside temporary storage")
            sha256, actual_size = sha256_file(path)
            if actual_size != size:
                raise ValueError("Uploaded file changed during preparation")
            token = secrets.token_urlsafe(24)
            self._previews[token] = UploadPreview(
                actor_id=actor.id,
                key=key,
                filename=clean_name,
                mime=normalized,
                size=size,
                sha256=sha256,
                expires_at=monotonic() + self.TTL_SECONDS,
            )
            return {
                "token": token,
                "filename": clean_name,
                "size": size,
                "status": "preview",
                "notice": "Fayl hələ bazaya əlavə edilməyib. Təsdiq edin və ya ləğv edin.",
            }
        except Exception:
            self.storage.delete(key)
            raise

    def _eligible(self, actor: User, token: str) -> UploadPreview:
        if not 20 <= len(token) <= 64:
            raise MiniAppUploadExpired("Invalid upload token")
        preview = self._previews.get(token)
        if (
            preview is None
            or preview.actor_id != actor.id
            or preview.expires_at < monotonic()
        ):
            raise MiniAppUploadExpired("Preview expired or belongs to another account")
        return preview

    async def cancel(self, actor: User, token: str) -> dict[str, object]:
        preview = self._eligible(actor, token)
        self._previews.pop(token, None)
        self.storage.delete(preview.key)
        return {"status": "cancelled", "notice": "Fayl saxlanılmadı."}

    async def confirm(self, actor: User, token: str) -> dict[str, object]:
        """Explicit approval; stage exactly one file, then commit only that session."""

        preview = self._eligible(actor, token)
        if await self.ingestion.has_open_session(actor):
            raise MiniAppUploadConflict(
                "Başqa açıq sənəd sessiyası var. Əvvəl onu Telegram botunda tamamlayın."
            )
        path = self.storage.path_for_key(preview.key)
        if not path.is_file() or sha256_file(path) != (preview.sha256, preview.size):
            self._previews.pop(token, None)
            self.storage.delete(preview.key)
            raise MiniAppUploadExpired("Prepared file has changed")
        self._previews.pop(token, None)
        # Reserved, non-Telegram chat namespace prevents collisions with real messages.
        message_id = secrets.randbelow(2**62 - 1) + 1
        result = await self.ingestion.add_file(
            actor,
            UploadMetadata(
                original_filename=preview.filename,
                content_type=preview.mime,
                staging_path=path,
                staging_key=preview.key,
                telegram_chat_id=-(5_000_000_000_000_000_000 + actor.telegram_user_id),
                telegram_message_id=message_id,
                telegram_update_id=None,
                caption=None,
            ),
        )
        if result.summary.file_count != 1 or result.summary.instruction_count != 0:
            raise MiniAppUploadConflict(
                "Açıq sessiya dəyişdi. Fayl növbədədir; botda /saxla və ya /cancel edin."
            )
        # The ingestion service validates expected contents INSIDE the locked
        # commit transaction, so a concurrent Telegram upload cannot be auto-saved.
        summary = await self.ingestion.commit(
            actor,
            expected_session_id=result.summary.session_id,
            expected_file_count=1,
            expected_instruction_count=0,
        )
        if summary is None:
            raise InvalidStateTransition("Prepared session disappeared before commit")
        return {
            "status": "queued",
            "session_id": summary.session_id,
            "file_count": summary.file_count,
            "job_id": summary.job_id,
            "notice": "Fayl təsdiqləndi və emal növbəsinə verildi. Emal bitməsi hələ təsdiqlənməyib.",
        }
