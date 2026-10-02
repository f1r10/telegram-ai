"""Repository for Telegram application users."""

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.infrastructure.database.models import User, UserRole


class UserRepository:
    """Explicit user queries; transaction ownership stays with the caller."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_telegram_id(
        self, telegram_user_id: int, *, for_update: bool = False
    ) -> User | None:
        statement: Select[tuple[User]] = select(User).where(
            User.telegram_user_id == telegram_user_id
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_by_id(self, user_id: int, *, for_update: bool = False) -> User | None:
        statement: Select[tuple[User]] = select(User).where(User.id == user_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_users(self, *, offset: int = 0, limit: int = 50) -> list[User]:
        statement = select(User).order_by(User.id).offset(offset).limit(limit)
        return list((await self.session.execute(statement)).scalars().all())

    async def list_admins(self) -> list[User]:
        statement = select(User).where(User.role == UserRole.ADMIN, User.status == "ACTIVE")
        return list((await self.session.execute(statement)).scalars().all())

    async def count_admins(self) -> int:
        statement = select(User.id).where(User.role == UserRole.ADMIN, User.status == "ACTIVE")
        return len((await self.session.execute(statement)).scalars().all())
