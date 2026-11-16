"""Entity relations, the evidence behind them, and the one-hop query that reads them.

A relation is a claim a person can check, not a connection the engine invented: every row carries
the source that justifies it, a verification status, and the window in which the claim holds.
``record`` writes the relation and its first evidence row together, so no code path creates a
relation without provenance (invariants 1 and 2 of ``AGENTS.md``).

Reading is deliberately shallow. ``one_hop`` returns the relations of one seed at one instant,
bounded by ``MAX_RELATIONS_PER_ENTITY``, and never follows a second edge: a multi-hop walk is a
separate planned step with its own budget, not something this query grows into by accident.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.global_intelligence.entities.access import (
    EntityAccess,
    entity_scope,
    entity_type_scope,
)
from office_assistant.global_intelligence.entities.types import (
    RelationType,
    VerificationStatus,
    relation_endpoints,
)
from office_assistant.infrastructure.database.models import (
    Entity,
    EntityRelation,
    EntityRelationEvidence,
)

#: How many edges one query may follow from the seed identity. One hop means the identities directly
#: related to the seed, never the identities related to those.
MAX_RELATION_HOPS = 1
#: How many relations one seed may return, so a densely connected identity cannot flood a prompt.
MAX_RELATIONS_PER_ENTITY = 50


@dataclass(frozen=True, slots=True)
class RelationHop:
    """One relation and the evidence rows that justify it, as a reader may see them."""

    relation: EntityRelation
    evidence: tuple[EntityRelationEvidence, ...]


def one_hop_statement(
    access: EntityAccess,
    *,
    entity_id: UUID,
    relation_type: RelationType,
    as_of: date | None = None,
) -> Select[tuple[EntityRelation]]:
    """The single statement the one-hop query runs, visible so it can be asserted offline.

    Three rules live here and nowhere else: the seed's entity type must be one the actor may see,
    the related identity must be visible too (checked on the joined ``entities`` row), and a
    relation is only returned when ``as_of`` falls inside ``valid_from``/``valid_until``. The window
    is half-open, so a relation that ended on ``as_of`` is already history.
    """

    moment = as_of or date.today()
    return (
        select(EntityRelation)
        .join(Entity, Entity.id == EntityRelation.related_entity_id)
        .where(entity_type_scope(EntityRelation.entity_type, access))
        .where(entity_scope(access))
        .where(EntityRelation.entity_id == entity_id)
        .where(EntityRelation.relation_type == relation_type.value)
        .where(EntityRelation.valid_from <= moment)
        .where((EntityRelation.valid_until.is_(None)) | (EntityRelation.valid_until > moment))
        .order_by(EntityRelation.valid_from.desc(), EntityRelation.id)
        .limit(MAX_RELATIONS_PER_ENTITY)
    )


class RelationStore:
    """Access-scoped relation reads, plus the two writes that keep provenance mandatory."""

    def __init__(self, session: AsyncSession, access: EntityAccess) -> None:
        self.session = session
        self.access = access

    async def one_hop(
        self,
        *,
        entity_id: UUID,
        relation_type: RelationType,
        as_of: date | None = None,
    ) -> tuple[RelationHop, ...]:
        """The relations of ``entity_id`` at ``as_of``, each with the evidence behind it.

        An actor who may see no entity type gets nothing without a query being sent, the same answer
        the registry gives rather than a filtered subset of one.
        """

        if not self.access.entity_types:
            return ()
        statement = one_hop_statement(
            self.access, entity_id=entity_id, relation_type=relation_type, as_of=as_of
        )
        relations = list((await self.session.execute(statement)).scalars())
        if not relations:
            return ()
        evidence = await self._evidence_for([relation.id for relation in relations])
        return tuple(
            RelationHop(relation=relation, evidence=evidence.get(relation.id, ()))
            for relation in relations
        )

    async def record(
        self,
        *,
        source_reference_id: UUID,
        reason: str,
        entity_id: UUID,
        related_entity_id: UUID,
        relation_type: RelationType,
        created_by_user_id: int,
        verification_status: VerificationStatus = VerificationStatus.INFERRED,
        valid_from: date | None = None,
        valid_until: date | None = None,
        locator: dict[str, object] | None = None,
    ) -> EntityRelation:
        """Write one relation together with its first source, and return it.

        ``source_reference_id`` and ``reason`` are required keyword arguments, so a relation can
        never be recorded without provenance even by a caller that forgets to think about it. A
        backwards window is rejected before any row is added: a claim that never held is not a fact.
        """

        start = valid_from or date.today()
        if valid_until is not None and valid_until <= start:
            raise ValueError("valid_until must be after valid_from")
        if not reason.strip():
            raise ValueError("relation evidence needs a reason")
        relation = EntityRelation(
            entity_id=entity_id,
            entity_type=relation_endpoints(relation_type)[0].value,
            relation_type=relation_type.value,
            related_entity_id=related_entity_id,
            verification_status=verification_status.value,
            valid_from=start,
            valid_until=valid_until,
            source_reference_id=source_reference_id,
            created_by_user_id=created_by_user_id,
        )
        self.session.add(relation)
        await self.session.flush()
        await self.add_evidence(
            relation_id=relation.id,
            source_reference_id=source_reference_id,
            reason=reason,
            created_by_user_id=created_by_user_id,
            locator=locator,
        )
        return relation

    async def add_evidence(
        self,
        *,
        relation_id: UUID,
        source_reference_id: UUID,
        reason: str,
        created_by_user_id: int,
        locator: dict[str, object] | None = None,
    ) -> None:
        """Attach one more source to an existing relation, once per source."""

        await self.session.execute(
            insert(EntityRelationEvidence)
            .values(
                relation_id=relation_id,
                source_reference_id=source_reference_id,
                reason=reason.strip()[:512],
                locator=locator,
                created_by_user_id=created_by_user_id,
            )
            .on_conflict_do_nothing(index_elements=["relation_id", "source_reference_id"])
        )

    async def _evidence_for(
        self, relation_ids: list[UUID]
    ) -> dict[UUID, tuple[EntityRelationEvidence, ...]]:
        if not relation_ids:
            return {}
        rows = (
            await self.session.execute(
                select(EntityRelationEvidence)
                .where(EntityRelationEvidence.relation_id.in_(relation_ids))
                .order_by(EntityRelationEvidence.created_at, EntityRelationEvidence.id)
            )
        ).scalars()
        grouped: dict[UUID, list[EntityRelationEvidence]] = defaultdict(list)
        for row in rows:
            grouped[row.relation_id].append(row)
        return {relation_id: tuple(items) for relation_id, items in grouped.items()}
