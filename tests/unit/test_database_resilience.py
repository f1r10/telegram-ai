"""Pool recovery settings and the startup clock comparison."""

import asyncio
import logging
import socket
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from office_assistant.core.settings import Settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.health import (
    ClockDrift,
    check_clock_drift,
    measure_clock_drift,
)

HEALTH_LOGGER = "office_assistant.infrastructure.database.health"


class _FakeResult:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def scalar_one(self) -> datetime:
        return self.value


class _FakeConnection:
    def __init__(self, server_now: datetime) -> None:
        self.server_now = server_now

    async def __aenter__(self) -> "_FakeConnection":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def execute(self, statement: object) -> _FakeResult:
        return _FakeResult(self.server_now)


class _FakeEngine:
    """Stands in for an engine whose server clock is known, or which fails outright."""

    def __init__(self, connection: _FakeConnection | BaseException) -> None:
        self.connection = connection

    def connect(self) -> _FakeConnection:
        if isinstance(self.connection, BaseException):
            raise self.connection
        return self.connection


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "postgres_db": "office_assistant",
        "postgres_user": "assistant",
        "postgres_password": SecretStr("do-not-log-me"),
        "postgres_host": "postgres",
    }
    base.update(overrides)
    return Settings(**base)


def _engine_now(server_now: datetime) -> AsyncEngine:
    return cast(AsyncEngine, _FakeEngine(_FakeConnection(server_now)))


def _engine_that_raises(error: BaseException) -> AsyncEngine:
    return cast(AsyncEngine, _FakeEngine(error))


def _pool_tuning(engine: AsyncEngine) -> tuple[bool, int]:
    """SQLAlchemy exposes pool tuning only as protected attributes."""

    pool: Any = engine.pool
    return pool._pre_ping, pool._recycle


def test_the_pool_is_configured_to_ping_and_recycle() -> None:
    engine = create_database_engine(_settings(postgres_pool_recycle_seconds=61))

    assert _pool_tuning(engine) == (True, 61)


def test_pool_defaults_bound_the_connection_age() -> None:
    engine = create_database_engine(_settings())

    assert _pool_tuning(engine) == (True, 900)


def test_only_a_gap_beyond_the_bound_is_suspect() -> None:
    assert ClockDrift(seconds=180, threshold_seconds=120).is_suspect
    assert ClockDrift(seconds=-180, threshold_seconds=120).is_suspect
    assert not ClockDrift(seconds=120, threshold_seconds=120).is_suspect
    assert not ClockDrift(seconds=0.4, threshold_seconds=120).is_suspect


def test_the_drift_is_positive_when_the_process_runs_ahead() -> None:
    server_now = datetime.now(UTC) - timedelta(seconds=30)

    drift = asyncio.run(measure_clock_drift(_engine_now(server_now), threshold_seconds=120))

    assert 29.0 < drift.seconds < 31.0


def test_a_suspect_clock_warns_and_keeps_the_process_running(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server_now = datetime.now(UTC) - timedelta(hours=19)

    with caplog.at_level(logging.INFO, logger=HEALTH_LOGGER):
        drift = asyncio.run(check_clock_drift(_engine_now(server_now), threshold_seconds=120))

    assert drift is not None and drift.is_suspect
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "Clock drift against PostgreSQL exceeds the bound" in warnings[0]
    assert "direction=process_ahead" in warnings[0]


def test_a_matching_clock_reports_the_measured_drift(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=HEALTH_LOGGER):
        drift = asyncio.run(
            check_clock_drift(_engine_now(datetime.now(UTC)), threshold_seconds=120)
        )

    assert drift is not None and not drift.is_suspect
    assert "Startup clock check passed" in caplog.text


def test_an_unreachable_database_skips_the_check(caplog: pytest.LogCaptureFixture) -> None:
    error = OperationalError("SELECT now()", {}, Exception("down"))

    with caplog.at_level(logging.WARNING, logger=HEALTH_LOGGER):
        drift = asyncio.run(check_clock_drift(_engine_that_raises(error), threshold_seconds=120))

    assert drift is None
    assert "Startup clock check skipped | detail=OperationalError" in caplog.text


def test_name_resolution_failure_does_not_bubble_out_of_the_check(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failure = socket.gaierror(socket.EAI_NODATA, "No address associated with hostname")

    with caplog.at_level(logging.WARNING, logger=HEALTH_LOGGER):
        drift = asyncio.run(check_clock_drift(_engine_that_raises(failure), threshold_seconds=120))

    assert drift is None
    assert "Startup clock check skipped | detail=gaierror" in caplog.text
