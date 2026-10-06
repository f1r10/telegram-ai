"""Unit tests for the deterministic Azerbaijani reminder time parser (SPEC §5 golden cases)."""

from datetime import UTC, datetime

import pytest

from office_assistant.reminders.time_parsing import (
    DEFAULT_TIMEZONE,
    ReminderTimeResult,
    parse_reminder_time,
)


class FixedClock:
    """Fake Clock returning a constant UTC instant."""

    def __init__(self, now: datetime) -> None:
        assert now.tzinfo is not None
        self._now = now

    def now_utc(self) -> datetime:
        return self._now


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


# Wednesday 2026-10-07, 06:30 Asia/Baku.
WED_MORNING = FixedClock(utc("2026-10-07T02:30:00"))


def parse(
    message: str, clock: FixedClock = WED_MORNING, tz: str = DEFAULT_TIMEZONE
) -> ReminderTimeResult:
    return parse_reminder_time(message, clock=clock, timezone_name=tz)


def test_tomorrow_at_hour() -> None:
    result = parse("sabah saat 9-da Əliyə zəng etməyi xatırlat")
    assert result.due_at_utc == utc("2026-10-08T05:00:00")  # 09:00 Baku
    assert not result.needs_clarification
    assert result.reminder_text == "Əliyə zəng etməyi"


def test_day_after_tomorrow_evening_hour() -> None:
    result = parse("birisigün axşam 7-də görüşü xatırlat")
    assert result.due_at_utc == utc("2026-10-09T15:00:00")  # 19:00 Baku
    assert not result.needs_clarification


def test_relative_hours() -> None:
    result = parse("2 saatdan sonra ictimai qeysi xatırlat")
    assert result.due_at_utc == utc("2026-10-07T04:30:00")
    assert not result.needs_clarification


def test_relative_minutes() -> None:
    result = parse("30 dəqiqədən sonra")
    assert result.due_at_utc == utc("2026-10-07T03:00:00")


def test_word_number_hours() -> None:
    result = parse("iki saatdan sonra iclas")
    assert result.due_at_utc == utc("2026-10-07T04:30:00")


def test_next_week_weekday_needs_clarification() -> None:
    result = parse("gələn həftə cümə günü hesabatı xatırlat")
    assert result.due_at_utc is None
    assert result.needs_clarification
    assert result.suggested_due_at_utc == utc("2026-10-16T05:00:00")  # Friday 09:00 default
    assert result.clarification_reason is not None


def test_day_of_month_needs_clarification() -> None:
    result = parse("ayın 15-də ödənişi xatırlat")
    assert result.due_at_utc is None
    assert result.needs_clarification
    assert result.suggested_due_at_utc == utc("2026-10-15T05:00:00")  # the 15th, 09:00 default


def test_today_colon_time() -> None:
    result = parse("bu gün 18:30-a görüş xatırlatmağı unutma")
    assert result.due_at_utc == utc("2026-10-07T14:30:00")  # 18:30 Baku
    assert not result.needs_clarification


def test_weekday_with_period_word_needs_clarification() -> None:
    result = parse("bazar ertəsi səhər poçtu yoxlamağı xatırlat")
    assert result.due_at_utc is None
    assert result.needs_clarification
    assert result.suggested_due_at_utc == utc("2026-10-12T05:00:00")  # Monday 09:00


def test_bare_hour_without_period_rolls_to_tomorrow() -> None:
    result = parse("saat 5-də qaçışı xatırlat")
    # 05:00 Baku already passed at 06:30 -> next day.
    assert result.due_at_utc == utc("2026-10-08T01:00:00")


def test_explicit_time_in_past_needs_clarification() -> None:
    result = parse("bu gün saat 5-də")
    assert result.due_at_utc is None
    assert result.needs_clarification
    assert result.suggested_due_at_utc == utc("2026-10-07T01:00:00")
    assert result.clarification_reason == "vaxt keçmişə düşür"


def test_evening_bare_hour_uses_period() -> None:
    result = parse("sabah axşam 9-da konserti xatırlat")
    assert result.due_at_utc == utc("2026-10-08T17:00:00")  # 21:00 Baku


def test_no_time_expression() -> None:
    result = parse("necəsən")
    assert result.due_at_utc is None
    assert result.suggested_due_at_utc is None
    assert result.needs_clarification
    assert result.clarification_reason == "vaxt ifadəsi tapılmadı"
    assert result.reminder_text == "Necəsən"


def test_dst_europe_london_before_clock_change() -> None:
    # 2026-10-22: London is on BST (UTC+1).
    clock = FixedClock(utc("2026-10-22T05:30:00"))  # 06:30 local
    result = parse("sabah saat 9-da", clock=clock, tz="Europe/London")
    assert result.due_at_utc == utc("2026-10-23T08:00:00")  # 09:00 BST


def test_dst_europe_london_after_clock_change() -> None:
    # 2026-10-26: London is on GMT (UTC+0) after the switch.
    clock = FixedClock(utc("2026-10-26T06:30:00"))
    result = parse("sabah saat 9-da", clock=clock, tz="Europe/London")
    assert result.due_at_utc == utc("2026-10-27T09:00:00")  # 09:00 GMT


def test_results_are_utc_aware() -> None:
    result = parse("sabah saat 9-da")
    assert result.due_at_utc is not None
    assert result.due_at_utc.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


def test_invalid_timezone_raises() -> None:
    with pytest.raises(ValueError, match="Unknown IANA timezone"):
        parse("sabah saat 9-da", tz="Baku/Azerbaijan_X")
