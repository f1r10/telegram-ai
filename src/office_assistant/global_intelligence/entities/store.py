"""Database reads and writes behind entity resolution (Phase 3).

Resolution itself is pure (``resolution.py``). This module only loads the records an access-scoped
actor may see and stores what a human still has to decide, so the decision cannot depend on how the
rows were fetched.

Two deliberate choices:

* the registry points at ``products``/``customers`` instead of copying them, so the forms those
  tables already accept are read from ``product_aliases``/``customer_aliases`` directly;
* every query that touches the registry goes through the one access condition built in
  ``access.py``, so a hidden entity type cannot be reached by forgetting a filter.
"""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.business.normalization import normalize_product_code
from office_assistant.global_intelligence.entities.access import EntityAccess, entity_scope
from office_assistant.global_intelligence.entities.resolution import (
    EntityRecord,
    Resolution,
    ResolutionStatus,
    normalize_mention,
)
from office_assistant.infrastructure.database.models import (
    CustomerAlias,
    Entity,
    EntityAlias,
    EntityCandidate,
    EntityType,
    Product,
    ProductAlias,
)

#: How many identities one resolution pass may load. The bound keeps a mention from scanning an
#: unbounded registry; it is visible here rather than hidden in a query. ASSUMPTION (Q66).
MAX_ENTITY_RECORDS = 500


class EntityStore:
    """Access-scoped reads of the registry plus the business aliases that already exist."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load(self, access: EntityAccess) -> list[EntityRecord]:
        """Every identity the actor may see, with its aliases and business identifiers.

        Aliases come from both the registry and the record-level tables: an existing product alias
        is a fact about that product, and re-entering it in ``entity_aliases`` would let the two
        drift apart.
        """

        if not access.entity_types:
            return []
        statement = (
            select(Entity)
            .where(entity_scope(access))
            .order_by(Entity.normalized_name, Entity.id)
            .limit(MAX_ENTITY_RECORDS)
        )
        entities = list((await self.session.execute(statement)).scalars())
        if not entities:
            return []
        ids = [entity.id for entity in entities]
        aliases: dict[UUID, set[str]] = defaultdict(set)
        identifiers: dict[UUID, set[str]] = defaultdict(set)

        registry_aliases = await self.session.execute(
            select(EntityAlias.entity_id, EntityAlias.normalized_alias).where(
                EntityAlias.entity_id.in_(ids)
            )
        )
        for entity_id, alias in registry_aliases.all():
            aliases[entity_id].add(alias)

        # The business tables are keyed by their own row id, while a record is keyed by the registry
        # entity id, so every fetched fact is re-keyed onto the entity that names it.
        entity_ids_by_record: dict[UUID, UUID] = {
            entity.business_record_id: entity.id for entity in entities
        }
        product_ids = [
            entity.business_record_id
            for entity in entities
            if entity.entity_type == EntityType.PRODUCT.value
        ]
        if product_ids:
            product_aliases = await self.session.execute(
                select(ProductAlias.product_id, ProductAlias.normalized_alias).where(
                    ProductAlias.product_id.in_(product_ids)
                )
            )
            for product_id, alias in product_aliases.all():
                aliases[entity_ids_by_record[product_id]].add(alias)
            codes = await self.session.execute(
                select(Product.id, Product.canonical_code).where(Product.id.in_(product_ids))
            )
            for product_id, canonical_code in codes.all():
                normalized_code = _normalized_code(canonical_code)
                if normalized_code:
                    identifiers[entity_ids_by_record[product_id]].add(normalized_code)

        customer_ids = [
            entity.business_record_id
            for entity in entities
            if entity.entity_type == EntityType.CUSTOMER.value
        ]
        if customer_ids:
            customer_aliases = await self.session.execute(
                select(CustomerAlias.customer_id, CustomerAlias.normalized_alias).where(
                    CustomerAlias.customer_id.in_(customer_ids)
                )
            )
            for customer_id, alias in customer_aliases.all():
                aliases[entity_ids_by_record[customer_id]].add(alias)

        return [
            EntityRecord(
                entity_id=str(entity.id),
                entity_type=EntityType(entity.entity_type),
                canonical_name=entity.canonical_name,
                normalized_name=entity.normalized_name,
                aliases=tuple(sorted(aliases.get(entity.id, set()))),
                identifiers=tuple(sorted(identifiers.get(entity.id, set()))),
            )
            for entity in entities
        ]

    async def record_candidates(self, access: EntityAccess, outcome: Resolution) -> int:
        """Store the candidates an ambiguous mention produced, idempotently.

        The same mention meeting the same entity twice is one review row, not two: the insert relies
        on the unique constraint and does nothing on conflict.
        """

        if outcome.status is not ResolutionStatus.AMBIGUOUS or not outcome.candidates:
            return 0
        rows: list[dict[str, object]] = []
        for candidate in outcome.candidates:
            normalized = outcome.normalized_for(candidate.entity_type)[:512]
            if not normalized:
                continue
            rows.append(
                {
                    "mention": outcome.mention[:512],
                    "normalized_mention": normalized,
                    "proposed_entity_id": UUID(candidate.entity_id),
                    "score": candidate.score,
                    "reason": candidate.reason.value,
                    "status": "pending",
                    "created_by_user_id": access.user_id,
                }
            )
        if not rows:
            return 0
        await self.session.execute(
            insert(EntityCandidate)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["proposed_entity_id", "normalized_mention"])
        )
        return len(rows)

    async def register(
        self,
        *,
        entity_type: EntityType,
        business_record_id: UUID,
        canonical_name: str,
        created_by_user_id: int,
    ) -> Entity:
        """Return the registry row for one business record, creating it once if it is absent."""

        existing = (
            await self.session.execute(
                select(Entity).where(
                    Entity.entity_type == entity_type.value,
                    Entity.business_record_id == business_record_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        normalized = _normalized_form(entity_type, canonical_name)
        if not normalized:
            raise ValueError("entity name normalizes to nothing")
        entity = Entity(
            entity_type=entity_type.value,
            business_record_id=business_record_id,
            canonical_name=canonical_name.strip()[:512],
            normalized_name=normalized[:512],
            created_by_user_id=created_by_user_id,
        )
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def add_alias(
        self,
        *,
        entity_id: UUID,
        entity_type: EntityType,
        alias: str,
        created_by_user_id: int,
        source_reference_id: UUID,
        locator: dict[str, object] | None = None,
    ) -> None:
        """Record one accepted surface form for an entity, once."""

        normalized = _normalized_form(entity_type, alias)
        if not normalized:
            raise ValueError("entity alias normalizes to nothing")
        await self.session.execute(
            insert(EntityAlias)
            .values(
                entity_id=entity_id,
                alias=alias.strip()[:512],
                normalized_alias=normalized[:512],
                locator=locator,
                created_by_user_id=created_by_user_id,
                source_reference_id=source_reference_id,
            )
            .on_conflict_do_nothing(index_elements=["entity_id", "normalized_alias"])
        )


def _normalized_form(entity_type: EntityType, value: str) -> str:
    product, customer = normalize_mention(value)
    return product if entity_type is EntityType.PRODUCT else customer


def _normalized_code(code: str) -> str:
    try:
        return normalize_product_code(code)
    except ValueError:
        return ""
