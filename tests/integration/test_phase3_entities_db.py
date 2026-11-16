"""Phase 3 against a real PostgreSQL: registry, sourced relations, walks and traces.

Unit tests pin the SQL and the refusals; this test proves the same paths against the migrated
schema — a row written, read back, filtered by access and by the validity window, and an audit trace
that points at the statement which produced the answer. It runs only when ``POSTGRES_*`` names a
disposable ``*_test`` database (enforced by ``tests/conftest.py``).
"""

import asyncio
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.errors import AuthorizationDenied
from office_assistant.business.normalization import normalize_customer_name
from office_assistant.core.settings import Settings, load_settings
from office_assistant.global_intelligence.entities import (
    EntityResolutionService,
    RelationStore,
    RelationType,
    ResolutionStatus,
    VerificationStatus,
    entity_access,
)
from office_assistant.global_intelligence.entities.store import EntityStore
from office_assistant.global_intelligence.traces import (
    QueryStage,
    QueryTraceService,
    QueryTraceStore,
    statement_plan,
)
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    Customer,
    Entity,
    EntityAlias,
    EntityCandidate,
    EntityRelation,
    EntityRelationEvidence,
    EntityType,
    Product,
    QueryTrace,
    SourceReference,
    SourceType,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory

pytestmark = pytest.mark.integration

ADMIN_ID = 980001
INACTIVE_ID = 980002


def _settings_or_skip() -> Settings:
    settings = load_settings()
    if not all(
        (
            settings.postgres_db,
            settings.postgres_user,
            settings.postgres_password,
            settings.postgres_host,
        )
    ):
        pytest.skip("PostgreSQL integration settings are not configured")
    return settings


async def _reset(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory.begin() as session:
        for model in (
            EntityRelationEvidence,
            EntityRelation,
            EntityCandidate,
            EntityAlias,
            Entity,
            QueryTrace,
            Product,
            Customer,
            SourceReference,
        ):
            await session.execute(delete(model))
        await session.execute(
            delete(User).where(User.telegram_user_id.in_([ADMIN_ID, INACTIVE_ID]))
        )


async def _actors(factory: async_sessionmaker[AsyncSession]) -> tuple[int, UUID]:
    """One admin actor and one disabled account; returns the admin row id and a source reference."""

    async with factory.begin() as session:
        admin = User(telegram_user_id=ADMIN_ID, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
        session.add_all(
            [
                admin,
                User(telegram_user_id=INACTIVE_ID, role=UserRole.USER, status=UserStatus.DISABLED),
            ]
        )
        await session.flush()
        source = SourceReference(
            source_type=SourceType.MANUAL_ADMIN.value,
            actor_user_id=admin.id,
            description="Synthetic Phase-3 integration source",
        )
        session.add(source)
        await session.flush()
        return admin.id, source.id


async def _business_records(
    factory: async_sessionmaker[AsyncSession], *, actor_id: int, source_id: UUID
) -> tuple[Product, Customer]:
    async with factory.begin() as session:
        product = Product(
            canonical_code="M404",
            display_code="M404",
            name="HP LaserJet Pro M404dn",
            created_by_user_id=actor_id,
            source_reference_id=source_id,
        )
        customer = Customer(
            display_name="Delta MMC",
            normalized_name=normalize_customer_name("Delta MMC"),
            created_by_user_id=actor_id,
            source_reference_id=source_id,
        )
        session.add_all([product, customer])
        await session.flush()
        return product, customer


def test_phase3_registry_relations_walks_and_traces_on_postgres() -> None:
    settings = _settings_or_skip()
    asyncio.run(_scenario(settings))


async def _scenario(settings: Settings) -> None:
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        await _reset(factory)
        actor_id, source_id = await _actors(factory)
        product_row, customer_row = await _business_records(
            factory, actor_id=actor_id, source_id=source_id
        )

        # --- registry: register, alias, and load through the shared access filter ------------
        async with factory.begin() as session:
            access = await entity_access(session, ADMIN_ID)
            assert access.allows(EntityType.PRODUCT)
            assert access.allows(EntityType.CUSTOMER)
            store = EntityStore(session)
            product = await store.register(
                entity_type=EntityType.PRODUCT,
                business_record_id=product_row.id,
                canonical_name="HP LaserJet Pro M404dn",
                created_by_user_id=actor_id,
            )
            customer = await store.register(
                entity_type=EntityType.CUSTOMER,
                business_record_id=customer_row.id,
                canonical_name="Delta MMC",
                created_by_user_id=actor_id,
            )
            await store.add_alias(
                entity_id=product.id,
                entity_type=EntityType.PRODUCT,
                alias="HP M404",
                created_by_user_id=actor_id,
                source_reference_id=source_id,
            )
            records = await store.load(access)
        assert {record.entity_id for record in records} == {str(product.id), str(customer.id)}
        product_record = next(item for item in records if item.entity_id == str(product.id))
        assert "M404" in product_record.identifiers
        assert "HPM404" in product_record.aliases

        # A second registration of the same business record returns the row it already has.
        async with factory.begin() as session:
            again = await EntityStore(session).register(
                entity_type=EntityType.PRODUCT,
                business_record_id=product_row.id,
                canonical_name="HP LaserJet Pro M404dn",
                created_by_user_id=actor_id,
            )
            assert again.id == product.id

        # --- resolution: a code wins, a typo resolves to nothing ----------------------------
        resolution = EntityResolutionService(factory, enabled=True)
        by_code = await resolution.resolve(ADMIN_ID, "M404", identifier="M404")
        assert by_code.status is ResolutionStatus.RESOLVED
        assert by_code.record is not None and by_code.record.entity_id == str(product.id)
        typo = await resolution.resolve(ADMIN_ID, "Plinter M404")
        assert typo.status is not ResolutionStatus.RESOLVED

        # --- relations: sourced, evidenced, windowed, and walked -----------------------------
        async with factory.begin() as session:
            access = await entity_access(session, ADMIN_ID)
            relations = RelationStore(session, access)
            today = date.today()
            relation = await relations.record(
                source_reference_id=source_id,
                reason="delivery note 2026-11",
                entity_id=product.id,
                related_entity_id=customer.id,
                relation_type=RelationType.SUPPLIED_TO,
                created_by_user_id=actor_id,
                verification_status=VerificationStatus.CONFIRMED,
                valid_from=today - timedelta(days=30),
            )
            one_hop = await relations.one_hop(
                entity_id=product.id, relation_type=RelationType.SUPPLIED_TO
            )
        assert [hop.relation.id for hop in one_hop] == [relation.id]
        assert one_hop[0].depth == 1
        assert one_hop[0].evidence and one_hop[0].evidence[0].source_reference_id == source_id

        async with factory.begin() as session:
            access = await entity_access(session, ADMIN_ID)
            relations = RelationStore(session, access)
            walk = await relations.multi_hop(
                entity_id=product.id, relation_type=RelationType.SUPPLIED_TO, max_hops=2
            )
            before_window = await relations.one_hop(
                entity_id=product.id,
                relation_type=RelationType.SUPPLIED_TO,
                as_of=today - timedelta(days=60),
            )
        assert [hop.relation.id for hop in walk] == [relation.id], "the same claim is not repeated"
        assert before_window == (), "a relation outside its window is not a fact"

        # A second evidence row for the same source is one row, not two.
        async with factory.begin() as session:
            access = await entity_access(session, ADMIN_ID)
            await RelationStore(session, access).add_evidence(
                relation_id=relation.id,
                source_reference_id=source_id,
                reason="same source seen twice",
                created_by_user_id=actor_id,
            )
            evidence = (
                await session.execute(
                    select(EntityRelationEvidence).where(
                        EntityRelationEvidence.relation_id == relation.id
                    )
                )
            ).scalars()
            assert len(list(evidence)) == 1

        # --- traces: the read above is auditable ---------------------------------------------
        trace_id = uuid4()
        traces = QueryTraceService(factory, enabled=True)
        stored = await traces.record(
            ADMIN_ID,
            trace_id=trace_id,
            stage=QueryStage.RELATION_ONE_HOP,
            plan=statement_plan(
                select(EntityRelation).where(EntityRelation.entity_id == product.id)
            ),
            row_count=1,
            latency_ms=3,
        )
        assert stored is not None
        async with factory.begin() as session:
            access = await entity_access(session, ADMIN_ID)
            rows = await QueryTraceStore(session, access).traces(trace_id)
        assert len(rows) == 1
        assert rows[0].user_id == actor_id
        assert rows[0].stage == QueryStage.RELATION_ONE_HOP.value
        assert "entity_relations" in str(rows[0].plan_json["statement"])

        # --- access: a disabled account is refused, not shown a partial registry ------------
        with pytest.raises(AuthorizationDenied):
            async with factory.begin() as session:
                await entity_access(session, INACTIVE_ID)
    finally:
        # Leave the shared test database as it was found: the rows above reference a source and an
        # actor, and another suite deletes all sources.
        await _reset(factory)
        await engine.dispose()
