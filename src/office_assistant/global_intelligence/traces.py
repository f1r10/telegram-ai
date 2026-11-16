"""Query traces: one row per statement the entity layer ran, so an answer can be checked.

A traced fact says what a query returned; a trace says what that query was, who ran it and what it
cost. Two rules keep the table honest: a row cannot be written without a plan, and ``latency_ms``
is only set from a measurement — an unmeasured read records no number instead of a zero.

The stage rides ``gi_query_traces_enabled`` (off by default): with it off the service opens no
session and writes nothing, so an opted-out deployment behaves exactly as it did before.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.core.settings import Settings
from office_assistant.global_intelligence.entities.access import EntityAccess, entity_access
from office_assistant.infrastructure.database.models import QueryTrace


class QueryStage(StrEnum):
    """The stages that may write a trace; mirrored by ``ck_query_traces_stage``."""

    RESOLUTION = "resolution"
    RELATION_ONE_HOP = "relation_one_hop"
    RELATION_MULTI_HOP = "relation_multi_hop"


def _jsonable(value: object) -> object:
    """The JSONB-safe form of one bound value, so a plan always serializes."""

    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, UUID | date | datetime):
        return str(value)
    # Anything else (a container, an enum) is rendered as text rather than assumed JSON-native.
    return str(value)


def statement_plan(
    statement: Select[Any],
    *,
    parameters: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """The plan recorded for one statement: its text, plus the values it was bound to.

    Compilation uses the PostgreSQL dialect without ``literal_binds``, so the text keeps its
    placeholders and the values stay in ``parameters`` — the trace shows what ran without turning
    the statement into a string interpolation.
    """

    sql = " ".join(str(statement.compile(dialect=postgresql.dialect())).split())
    plan: dict[str, object] = {"statement": sql}
    if parameters:
        plan["parameters"] = {key: _jsonable(value) for key, value in parameters.items()}
    return plan


def traces_statement(access: EntityAccess, *, trace_id: UUID) -> Select[tuple[QueryTrace]]:
    """The statement that reads one trace, visible so its scoping can be asserted offline.

    A non-admin reads only the traces attributed to them, and the filter is in the query rather
    than applied after rows are loaded. An admin may read a whole trace, which is the point of a
    trace: the sequence of statements one answer ran.
    """

    statement = select(QueryTrace).where(QueryTrace.trace_id == trace_id)
    if not access.is_admin:
        statement = statement.where(QueryTrace.user_id == access.user_id)
    return statement.order_by(QueryTrace.created_at, QueryTrace.id)


class QueryTraceStore:
    """Writes and reads traces for one actor.

    The actor is the access decision's user, never a value a caller supplies, so a trace cannot be
    attributed to somebody else.
    """

    def __init__(self, session: AsyncSession, access: EntityAccess) -> None:
        self.session = session
        self.access = access

    async def record(
        self,
        *,
        trace_id: UUID,
        stage: QueryStage,
        plan: Mapping[str, object],
        row_count: int,
        latency_ms: int | None = None,
        source_usage: Mapping[str, object] | None = None,
    ) -> QueryTrace:
        """Record one executed statement, refusing a row that could not be checked later.

        The plan is required because a trace without one says nothing about what ran; a negative
        count or latency is refused because both are measurements, not estimates.
        """

        if not plan:
            raise ValueError("a query trace needs the plan it ran")
        if row_count < 0:
            raise ValueError("row_count is a measured count, not a guess")
        if latency_ms is not None and latency_ms < 0:
            raise ValueError("latency_ms is a measurement, never negative")
        trace = QueryTrace(
            trace_id=trace_id,
            user_id=self.access.user_id,
            stage=stage.value,
            plan_json={key: _jsonable(value) for key, value in plan.items()},
            source_usage_json={
                key: _jsonable(value) for key, value in (source_usage or {}).items()
            },
            row_count=row_count,
            latency_ms=latency_ms,
        )
        self.session.add(trace)
        await self.session.flush()
        return trace

    async def traces(self, trace_id: UUID) -> tuple[QueryTrace, ...]:
        """The statements recorded under ``trace_id`` that this actor may read, in order."""

        statement = traces_statement(self.access, trace_id=trace_id)
        return tuple((await self.session.execute(statement)).scalars())


class QueryTraceService:
    """The flag-gated entry point: a trace is written only when the stage is enabled."""

    FLAG = "gi_query_traces_enabled"

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        enabled: bool = False,
    ) -> None:
        self.session_factory = session_factory
        self.enabled = enabled

    async def record(
        self,
        telegram_user_id: int,
        *,
        trace_id: UUID,
        stage: QueryStage,
        plan: Mapping[str, object],
        row_count: int,
        latency_ms: int | None = None,
        source_usage: Mapping[str, object] | None = None,
    ) -> UUID | None:
        """Write one trace row and return its id, or ``None`` when tracing is off."""

        if not self.enabled:
            return None
        async with self.session_factory() as session:
            access = await entity_access(session, telegram_user_id)
            store = QueryTraceStore(session, access)
            trace = await store.record(
                trace_id=trace_id,
                stage=stage,
                plan=plan,
                row_count=row_count,
                latency_ms=latency_ms,
                source_usage=source_usage,
            )
            await session.commit()
            return trace.id


def query_trace_service(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> QueryTraceService:
    """Build the trace service from configuration, so the flag has one home."""

    return QueryTraceService(session_factory, enabled=settings.gi_query_traces_enabled)
