"""Shared authorization and validation for business services."""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.application.errors import AuthorizationDenied, InvalidBusinessValue
from office_assistant.auth.policy import is_active, is_admin
from office_assistant.business.types import ProvenanceInput
from office_assistant.infrastructure.database.models import SourceType, User
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
