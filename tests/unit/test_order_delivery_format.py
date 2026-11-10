"""The one delivered review reminder, worded from the record and nothing else.

``reminder_text`` is the only place the message is assembled, so these cases pin what the
owner contract asks for: the order is named by its number, the deadline is stated in the
order's own zone, and the remaining time is a deterministic difference against the clock the
caller supplies — never a number the model guessed (invariant 3).
"""

from datetime import UTC, datetime, timedelta

import pytest

from office_assistant.infrastructure.database.models import Order, OrderState
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.orders_format import reminder_text

DEADLINE = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def _order(**overrides: object) -> Order:
    fields: dict[str, object] = {
        "id": 42,
        "requested_by": "Aysel müəllim",
        "description": "Konferans zalı üçün kabellər",
        "timezone": "Asia/Baku",
        "deadline_utc": DEADLINE,
        "lead_time_minutes": 48 * 60,
        "warning_count": 5,
        "state": OrderState.ACTIVE.value,
    }
    fields.update(overrides)
    return Order(**fields)  # type: ignore[arg-type]


def test_the_subject_names_the_order_and_says_which_warning_of_how_many_it_is() -> None:
    text = reminder_text(_order(), index=3, total=5, now_utc=datetime(2026, 11, 12, tzinfo=UTC))

    assert text.startswith(messages.ORDERS_REMINDER_SUBJECT.format(order_id=42, index=3, total=5))
    assert "Konferans zalı üçün kabellər" in text
    assert "Aysel müəllim" in text


def test_the_deadline_is_rendered_in_the_order_zone_with_an_azerbaijani_weekday() -> None:
    text = reminder_text(_order(), index=1, total=5, now_utc=datetime(2026, 11, 12, tzinfo=UTC))
    weekday = messages.WEEKDAY_NAMES[DEADLINE.weekday()]

    assert f"{weekday}, 13.11.2026 13:00 (Asia/Baku)" in text


def test_the_remaining_time_is_the_difference_to_the_clock_the_caller_supplied() -> None:
    text = reminder_text(_order(), index=1, total=5, now_utc=datetime(2026, 11, 12, tzinfo=UTC))

    # 2026-11-12 00:00 UTC to 2026-11-13 09:00 UTC is 33 hours.
    assert f"{messages.ORDERS_FIELD_REMAINING}: 1 gün 9 saat" in text


def test_a_send_after_the_deadline_reports_the_overrun_instead_of_a_negative_wait() -> None:
    late = DEADLINE.replace(hour=10, minute=30)
    text = reminder_text(_order(), index=5, total=5, now_utc=late)

    assert messages.ORDERS_FIELD_REMAINING not in text
    assert messages.ORDERS_REMINDER_PAST_DEADLINE.format(over="1 saat 30 dəqiqə") in text


def test_the_exact_deadline_is_never_worded_as_no_time_at_all() -> None:
    text = reminder_text(_order(), index=5, total=5, now_utc=DEADLINE)

    assert messages.ORDERS_REMINDER_DUE_NOW in text
    assert messages.ORDERS_NONE_VALUE not in text


def test_an_incomplete_record_degrades_to_the_documented_unknown_values() -> None:
    order = _order(deadline_utc=None, requested_by=None, description="")
    text = reminder_text(order, index=1, total=5, now_utc=datetime(2026, 11, 12, tzinfo=UTC))

    assert messages.ORDERS_UNKNOWN_VALUE in text
    assert f"{messages.ORDERS_FIELD_DEADLINE}: {messages.ORDERS_UNKNOWN_VALUE}" in text
    assert messages.ORDERS_FIELD_REMAINING not in text


def test_an_unnamed_zone_is_falled_back_to_instead_of_raising() -> None:
    order = _order(timezone="")
    text = reminder_text(order, index=1, total=5, now_utc=datetime(2026, 11, 12, tzinfo=UTC))

    assert f"({messages.ORDERS_UNKNOWN_ZONE})" in text


@pytest.mark.parametrize("character", ["ə", chr(0x10400)])
def test_long_confirmed_fields_fit_telegram_without_losing_the_deadline(character: str) -> None:
    order = _order(description=character * 4000, requested_by=character * 4000)
    text = reminder_text(order, index=10, total=10, now_utc=DEADLINE)
    assert len(text.encode("utf-16-le")) // 2 <= 4096
    assert messages.ORDERS_FIELD_DEADLINE in text
    assert messages.ORDERS_REMINDER_DUE_NOW in text
    assert "..." in text


@pytest.mark.parametrize("seconds", [-59, -1, 1, 59])
def test_subminute_remaining_time_is_not_confused_with_overdue(seconds: int) -> None:
    text = reminder_text(_order(), index=1, total=1, now_utc=DEADLINE - timedelta(seconds=seconds))
    assert messages.ORDERS_LESS_THAN_MINUTE in text
    assert (messages.ORDERS_FIELD_REMAINING in text) == (seconds > 0)
    if seconds < 0:
        assert (
            messages.ORDERS_REMINDER_PAST_DEADLINE.format(over=messages.ORDERS_LESS_THAN_MINUTE)
            in text
        )
