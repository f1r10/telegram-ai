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

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import Select, String, cast, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from office_assistant.core.settings import Settings
from office_assistant.global_intelligence.entities.access import (
    EntityAccess,
    entity_access,
    entity_scope,
    entity_type_scope,
)
from office_assistant.global_intelligence.entities.types import (
    RelationType,
    VerificationStatus,
    relation_endpoints,
)
from office_assistant.global_intelligence.traces import (
    QueryStage,
    QueryTraceService,
    statement_plan,
)
from office_assistant.infrastructure.database.models import (
    Entity,
    EntityRelation,
    EntityRelationEvidence,
)

logger = logging.getLogger(__name__)

#: How many edges one query may follow from the seed identity. One hop means the identities directly
#: related to the seed, never the identities related to those.
MAX_RELATION_HOPS = 1
#: How many relations one seed may return, so a densely connected identity cannot flood a prompt.
MAX_RELATIONS_PER_ENTITY = 50
#: The starting depth a multi-hop read uses: the seed's neighbours and their neighbours.
DEFAULT_MULTI_HOP_DEPTH = 2
#: The deepest walk any caller may ask for. Every extra hop multiplies the work, so the ceiling is a
#: constant here rather than a number a call site can raise.
MAX_MULTI_HOP_DEPTH = 3


@dataclass(frozen=True, slots=True)
class RelationHop:
    """One relation and the evidence rows that justify it, as a reader may see them.

    ``depth`` is 1 for a relation of the seed itself and larger for a relation reached through a
    multi-hop walk, so a caller can tell a direct claim from a distant one.
    """

    relation: EntityRelation
    evidence: tuple[EntityRelationEvidence, ...]
    depth: int = 1


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


def multi_hop_statement(
    access: EntityAccess,
    *,
    entity_id: UUID,
    relation_type: RelationType,
    max_hops: int = DEFAULT_MULTI_HOP_DEPTH,
    as_of: date | None = None,
) -> Select[tuple[EntityRelation, int]]:
    """The recursive walk the multi-hop read runs, visible so it can be asserted offline.

    The walk is bounded four ways: the requested depth, ``MAX_MULTI_HOP_DEPTH``, the per-seed row
    bound, and a ``visited`` path that refuses to revisit a relation id (a cycle would otherwise
    repeat until the depth bound stopped it). Access is applied at every step: the seed's own type
    and each reached entity's type must both be visible to the actor, and the validity window is
    checked for every hop, not only the first.
    """

    if not 1 <= max_hops <= MAX_MULTI_HOP_DEPTH:
        raise ValueError(f"max_hops must be between 1 and {MAX_MULTI_HOP_DEPTH}")
    moment = as_of or date.today()
    next_hop = aliased(EntityRelation)
    seed = (
        select(
            EntityRelation.id.label("relation_id"),
            EntityRelation.related_entity_id.label("reached_id"),
            literal(1).label("depth"),
            cast(EntityRelation.id, String).label("visited"),
        )
        .where(entity_type_scope(EntityRelation.entity_type, access))
        .where(EntityRelation.entity_id == entity_id)
        .where(EntityRelation.relation_type == relation_type.value)
        .where(EntityRelation.valid_from <= moment)
        .where((EntityRelation.valid_until.is_(None)) | (EntityRelation.valid_until > moment))
    )
    walk = seed.cte("relation_walk", recursive=True)
    step = (
        select(
            next_hop.id,
            next_hop.related_entity_id,
            (walk.c.depth + 1).label("depth"),
            (walk.c.visited + ":" + cast(next_hop.id, String)).label("visited"),
        )
        .join(Entity, Entity.id == walk.c.reached_id)
        .join(next_hop, next_hop.entity_id == walk.c.reached_id)
        .where(entity_scope(access))
        .where(next_hop.relation_type == relation_type.value)
        .where(next_hop.valid_from <= moment)
        .where((next_hop.valid_until.is_(None)) | (next_hop.valid_until > moment))
        .where(walk.c.depth < max_hops)
        .where(~walk.c.visited.contains(cast(next_hop.id, String)))
        .where(next_hop.related_entity_id != entity_id)
    )
    paths = walk.union_all(step)
    return (
        select(EntityRelation, paths.c.depth)
        .join(paths, paths.c.relation_id == EntityRelation.id)
        .order_by(paths.c.depth, EntityRelation.id)
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

    async def multi_hop(
        self,
        *,
        entity_id: UUID,
        relation_type: RelationType,
        max_hops: int = DEFAULT_MULTI_HOP_DEPTH,
        as_of: date | None = None,
    ) -> tuple[RelationHop, ...]:
        """The relations reachable from ``entity_id`` within ``max_hops``, nearest first.

        A relation reached by two different paths is returned once, at its smallest depth, so the
        same claim is never presented twice. The caller keeps the depth and the evidence and can
        decide how far to trust a claim it did not see directly.
        """

        if not self.access.entity_types:
            return ()
        statement = multi_hop_statement(
            self.access,
            entity_id=entity_id,
            relation_type=relation_type,
            max_hops=max_hops,
            as_of=as_of,
        )
        rows = (await self.session.execute(statement)).all()
        if not rows:
            return ()
        depths: dict[UUID, int] = {}
        ordered: list[EntityRelation] = []
        for relation, depth in rows:
            if relation.id in depths:
                continue
            depths[relation.id] = int(depth)
            ordered.append(relation)
        evidence = await self._evidence_for(list(depths))
        return tuple(
            RelationHop(
                relation=relation,
                evidence=evidence.get(relation.id, ()),
                depth=depths[relation.id],
            )
            for relation in ordered
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


class RelationService:
    """The flag-gated entry point for relation reads (Phase 3).

    A caller asks for an identity's neighbours and gets the sourced relations around it, or nothing
    at all. With ``gi_relations_enabled`` off no session opens and the answer is empty, so an
    opted-out deployment behaves exactly as it did before this layer existed.

    When a ``trace_id`` is supplied and a trace service is configured, the statement that ran is
    recorded with its measured latency. Tracing is observability, so its failure is logged and the
    answer is still returned: an audit row is never worth losing a sourced fact over.
    """

    FLAG = "gi_relations_enabled"

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        enabled: bool = False,
        trace: QueryTraceService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.enabled = enabled
        self.trace = trace

    async def neighbors(
        self,
        telegram_user_id: int,
        *,
        entity_id: UUID,
        relation_type: RelationType,
        max_hops: int = MAX_RELATION_HOPS,
        as_of: date | None = None,
        trace_id: UUID | None = None,
    ) -> tuple[RelationHop, ...]:
        """The relations around one identity, at most ``max_hops`` away, for one actor.

        The access decision is the shared one: a user who may see no entity type gets nothing, and
        the query itself carries the filter rather than hiding rows after they were read.
        """

        if not self.enabled:
            return ()
        started = perf_counter()
        async with self.session_factory() as session:
            access = await entity_access(session, telegram_user_id)
            store = RelationStore(session, access)
            one_hop = max_hops <= MAX_RELATION_HOPS
            if one_hop:
                hops = await store.one_hop(
                    entity_id=entity_id, relation_type=relation_type, as_of=as_of
                )
                statement = one_hop_statement(
                    access, entity_id=entity_id, relation_type=relation_type, as_of=as_of
                )
            else:
                hops = await store.multi_hop(
                    entity_id=entity_id,
                    relation_type=relation_type,
                    max_hops=max_hops,
                    as_of=as_of,
                )
                statement = multi_hop_statement(
                    access,
                    entity_id=entity_id,
                    relation_type=relation_type,
                    max_hops=max_hops,
                    as_of=as_of,
                )
        if self.trace is not None and trace_id is not None:
            await self._trace(
                telegram_user_id,
                trace_id=trace_id,
                one_hop=one_hop,
                statement=statement,
                row_count=len(hops),
                latency_ms=int((perf_counter() - started) * 1000),
            )
        return hops

    async def _trace(
        self,
        telegram_user_id: int,
        *,
        trace_id: UUID,
        one_hop: bool,
        statement: Select[Any],
        row_count: int,
        latency_ms: int,
    ) -> None:
        """Record the read that just happened; a trace failure never fails the read."""

        assert self.trace is not None
        try:
            await self.trace.record(
                telegram_user_id,
                trace_id=trace_id,
                stage=QueryStage.RELATION_ONE_HOP if one_hop else QueryStage.RELATION_MULTI_HOP,
                plan=statement_plan(statement),
                row_count=row_count,
                latency_ms=latency_ms,
            )
        except SQLAlchemyError:
            logger.warning("relation trace not recorded", extra={"stage": "relation_trace"})


def relation_service(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    trace: QueryTraceService | None = None,
) -> RelationService:
    """Build the relation service from configuration, so the flag has one home.

    The trace service is passed in rather than built here, because it rides its own flag and a
    caller may already hold one for the answer it is composing.
    """

    return RelationService(session_factory, enabled=settings.gi_relations_enabled, trace=trace)
