"""Execute readiness aggregates in isolated SQLite, never the shared PostgreSQL DB."""

import asyncio
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import JSON, Column, DateTime, Integer, MetaData, String, Table, create_engine
from sqlalchemy.engine import Connection, Result
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from office_assistant.infrastructure.database.models import OrderReminderStatus, OutboxEventStatus
from office_assistant.scripts.check_readiness import Check, order_reminder_delivery_check

# SQLite returns naive timestamps; use one deterministic, naive clock in these SQL tests.
NOW = datetime(2026, 10, 7, 12)


@pytest.fixture
def connection() -> Iterator[Connection]:
    metadata = MetaData()
    Table("orders", metadata, Column("id", Integer, primary_key=True), Column("state", String))
    Table(
        "order_reminders",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("order_id", Integer),
        Column("status", String),
        Column("last_error", String),
        Column("claimed_at", DateTime),
        Column("scheduled_for", DateTime),
    )
    Table(
        "outbox_events",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("status", String),
        Column("event_type", String),
        Column("last_error", String),
        Column("payload", JSON),
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        metadata.create_all(engine)
        with engine.begin() as connection:
            yield connection
    finally:
        engine.dispose()


class ReadOnlySession:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    async def execute(self, statement: Select[tuple[object, ...]]) -> Result[tuple[object, ...]]:
        sql = str(statement)
        assert sql.startswith("SELECT ")
        assert "FOR UPDATE" not in sql
        return self.connection.execute(statement)


def probe(connection: Connection, *, lease_seconds: int = 300) -> Check:
    return asyncio.run(
        order_reminder_delivery_check(
            cast(AsyncSession, ReadOnlySession(connection)), now=NOW, lease_seconds=lease_seconds
        )
    )


def add_reminder(
    connection: Connection,
    *,
    reminder_id: int,
    status: str = "PENDING",
    state: str = "ACTIVE",
    age: float = 60,
    claim_age: float | None = None,
    error: str | None = None,
) -> None:
    connection.exec_driver_sql("INSERT INTO orders VALUES (?, ?)", (reminder_id, state))
    connection.exec_driver_sql(
        "INSERT INTO order_reminders VALUES (?, ?, ?, ?, ?, ?)",
        (
            reminder_id,
            reminder_id,
            status,
            error,
            (NOW - timedelta(seconds=claim_age)).isoformat(" ", timespec="microseconds")
            if claim_age is not None
            else None,
            (NOW - timedelta(seconds=age)).isoformat(" ", timespec="microseconds"),
        ),
    )


def add_outbox(
    connection: Connection,
    *,
    event_id: int,
    status: str,
    event_type: str = "ORDER_REMINDER_DUE",
    error: str | None = None,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO outbox_events VALUES (?, ?, ?, ?, ?)",
        (
            event_id,
            status,
            event_type,
            error,
            f'{{"reminder_id": {event_id}, "text": "private-content"}}',
        ),
    )


def test_empty_delivery_tables_are_ok(connection: Connection) -> None:
    check = probe(connection)
    assert check.status == "OK"
    assert "ledger[PENDING=0" in check.detail
    assert "outbox[PENDING=0" in check.detail
    assert "active_due_PENDING_oldest_lag_seconds=0" in check.detail


def test_delivery_counts_are_separate_and_filter_other_outbox_events(
    connection: Connection,
) -> None:
    for index, status in enumerate(OrderReminderStatus, start=1):
        add_reminder(connection, reminder_id=index, status=status, error="private-provider-message")
    for index, status in enumerate(OutboxEventStatus, start=1):
        add_outbox(connection, event_id=index, status=status, error="private-provider-message")
    add_reminder(connection, reminder_id=100, status="FAILED", error="DELIVERY_UNKNOWN")
    add_outbox(connection, event_id=100, status="FAILED", error="DELIVERY_UNKNOWN")
    add_outbox(
        connection,
        event_id=101,
        status="FAILED",
        event_type="REMINDER_DUE",
        error="DELIVERY_UNKNOWN",
    )
    check = probe(connection)
    assert check == Check(
        "order_reminder_delivery",
        "WARN",
        "ledger[PENDING=1 CLAIMED=1 SENDING=1 SENT=1 RETRY_PENDING=1 FAILED=2 "
        "SUPERSEDED=1 SKIPPED=1 CANCELLED=1] "
        "outbox[PENDING=1 SENDING=1 SENT=1 CANCELLED=1 FAILED=2] "
        "ledger_stale_SENDING=0 outbox_stale_SENDING=0 "
        "ledger_DELIVERY_UNKNOWN=1 outbox_DELIVERY_UNKNOWN=1 "
        "active_due_PENDING_oldest_lag_seconds=60",
    )


@pytest.mark.parametrize("lease_seconds", [30, 300, 900])
@pytest.mark.parametrize("offset", [-0.001, 0, 0.001])
def test_stale_sending_requires_claim_older_than_configured_lease(
    connection: Connection, lease_seconds: int, offset: float
) -> None:
    add_reminder(connection, reminder_id=1, status="SENDING", claim_age=lease_seconds + offset)
    add_outbox(connection, event_id=1, status="SENDING")
    check = probe(connection, lease_seconds=lease_seconds)
    stale = int(offset > 0)
    assert check.status == ("WARN" if stale else "OK")
    assert f"ledger_stale_SENDING={stale}" in check.detail
    assert f"outbox_stale_SENDING={stale}" in check.detail


def test_stale_outbox_sending_is_visible_even_after_ledger_cancellation(
    connection: Connection,
) -> None:
    add_reminder(connection, reminder_id=1, status="CANCELLED", state="FROZEN", claim_age=301)
    add_outbox(connection, event_id=1, status="SENDING")
    check = probe(connection)
    assert check.status == "WARN"
    assert "ledger_stale_SENDING=0 outbox_stale_SENDING=1" in check.detail
    assert "active_due_PENDING_oldest_lag_seconds=0" in check.detail


@pytest.mark.parametrize("active_age", [None, -60, 0, 90])
def test_pending_lag_excludes_inactive_plans_future_work_and_other_statuses(
    connection: Connection, active_age: int | None
) -> None:
    for index, state in enumerate(
        ("PENDING_CONFIRMATION", "FROZEN", "COMPLETED", "CANCELLED", "DELETED"), start=1
    ):
        add_reminder(connection, reminder_id=index, state=state, age=86400)
    for index, status in enumerate(("CLAIMED", "RETRY_PENDING", "SKIPPED", "SUPERSEDED"), start=10):
        add_reminder(connection, reminder_id=index, status=status, age=86400, claim_age=86400)
    if active_age is not None:
        add_reminder(connection, reminder_id=20, age=active_age)
    check = probe(connection)
    assert check.status == "OK"
    assert f"active_due_PENDING_oldest_lag_seconds={max(0, active_age or 0)}" in check.detail
