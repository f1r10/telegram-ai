"""Phase 3 relation layer: a relation is sourced, has a window, and is read one hop deep.

The store needs PostgreSQL, which this host does not have, so only the rules that can be asserted
honestly offline are pinned: the vocabulary the migration and the code share, the statement the
one-hop query runs (access filter, validity window, row bound), and the guarantee that a relation
cannot be written without a source.
"""

import asyncio
from datetime import date
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from office_assistant.global_intelligence.entities import (
    ENTITY_FEATURES,
    EntityAccess,
    RelationStore,
    RelationType,
    VerificationStatus,
    relation_endpoints,
)
from office_assistant.global_intelligence.entities.relations import (
    MAX_RELATION_HOPS,
    MAX_RELATIONS_PER_ENTITY,
    one_hop_statement,
)
from office_assistant.infrastructure.database.models import EntityRelation, EntityType, SourceType


def _access(*entity_types: EntityType) -> EntityAccess:
    return EntityAccess(user_id=1, entity_types=frozenset(entity_types), is_admin=False)


def _sql(access: EntityAccess, **kwargs: Any) -> str:
    statement = one_hop_statement(access, **kwargs)
    return str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


def test_every_relation_kind_maps_to_two_registry_entity_types() -> None:
    for relation_type in RelationType:
        subject, object_ = relation_endpoints(relation_type)
        assert subject in ENTITY_FEATURES
        assert object_ in ENTITY_FEATURES
        assert subject is not object_


def test_the_shipped_relation_connects_a_product_to_a_customer() -> None:
    assert relation_endpoints(RelationType.SUPPLIED_TO) == (EntityType.PRODUCT, EntityType.CUSTOMER)


def test_the_verification_vocabulary_matches_the_stored_status_strings() -> None:
    assert {status.value for status in VerificationStatus} == {
        "confirmed",
        "inferred",
        "rejected",
    }
    column = cast(Any, EntityRelation.__table__.c.verification_status)
    assert column.server_default is not None
    assert str(column.server_default.arg) == "inferred", "a new row is unverified until reviewed"


def test_the_one_hop_query_asks_for_exactly_one_relation_kind() -> None:
    sql = _sql(
        _access(EntityType.PRODUCT, EntityType.CUSTOMER),
        entity_id=uuid4(),
        relation_type=RelationType.SUPPLIED_TO,
    )

    assert "'supplied_to'" in sql
    assert "entity_relations.entity_id" in sql
    assert "LIMIT" in sql, "one seed cannot return an unbounded relation list"


def test_the_one_hop_query_returns_only_relations_valid_at_the_asked_instant() -> None:
    sql = _sql(
        _access(EntityType.PRODUCT, EntityType.CUSTOMER),
        entity_id=uuid4(),
        relation_type=RelationType.SUPPLIED_TO,
        as_of=date(2026, 3, 1),
    )

    assert "entity_relations.valid_from <=" in sql
    assert "entity_relations.valid_until" in sql
    assert "'2026-03-01'" in sql


def test_the_one_hop_query_applies_the_actor_access_filter_to_both_ends() -> None:
    product_only = _sql(
        _access(EntityType.PRODUCT),
        entity_id=uuid4(),
        relation_type=RelationType.SUPPLIED_TO,
    )

    assert product_only.count("'product'") >= 2, "the seed and the related identity are both scoped"
    assert "'customer'" not in product_only


def test_an_actor_with_no_entity_type_reads_nothing() -> None:
    class _ExplodingSession:
        async def execute(self, *_: Any, **__: Any) -> Any:
            raise AssertionError("an actor who sees no entity type must not query")

    store = RelationStore(cast(Any, _ExplodingSession()), _access())

    assert (
        asyncio.run(store.one_hop(entity_id=uuid4(), relation_type=RelationType.SUPPLIED_TO)) == ()
    )


def test_a_relation_cannot_be_recorded_without_a_source() -> None:
    store = RelationStore(cast(Any, object()), _access(EntityType.PRODUCT))

    with pytest.raises(TypeError):
        cast(Any, store).record(  # type: ignore[call-arg]
            reason="proposal 12 line 3",
            entity_id=uuid4(),
            related_entity_id=uuid4(),
            relation_type=RelationType.SUPPLIED_TO,
            created_by_user_id=1,
        )


def test_a_relation_cannot_be_recorded_without_a_reason() -> None:
    store = RelationStore(cast(Any, object()), _access(EntityType.PRODUCT))

    with pytest.raises(TypeError):
        cast(Any, store).record(  # type: ignore[call-arg]
            source_reference_id=uuid4(),
            entity_id=uuid4(),
            related_entity_id=uuid4(),
            relation_type=RelationType.SUPPLIED_TO,
            created_by_user_id=1,
        )


def test_a_backwards_validity_window_is_refused_before_anything_is_written() -> None:
    store = RelationStore(cast(Any, object()), _access(EntityType.PRODUCT))

    with pytest.raises(ValueError, match="valid_until"):
        asyncio.run(
            store.record(
                source_reference_id=uuid4(),
                reason="price list 2026",
                entity_id=uuid4(),
                related_entity_id=uuid4(),
                relation_type=RelationType.SUPPLIED_TO,
                created_by_user_id=1,
                valid_from=date(2026, 5, 1),
                valid_until=date(2026, 4, 1),
            )
        )


def test_the_relation_layer_stays_one_hop_deep() -> None:
    assert MAX_RELATION_HOPS == 1
    assert 1 <= MAX_RELATIONS_PER_ENTITY <= 100


def test_the_relation_source_vocabulary_matches_the_shipped_source_types() -> None:
    assert {source.value for source in SourceType} == {
        "TELEGRAM_USER",
        "INGESTION_SESSION",
        "FILE",
        "DOCUMENT_PARSE",
        "MANUAL_ADMIN",
        "FUTURE_AI_EXTRACTION",
    }
