"""Administrator-managed per-user feature toggles (owner contract V2 §2).

Reads are cheap and happen on every turn; writes are admin-only, row-locked, audited
and idempotent. An administrator's own access is never editable, so no toggle can lock
the owner out of the system.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.business_common import require_feature
from office_assistant.application.errors import AuthorizationDenied, InvalidStateTransition
from office_assistant.auth.features import (
    BASELINE_ENABLED,
    FEATURE_KEYS,
    FeatureKey,
    allowed_features,
    can_use_feature,
    parse_feature,
)
from office_assistant.auth.policy import can_manage_users
from office_assistant.infrastructure.database.models import User, UserRole
from office_assistant.repositories.audit import AuditRepository
from office_assistant.repositories.feature_permissions import FeaturePermissionRepository
from office_assistant.repositories.users import UserRepository


@dataclass(frozen=True, slots=True)
class FeatureGrantView:
    feature_key: str
    enabled: bool
    effective: bool


class AdministratorFeatureLocked(InvalidStateTransition):
    """An administrator's access is never editable, so a toggle cannot lock them out."""


class UnknownFeatureTarget(InvalidStateTransition):
    """The requested user or feature key does not exist."""


@dataclass(frozen=True, slots=True)
class UserFeatureGrid:
    user: User
    editable: bool
    grants: tuple[FeatureGrantView, ...]


def _grid(target: User, stored: dict[str, bool]) -> UserFeatureGrid:
    views = tuple(
        FeatureGrantView(
            feature_key=key.value,
            enabled=bool(stored.get(key.value, key in BASELINE_ENABLED)),
            effective=can_use_feature(target, stored, key),
        )
        for key in FEATURE_KEYS
    )
    return UserFeatureGrid(user=target, editable=target.role != UserRole.ADMIN, grants=views)


class FeaturePermissionService:
    """The application boundary for feature access; Telegram only renders its answers."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def grants_for(self, user: User) -> dict[str, bool]:
        async with self.session_factory() as session:
            return await FeaturePermissionRepository(session).grants_for_user(user.id)

    async def allowed_for(self, user: User) -> tuple[FeatureKey, ...]:
        return allowed_features(user, await self.grants_for(user))

    async def require(self, telegram_user_id: int, feature: FeatureKey | str) -> User:
        """Deny a disabled capability even when the caller never opened a menu."""

        async with self.session_factory() as session:
            return await require_feature(session, telegram_user_id, feature)

    async def grid_for_actor(
        self, actor_telegram_user_id: int, target_user_id: int
    ) -> UserFeatureGrid:
        async with self.session_factory() as session:
            users = UserRepository(session)
            actor = await users.get_by_telegram_id(actor_telegram_user_id)
            if not can_manage_users(actor):
                raise AuthorizationDenied("Only an active administrator may view feature access")
            target = await users.get_by_id(target_user_id)
            if target is None:
                raise UnknownFeatureTarget("User does not exist")
            stored = await FeaturePermissionRepository(session).grants_for_user(target.id)
            return _grid(target, stored)

    async def set_for_actor(
        self,
        actor_telegram_user_id: int,
        target_user_id: int,
        feature: object,
        enabled: bool,
    ) -> UserFeatureGrid:
        key = parse_feature(feature)
        if key is None:
            raise UnknownFeatureTarget("Unknown feature key")
        async with self.session_factory.begin() as session:
            users = UserRepository(session)
            actor = await users.get_by_telegram_id(actor_telegram_user_id, for_update=True)
            if not can_manage_users(actor):
                raise AuthorizationDenied("Only an active administrator may change feature access")
            assert actor is not None
            target = await users.get_by_id(target_user_id, for_update=True)
            if target is None:
                raise UnknownFeatureTarget("User does not exist")
            if target.role == UserRole.ADMIN:
                raise AdministratorFeatureLocked("Administrator access cannot be toggled")
            repository = FeaturePermissionRepository(session)
            stored = await repository.grants_for_user(target.id)
            if bool(stored.get(key.value, key in BASELINE_ENABLED)) == bool(enabled):
                return _grid(target, stored)
            await repository.upsert(
                user_id=target.id,
                feature_key=key.value,
                enabled=bool(enabled),
                updated_by_user_id=actor.id,
            )
            AuditRepository(session).add(
                actor_user_id=actor.id,
                action="USER_FEATURE_ENABLED" if enabled else "USER_FEATURE_DISABLED",
                entity_type="user_feature",
                entity_id=f"{target.id}:{key.value}",
                metadata={
                    "target_user_id": target.id,
                    "feature_key": key.value,
                    "enabled": bool(enabled),
                },
            )
            updated = await repository.grants_for_user(target.id)
            return _grid(target, updated)
