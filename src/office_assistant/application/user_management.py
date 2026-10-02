"""Administrative user approval and status transitions."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.errors import AuthorizationDenied, InvalidStateTransition
from office_assistant.auth.policy import can_manage_users
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.repositories.audit import AuditRepository
from office_assistant.repositories.users import UserRepository


class UserManagementService:
    """Performs server-side checked, auditable user administration."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        async with self.session_factory() as session:
            return await UserRepository(session).get_by_telegram_id(telegram_user_id)

    async def touch_active_user(self, telegram_user_id: int) -> None:
        async with self.session_factory.begin() as session:
            user = await UserRepository(session).get_by_telegram_id(
                telegram_user_id, for_update=True
            )
            if user is not None and user.status == UserStatus.ACTIVE:
                user.last_seen_at = datetime.now(UTC)

    async def list_users_for_actor(
        self, actor_telegram_user_id: int, *, offset: int = 0, limit: int = 50
    ) -> list[User]:
        async with self.session_factory() as session:
            actor = await UserRepository(session).get_by_telegram_id(actor_telegram_user_id)
            if not can_manage_users(actor):
                raise AuthorizationDenied("Only an active administrator may list users")
            return await UserRepository(session).list_users(offset=offset, limit=limit)

    async def approve(self, actor_telegram_user_id: int, target_user_id: int) -> User:
        async with self.session_factory.begin() as session:
            users = UserRepository(session)
            actor = await users.get_by_telegram_id(actor_telegram_user_id, for_update=True)
            if not can_manage_users(actor):
                raise AuthorizationDenied("Only an active administrator may approve users")
            assert actor is not None
            target = await users.get_by_id(target_user_id, for_update=True)
            if target is None:
                raise InvalidStateTransition("User does not exist")
            if target.status == UserStatus.ACTIVE:
                return target
            if target.status != UserStatus.PENDING:
                raise InvalidStateTransition("Only pending users may be approved")
            target.role = UserRole.USER
            target.status = UserStatus.ACTIVE
            target.approved_at = datetime.now(UTC)
            target.approved_by_user_id = actor.id
            AuditRepository(session).add(
                actor_user_id=actor.id,
                action="USER_APPROVED",
                entity_type="user",
                entity_id=str(target.id),
            )
            return target

    async def reject(self, actor_telegram_user_id: int, target_user_id: int) -> User:
        async with self.session_factory.begin() as session:
            users = UserRepository(session)
            actor = await users.get_by_telegram_id(actor_telegram_user_id, for_update=True)
            if not can_manage_users(actor):
                raise AuthorizationDenied("Only an active administrator may reject users")
            assert actor is not None
            target = await users.get_by_id(target_user_id, for_update=True)
            if target is None:
                raise InvalidStateTransition("User does not exist")
            if target.status == UserStatus.REJECTED:
                return target
            if target.status != UserStatus.PENDING:
                raise InvalidStateTransition("Only pending users may be rejected")
            target.status = UserStatus.REJECTED
            AuditRepository(session).add(
                actor_user_id=actor.id,
                action="USER_REJECTED",
                entity_type="user",
                entity_id=str(target.id),
            )
            return target

    async def disable(self, actor_telegram_user_id: int, target_user_id: int) -> User:
        async with self.session_factory.begin() as session:
            users = UserRepository(session)
            actor = await users.get_by_telegram_id(actor_telegram_user_id, for_update=True)
            if not can_manage_users(actor):
                raise AuthorizationDenied("Only an active administrator may disable users")
            assert actor is not None
            target = await users.get_by_id(target_user_id, for_update=True)
            if target is None:
                raise InvalidStateTransition("User does not exist")
            if actor.id == target.id:
                raise InvalidStateTransition("An administrator cannot disable their own account")
            if target.status == UserStatus.DISABLED:
                return target
            if target.status != UserStatus.ACTIVE or target.role != UserRole.USER:
                raise InvalidStateTransition("Only active users may be disabled")
            target.status = UserStatus.DISABLED
            AuditRepository(session).add(
                actor_user_id=actor.id,
                action="USER_DISABLED",
                entity_type="user",
                entity_id=str(target.id),
            )
            return target
