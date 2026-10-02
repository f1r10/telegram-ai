"""Password authentication and race-safe admin bootstrap."""

from dataclasses import dataclass
from datetime import UTC, datetime
from secrets import compare_digest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.errors import AuthenticationConfigurationError
from office_assistant.infrastructure.database.models import (
    SystemMetadata,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.repositories.audit import AuditRepository
from office_assistant.repositories.users import UserRepository

BOOTSTRAP_KEY = "bootstrap.admin.initialized"


@dataclass(frozen=True, slots=True)
class TelegramIdentity:
    telegram_user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    user: User
    outcome: str
    admin_telegram_ids: tuple[int, ...] = ()


class AuthenticationService:
    """Owns access-password verification and durable bootstrap semantics."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        access_password: str | None,
    ) -> None:
        self.session_factory = session_factory
        self.access_password = access_password

    async def authenticate(
        self, identity: TelegramIdentity, supplied_password: str
    ) -> AuthenticationResult | None:
        if self.access_password is None:
            raise AuthenticationConfigurationError("Telegram access password is not configured")
        if not compare_digest(supplied_password, self.access_password):
            return None

        async with self.session_factory.begin() as session:
            users = UserRepository(session)
            audits = AuditRepository(session)
            bootstrap = await self._locked_bootstrap_row(session)
            existing = await users.get_by_telegram_id(identity.telegram_user_id, for_update=True)
            self._refresh_identity(existing, identity)

            if existing is not None:
                return AuthenticationResult(existing, existing.status.lower())

            now = datetime.now(UTC)
            if bootstrap.value != "true":
                user = User(
                    telegram_user_id=identity.telegram_user_id,
                    telegram_username=identity.username,
                    first_name=identity.first_name,
                    last_name=identity.last_name,
                    role=UserRole.ADMIN,
                    status=UserStatus.ACTIVE,
                    last_seen_at=now,
                )
                session.add(user)
                await session.flush()
                bootstrap.value = "true"
                audits.add(
                    actor_user_id=user.id,
                    action="ADMIN_BOOTSTRAPPED",
                    entity_type="user",
                    entity_id=str(user.id),
                )
                return AuthenticationResult(user, "admin_created")

            user = User(
                telegram_user_id=identity.telegram_user_id,
                telegram_username=identity.username,
                first_name=identity.first_name,
                last_name=identity.last_name,
                role=UserRole.USER,
                status=UserStatus.PENDING,
                last_seen_at=now,
            )
            session.add(user)
            await session.flush()
            audits.add(
                actor_user_id=None,
                action="ACCESS_REQUEST_CREATED",
                entity_type="user",
                entity_id=str(user.id),
            )
            admins = await users.list_admins()
            return AuthenticationResult(
                user,
                "pending",
                tuple(admin.telegram_user_id for admin in admins),
            )

    async def _locked_bootstrap_row(self, session: AsyncSession) -> SystemMetadata:
        statement = (
            select(SystemMetadata).where(SystemMetadata.key == BOOTSTRAP_KEY).with_for_update()
        )
        row = (await session.execute(statement)).scalar_one_or_none()
        if row is None:
            raise AuthenticationConfigurationError("Bootstrap migration is not applied")
        return row

    @staticmethod
    def _refresh_identity(user: User | None, identity: TelegramIdentity) -> None:
        if user is None:
            return
        user.telegram_username = identity.username
        user.first_name = identity.first_name
        user.last_name = identity.last_name
        if user.status == UserStatus.ACTIVE:
            user.last_seen_at = datetime.now(UTC)
