"""The review reminder formula, exactly as the owner contract states it (V2 §3.3).

``I = L / N`` and ``T(i) = D - L + (i * I)`` for ``i`` in ``0 .. N-1``: the first warning
falls at the moment the work should have started, the last one still inside the lead
time, and none of them at the deadline itself. Pure microseconds, no wall clock, so a
fake clock can reproduce an instant exactly.
"""

from datetime import UTC, datetime, timedelta

MAX_WARNINGS = 10
MIN_WARNINGS = 1


class InvalidSchedule(ValueError):
    """The given deadline, lead time or warning count cannot be scheduled."""


def review_instants(
    *, deadline_utc: datetime, lead_time_minutes: int, warning_count: int
) -> tuple[datetime, ...]:
    """Every planned review instant for one order, oldest first, in UTC.

    The deadline may arrive in the order's own zone; it is normalised to UTC so the
    spacing is the same either way. A naive datetime is refused because it names no
    instant, and the result is always UTC-aware because that is what the ledger stores.
    """

    deadline = _as_utc(deadline_utc)
    if lead_time_minutes <= 0:
        raise InvalidSchedule("lead time must be a positive number of minutes")
    if not MIN_WARNINGS <= warning_count <= MAX_WARNINGS:
        raise InvalidSchedule(f"warning count must be between {MIN_WARNINGS} and {MAX_WARNINGS}")
    lead_time = timedelta(minutes=lead_time_minutes)
    start = deadline - lead_time
    step = lead_time / warning_count
    return tuple(start + step * index for index in range(warning_count))


def idempotency_key(*, order_id: int, scheduled_for: datetime) -> str:
    """One delivery per order and instant, however often confirmation is retried."""

    return f"order:{order_id}:{_as_utc(scheduled_for).isoformat(timespec='microseconds')}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise InvalidSchedule("the deadline must carry a timezone")
    return value.astimezone(UTC)
