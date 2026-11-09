"""Shared authorization and validation for business services."""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.application.errors import (
    AuthorizationDenied,
    FeatureAccessDenied,
    InvalidBusinessValue,
)
from office_assistant.auth.features import FeatureKey, can_use_feature, parse_feature
from office_assistant.auth.policy import is_active, is_admin
from office_assistant.business.types import ProvenanceInput
from office_assistant.infrastructure.database.models import SourceType, User
from office_assistant.repositories.feature_permissions import FeaturePermissionRepository
from office_assistant.repositories.users import UserRepository


async def require_actor(
    session: AsyncSession, telegram_user_id: int, *, admin: bool = False
) -> User:
    actor = await UserRepository(session).get_by_telegram_id(telegram_user_id, for_update=True)
    allowed = is_admin(actor) if admin else is_active(actor)
    if not allowed or actor is None:
        role = "active administrator" if admin else "active user"
        raise AuthorizationDenied(f"Operation requires an {role}")
    return actor


async def require_feature(
    session: AsyncSession, telegram_user_id: int, feature: FeatureKey | str
) -> User:
    """The one service-boundary feature check for callers holding only a Telegram id."""

    actor = await require_actor(session, telegram_user_id)
    await ensure_feature(session, actor, feature)
    return actor


async def ensure_feature(session: AsyncSession, actor: User, feature: FeatureKey | str) -> None:
    """Apply the shared capability policy to an already validated active actor.

    Services that re-read the actor row themselves call this so the decision stays in
    one place: an administrator is never locked out, a missing grant row falls back to
    the documented rollout baseline, and a disabled capability raises FeatureAccessDenied
    even when the caller bypassed the Telegram adapter.
    """

    key = parse_feature(feature)
    if key is None:
        raise AuthorizationDenied("Unknown feature key")
    if not is_active(actor):
        raise AuthorizationDenied("Operation requires an active user")
    if is_admin(actor):
        return
    grants = await FeaturePermissionRepository(session).grants_for_user(actor.id)
    if not can_use_feature(actor, grants, key):
        raise FeatureAccessDenied(key.value)


def validate_provenance(value: ProvenanceInput, actor: User) -> None:
    if value.source_type == SourceType.MANUAL_ADMIN and not is_admin(actor):
        raise AuthorizationDenied("MANUAL_ADMIN provenance requires an administrator")


def validate_decimal(value: Decimal, *, field: str, allow_zero: bool) -> None:
    if not value.is_finite():
        raise InvalidBusinessValue(f"{field} must be finite")
    if value < 0 or (not allow_zero and value == 0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise InvalidBusinessValue(f"{field} must be {qualifier}")
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -4:
        raise InvalidBusinessValue(f"{field} supports at most four decimal places")
