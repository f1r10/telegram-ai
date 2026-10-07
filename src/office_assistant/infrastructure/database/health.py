"""Typed PostgreSQL health check."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    """Result of a minimal database connectivity check."""

    status: Literal["healthy", "unavailable"]
    detail: str | None = None

    @property
    def is_healthy(self) -> bool:
        return self.status == "healthy"


async def check_database_health(engine: AsyncEngine) -> DatabaseHealth:
    """Execute SELECT 1 and return an explicit healthy/unavailable result."""

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        return DatabaseHealth(status="unavailable", detail=type(error).__name__)
    return DatabaseHealth(status="healthy")


@dataclass(frozen=True, slots=True)
class ClockDrift:
    """Process wall clock minus PostgreSQL wall clock, in seconds."""

    seconds: float
    threshold_seconds: float

    @property
    def is_suspect(self) -> bool:
        return abs(self.seconds) > self.threshold_seconds


async def measure_clock_drift(engine: AsyncEngine, *, threshold_seconds: float) -> ClockDrift:
    """Compare the process clock to the server clock that stamps now() defaults."""

    before = datetime.now(UTC)
    async with engine.connect() as connection:
        server_now = cast(datetime, (await connection.execute(text("SELECT now()"))).scalar_one())
    after = datetime.now(UTC)
    # The server samples the instant between the two reads, so that instant is the one to compare.
    return ClockDrift(
        seconds=(before + (after - before) / 2 - server_now).total_seconds(),
        threshold_seconds=threshold_seconds,
    )


async def check_clock_drift(engine: AsyncEngine, *, threshold_seconds: float) -> ClockDrift | None:
    """Log the startup clock comparison; a suspect clock warns instead of stopping the process."""

    try:
        drift = await measure_clock_drift(engine, threshold_seconds=threshold_seconds)
    except (SQLAlchemyError, OSError) as error:
        # A host that will not resolve surfaces here as socket.gaierror, not a SQLAlchemyError.
        logger.warning("Startup clock check skipped | detail=%s", type(error).__name__)
        return None
    if drift.is_suspect:
        logger.warning(
            "Clock drift against PostgreSQL exceeds the bound | drift_seconds=%.3f "
            "threshold_seconds=%.3f direction=%s",
            drift.seconds,
            drift.threshold_seconds,
            "process_ahead" if drift.seconds > 0 else "process_behind",
        )
    else:
        logger.info("Startup clock check passed | drift_seconds=%.3f", drift.seconds)
    return drift
