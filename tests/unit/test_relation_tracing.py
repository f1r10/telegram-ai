"""Phase 3: a relation read leaves a trace, without the trace ever owning the answer.

The database this would normally run against does not exist on this host, so the test drives the
service through the smallest possible session double: it returns no rows, which is enough to assert
what the service asks for and what it records. The access decision is replaced with a fixed one so
the test measures tracing rather than permissions, which have their own suite.
"""

import asyncio
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from office_assistant.global_intelligence.entities import (
    EntityAccess,
    RelationService,
    RelationType,
)
from office_assistant.global_intelligence.entities import relations as relations_module
from office_assistant.global_intelligence.traces import QueryStage
from office_assistant.infrastructure.database.models import EntityType


class _EmptyResult:
    def scalars(self) -> list[object]:
        return []

    def all(self) -> list[object]:
        return []


class _Session:
    async def execute(self, statement: object) -> _EmptyResult:
        return _EmptyResult()


class _SessionContext:
    async def __aenter__(self) -> _Session:
        return _Session()

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Factory:
    def __call__(self) -> _SessionContext:
        return _SessionContext()


class _TraceSpy:
    """Records what the service asked it to trace; can fail on demand."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[int, dict[str, Any]]] = []
        self.error = error

    async def record(self, telegram_user_id: int, **kwargs: Any) -> str:
        self.calls.append((telegram_user_id, kwargs))
        if self.error is not None:
            raise self.error
        return "trace-id"


def _access() -> EntityAccess:
    return EntityAccess(user_id=7, entity_types=frozenset({EntityType.PRODUCT}), is_admin=False)


@pytest.fixture
def fixed_access(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_access(session: object, telegram_user_id: int) -> EntityAccess:
        return _access()

    monkeypatch.setattr(relations_module, "entity_access", fake_access)


def _service(spy: _TraceSpy) -> RelationService:
    return RelationService(cast(Any, _Factory()), enabled=True, trace=cast(Any, spy))


def test_a_one_hop_read_records_the_statement_it_ran(fixed_access: None) -> None:
    spy = _TraceSpy()
    trace_id = uuid4()

    asyncio.run(
        _service(spy).neighbors(
            7,
            entity_id=uuid4(),
            relation_type=RelationType.SUPPLIED_TO,
            trace_id=trace_id,
        )
    )

    assert len(spy.calls) == 1
    user_id, kwargs = spy.calls[0]
    assert user_id == 7
    assert kwargs["trace_id"] == trace_id
    assert kwargs["stage"] is QueryStage.RELATION_ONE_HOP
    assert kwargs["row_count"] == 0
    assert kwargs["latency_ms"] >= 0, "latency is measured, never invented"
    assert "entity_relations" in str(kwargs["plan"]["statement"])


def test_a_multi_hop_read_is_traced_as_the_recursive_stage(fixed_access: None) -> None:
    spy = _TraceSpy()

    asyncio.run(
        _service(spy).neighbors(
            7,
            entity_id=uuid4(),
            relation_type=RelationType.SUPPLIED_TO,
            max_hops=2,
            trace_id=uuid4(),
        )
    )

    assert len(spy.calls) == 1
    kwargs = spy.calls[0][1]
    assert kwargs["stage"] is QueryStage.RELATION_MULTI_HOP
    assert "WITH RECURSIVE" in str(kwargs["plan"]["statement"])


def test_a_read_without_a_trace_id_records_nothing(fixed_access: None) -> None:
    spy = _TraceSpy()

    asyncio.run(
        _service(spy).neighbors(7, entity_id=uuid4(), relation_type=RelationType.SUPPLIED_TO)
    )

    assert spy.calls == []


def test_a_failing_trace_does_not_fail_the_read(fixed_access: None) -> None:
    spy = _TraceSpy(error=SQLAlchemyError("trace table unavailable"))

    outcome = asyncio.run(
        _service(spy).neighbors(
            7,
            entity_id=uuid4(),
            relation_type=RelationType.SUPPLIED_TO,
            trace_id=uuid4(),
        )
    )

    assert outcome == (), "the answer survives a trace failure"
    assert len(spy.calls) == 1, "tracing was attempted before it failed"


def test_the_flag_off_service_traces_nothing() -> None:
    spy = _TraceSpy()
    service = RelationService(cast(Any, _Factory()), enabled=False, trace=cast(Any, spy))

    asyncio.run(
        service.neighbors(
            7,
            entity_id=uuid4(),
            relation_type=RelationType.SUPPLIED_TO,
            trace_id=uuid4(),
        )
    )

    assert spy.calls == []
