"""The review-schedule formula and its validation, tested without a database.

Owner contract V2 §3.3 states the interval as ``I = L / N`` with the first warning at
``D - L``; the worked example (48 hours, 5 warnings) is the acceptance value, so it is
asserted in exact microseconds rather than approximately.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from office_assistant.orders import schedule

DEADLINE = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


def test_48_hour_window_with_five_warnings_matches_the_contract_example() -> None:
    instants = schedule.review_instants(
        deadline_utc=DEADLINE, lead_time_minutes=48 * 60, warning_count=5
    )

    assert len(instants) == 5
    interval = instants[1] - instants[0]
    assert interval == timedelta(hours=9, minutes=36)
    assert instants[0] == DEADLINE - timedelta(hours=48)
    assert instants == tuple(
        DEADLINE - timedelta(hours=48) + interval * index for index in range(5)
    )


def test_the_last_warning_is_one_interval_before_the_deadline_not_at_it() -> None:
    instants = schedule.review_instants(
        deadline_utc=DEADLINE, lead_time_minutes=60, warning_count=4
    )

    assert instants[-1] == DEADLINE - timedelta(minutes=15)
    assert all(instant < DEADLINE for instant in instants)


def test_a_single_warning_falls_at_the_start_of_the_lead_window() -> None:
    instants = schedule.review_instants(
        deadline_utc=DEADLINE, lead_time_minutes=120, warning_count=1
    )

    assert instants == (DEADLINE - timedelta(hours=2),)


def test_a_non_dividing_interval_stays_evenly_spaced_and_sorted() -> None:
    instants = schedule.review_instants(
        deadline_utc=DEADLINE, lead_time_minutes=100, warning_count=3
    )

    assert list(instants) == sorted(instants)
    first = instants[0]
    assert instants[1] - first == instants[2] - instants[1]
    assert first == DEADLINE - timedelta(minutes=100)


def test_a_localised_deadline_is_converted_without_moving_the_instant() -> None:
    """The same moment written in Asia/Baku must produce the same plan, not a shifted one."""

    baku = ZoneInfo("Asia/Baku")
    localised = datetime(2026, 11, 15, 16, 0, tzinfo=baku)

    assert localised == DEADLINE
    plain = schedule.review_instants(
        deadline_utc=DEADLINE, lead_time_minutes=48 * 60, warning_count=5
    )
    shifted = schedule.review_instants(
        deadline_utc=localised, lead_time_minutes=48 * 60, warning_count=5
    )

    assert shifted == tuple(instant.astimezone(UTC) for instant in plain)
    assert all(
        instant.tzinfo is not None and instant.utcoffset() == timedelta(0) for instant in plain
    )


@pytest.mark.parametrize(
    ("lead_time_minutes", "warning_count", "reason"),
    [
        (0, 3, "lead time"),
        (-5, 3, "lead time"),
        (60, 0, "warning count"),
        (60, 11, "warning count"),
        (60, -1, "warning count"),
    ],
)
def test_impossible_plans_are_refused(
    lead_time_minutes: int, warning_count: int, reason: str
) -> None:
    with pytest.raises(schedule.InvalidSchedule) as caught:
        schedule.review_instants(
            deadline_utc=DEADLINE,
            lead_time_minutes=lead_time_minutes,
            warning_count=warning_count,
        )
    assert reason in str(caught.value)


def test_a_deadline_without_a_timezone_is_refused_because_it_has_no_instant() -> None:
    naive = datetime(2026, 11, 15, 12, 0)

    with pytest.raises(schedule.InvalidSchedule, match="timezone"):
        schedule.review_instants(deadline_utc=naive, lead_time_minutes=60, warning_count=2)


def test_the_idempotency_key_is_stable_per_order_and_instant() -> None:
    instant = DEADLINE - timedelta(hours=9, minutes=36)

    first = schedule.idempotency_key(order_id=42, scheduled_for=instant)
    second = schedule.idempotency_key(order_id=42, scheduled_for=instant)

    assert first == second
    assert first.startswith("order:42:")
    assert schedule.idempotency_key(order_id=43, scheduled_for=instant) != first


def test_the_idempotency_key_distinguishes_instants_that_share_a_minute() -> None:
    early = schedule.idempotency_key(order_id=1, scheduled_for=DEADLINE - timedelta(seconds=301))
    late = schedule.idempotency_key(order_id=1, scheduled_for=DEADLINE - timedelta(seconds=300))

    assert early != late
