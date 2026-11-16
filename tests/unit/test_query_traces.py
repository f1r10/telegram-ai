"""Phase 3 query traces: every statement the entity layer ran can be inspected afterwards.

The store needs PostgreSQL, which this host does not have, so only the rules that can be asserted
honestly offline are pinned: the stage vocabulary the migration and the code share, the plan a
trace records, the refusals that keep a trace from recording an unmeasured number, the per-actor
scoping of the read, and the flag-off path.
"""

import asyncio
from datetime import date
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from office_assistant.core.settings import Settings
from office_assistant.global_intelligence.entities import EntityAccess, RelationType
from office_assistant.global_intelligence.entities.relations import one_hop_statement
from office_assistant.global_intelligence.traces import (
    QueryStage,
    QueryTraceService,
    QueryTraceStore,
    query_trace_service,
    statement_plan,
    traces_statement,
)
from office_assistant.infrastructure.database.models import EntityType, QueryTrace


def _access(*entity_types: EntityType, user_id: int = 7, is_admin: bool = False) -> EntityAccess:
    return EntityAccess(user_id=user_id, entity_types=frozenset(entity_types), is_admin=is_admin)


def _sql(statement: Any) -> str:
    return str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


def _store() -> QueryTraceStore:
    """A store whose validation runs before any SQLAlchemy call would need a database."""

    return QueryTraceStore(cast(Any, object()), _access(EntityType.PRODUCT, user_id=7))


def test_the_stage_vocabulary_matches_the_stored_check_constraint() -> None:
    table = cast(Any, QueryTrace.__table__)
    constraint = next(item for item in table.constraints if item.name == "ck_query_traces_stage")
    text = str(constraint.sqltext)

    for stage in QueryStage:
        assert f"'{stage.value}'" in text


def test_the_trace_table_keeps_latency_nullable_so_no_unmeasured_number_is_stored() -> None:
    latency = QueryTrace.__table__.c.latency_ms
    assert latency.nullable is True, "an unmeasured read must record no latency at all"

    row_count = QueryTrace.__table__.c.row_count
    assert row_count.nullable is False, "a trace without a measured row count says nothing"


def test_a_plan_records_the_statement_text_and_the_values_it_was_bound_to() -> None:
    entity_id = uuid4()
    statement = one_hop_statement(
        _access(EntityType.PRODUCT),
        entity_id=entity_id,
        relation_type=RelationType.SUPPLIED_TO,
    )

    plan = statement_plan(statement, parameters={"entity_id": entity_id, "as_of": date(2026, 1, 2)})

    assert isinstance(plan["statement"], str)
    assert "FROM entity_relations" in str(plan["statement"])
    assert "\n" not in str(plan["statement"]), "the plan is one readable line"
    parameters = cast(dict[str, object], plan["parameters"])
    assert parameters == {"entity_id": str(entity_id), "as_of": "2026-01-02"}
    assert isinstance(parameters["entity_id"], str), "a UUID must serialize for JSONB"


def test_a_plan_without_parameters_carries_no_empty_block() -> None:
    statement = one_hop_statement(
        _access(EntityType.PRODUCT),
        entity_id=uuid4(),
        relation_type=RelationType.SUPPLIED_TO,
    )

    assert set(statement_plan(statement)) == {"statement"}


def test_a_trace_cannot_be_written_without_a_plan() -> None:
    with pytest.raises(ValueError, match="plan"):
        asyncio.run(
            _store().record(trace_id=uuid4(), stage=QueryStage.RESOLUTION, plan={}, row_count=0)
        )


def test_a_trace_refuses_an_unmeasured_or_negative_cost() -> None:
    with pytest.raises(ValueError, match="row_count"):
        asyncio.run(
            _store().record(
                trace_id=uuid4(),
                stage=QueryStage.RESOLUTION,
                plan={"statement": "SELECT 1"},
                row_count=-1,
            )
        )

    with pytest.raises(ValueError, match="latency"):
        asyncio.run(
            _store().record(
                trace_id=uuid4(),
                stage=QueryStage.RESOLUTION,
                plan={"statement": "SELECT 1"},
                row_count=0,
                latency_ms=-5,
            )
        )


def test_a_non_admin_reads_only_their_own_trace_rows() -> None:
    trace_id = uuid4()

    own = _sql(traces_statement(_access(user_id=7), trace_id=trace_id))
    assert "query_traces.user_id = 7" in own

    admin = _sql(traces_statement(_access(user_id=1, is_admin=True), trace_id=trace_id))
    assert "query_traces.user_id =" not in admin, "an admin reads the whole trace, unfiltered"
    assert str(trace_id) in admin, "an admin still reads only the trace they asked for"


def test_the_trace_read_is_ordered_so_a_sequence_reads_as_one() -> None:
    sql = _sql(traces_statement(_access(user_id=7), trace_id=uuid4()))
    assert "ORDER BY query_traces.created_at, query_traces.id" in sql


class _ExplodingFactory:
    """A session factory that fails if the flag-off path touches the database at all."""

    def begin(self) -> Any:
        raise AssertionError("the disabled path must not open a session")

    def __call__(self) -> Any:
        raise AssertionError("the disabled path must not open a session")


def test_the_trace_flag_off_path_writes_nothing() -> None:
    service = QueryTraceService(cast(Any, _ExplodingFactory()), enabled=False)

    outcome = asyncio.run(
        service.record(
            1,
            trace_id=uuid4(),
            stage=QueryStage.RESOLUTION,
            plan={"statement": "SELECT 1"},
            row_count=0,
        )
    )

    assert outcome is None


def test_the_trace_service_reads_its_default_from_the_flag() -> None:
    default = query_trace_service(cast(Any, _ExplodingFactory()), Settings())
    assert default.enabled is False, "gi_query_traces_enabled defaults to off"

    enabled = query_trace_service(
        cast(Any, _ExplodingFactory()), Settings(gi_query_traces_enabled=True)
    )
    assert enabled.enabled is True
