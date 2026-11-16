"""The one access decision every Phase-3 entity query goes through.

Invariant 5 of ``AGENTS.md``: every retrieval and every facts query applies the access filter of the
authenticated user, built by **one** shared function. For the entity registry that means an entity
type is visible only when the actor may use the capability that exposes it, and the decision reuses
the shipped policy (``auth.features.can_use_feature``) rather than inventing a second notion of
permission.

A user without the capability does not see a partial answer: the entity type is filtered out of the
query itself, so a mention of a hidden customer resolves to nothing instead of leaking its name.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from office_assistant.application.business_common import require_actor
from office_assistant.auth.features import FeatureKey, can_use_feature
from office_assistant.auth.policy import is_active, is_admin
from office_assistant.infrastructure.database.models import Entity, EntityType, User
from office_assistant.repositories.feature_permissions import FeaturePermissionRepository

#: Which capability an actor needs before an entity of each type is visible at all. A product is
#: the object of product search; a customer exists inside sales reporting. ASSUMPTION
#: (docs/OPEN-QUESTIONS.md Q66).
ENTITY_FEATURES: Mapping[EntityType, FeatureKey] = MappingProxyType(
    {
        EntityType.PRODUCT: FeatureKey.PRODUCT_SEARCH,
        EntityType.CUSTOMER: FeatureKey.SALES_REPORTS,
    }
)


@dataclass(frozen=True, slots=True)
class EntityAccess:
    """What one authenticated actor may see in the registry, decided once per request."""

    user_id: int
    entity_types: frozenset[EntityType]
    is_admin: bool

    def allows(self, entity_type: EntityType) -> bool:
        return entity_type in self.entity_types


def allowed_entity_types(actor: User | None, grants: Mapping[str, bool]) -> frozenset[EntityType]:
    """The entity types an actor may see. An inactive or missing actor sees none.

    Administrators are not filtered by grants, because ``can_use_feature`` already says so; every
    other decision is that one function's answer, not a parallel rule.
    """

    if not is_active(actor):
        return frozenset()
    return frozenset(
        entity_type
        for entity_type, feature in ENTITY_FEATURES.items()
        if can_use_feature(actor, grants, feature)
    )


async def entity_access(session: AsyncSession, telegram_user_id: int) -> EntityAccess:
    """Resolve the actor and their entity visibility in one call.

    Raises :class:`AuthorizationDenied` for a missing or inactive account (fail closed), which is
    the same behaviour every other business service has.
    """

    actor = await require_actor(session, telegram_user_id)
    grants = await FeaturePermissionRepository(session).grants_for_user(actor.id)
    return EntityAccess(
        user_id=actor.id,
        entity_types=allowed_entity_types(actor, grants),
        is_admin=is_admin(actor),
    )


def entity_type_scope(
    column: InstrumentedAttribute[str], access: EntityAccess
) -> ColumnElement[bool]:
    """The condition that keeps a query to the entity types the actor may see.

    One builder serves every table that names an entity type — the registry and the relation layer —
    so a new query cannot invent a second, weaker filter. An empty set compiles to a condition that
    matches nothing, which is the honest result for an actor who may see no entity type at all.
    """

    entity_types = sorted(access.entity_types, key=str)
    return column.in_([entity_type.value for entity_type in entity_types])


def entity_scope(access: EntityAccess) -> ColumnElement[bool]:
    """The registry's own access condition, built by the one shared builder above."""

    return entity_type_scope(Entity.entity_type, access)
