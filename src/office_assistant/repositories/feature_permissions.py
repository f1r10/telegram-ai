"""Explicit feature-grant queries; transaction ownership stays with the caller."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.infrastructure.database.models import UserFeaturePermission


class FeaturePermissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def grants_for_user(self, user_id: int) -> dict[str, bool]:
        statement = select(UserFeaturePermission.feature_key, UserFeaturePermission.enabled).where(
            UserFeaturePermission.user_id == user_id
        )
        rows = (await self.session.execute(statement)).tuples().all()
        return {key: bool(enabled) for key, enabled in rows}

    async def list_for_user(self, user_id: int) -> list[UserFeaturePermission]:
        statement = (
            select(UserFeaturePermission)
            .where(UserFeaturePermission.user_id == user_id)
            .order_by(UserFeaturePermission.feature_key)
        )
        return list((await self.session.execute(statement)).scalars().all())

    async def upsert(
        self,
        *,
        user_id: int,
        feature_key: str,
        enabled: bool,
        updated_by_user_id: int | None,
    ) -> None:
        """One atomic toggle; re-pressing the same button is a no-op, not an error."""

        statement = (
            insert(UserFeaturePermission)
            .values(
                user_id=user_id,
                feature_key=feature_key,
                enabled=enabled,
                updated_by_user_id=updated_by_user_id,
            )
            .on_conflict_do_update(
                constraint="pk_user_feature_permissions",
                set_={"enabled": enabled, "updated_by_user_id": updated_by_user_id},
            )
        )
        await self.session.execute(statement)

    async def insert_baseline(self, user_id: int, grants: dict[str, bool]) -> None:
        """Write the approval grid once; existing rows are never overwritten."""

        for feature_key, enabled in grants.items():
            statement = (
                insert(UserFeaturePermission)
                .values(user_id=user_id, feature_key=feature_key, enabled=enabled)
                .on_conflict_do_nothing(constraint="pk_user_feature_permissions")
            )
            await self.session.execute(statement)
