"""Typed PostgreSQL health check."""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine


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
