"""Deterministic clock used by reminder parsing and dispatch."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Read-only current time provider. Always returns UTC-aware datetimes."""

    def now_utc(self) -> datetime: ...


class SystemClock:
    """Production clock backed by the system wall clock."""

    def now_utc(self) -> datetime:
        return datetime.now(UTC)
