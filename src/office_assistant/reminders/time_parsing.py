"""Deterministic Azerbaijani reminder time-expression parser (SPEC §5).

Parses the golden cases from SPEC §5 and common variations into UTC datetimes.
The parser is pure: it receives an injected ``Clock`` and the user's IANA
timezone, performs no I/O, and never calls an LLM. Ambiguous expressions
return ``needs_clarification`` together with a suggested default that the
confirmation UX (Confirm/Edit/Cancel) must present before anything is saved.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from office_assistant.core.clock import Clock

DEFAULT_TIMEZONE = "Asia/Baku"

_WORD_NUM = r"on bir|on iki|bir|iki|üç|dörd|beş|altı|yeddi|səkkiz|doqquz|on"
_WORD_NUMBERS: dict[str, int] = {
    "bir": 1,
    "iki": 2,
    "üç": 3,
    "dörd": 4,
    "beş": 5,
    "altı": 6,
    "yeddi": 7,
    "səkkiz": 8,
    "doqquz": 9,
    "on": 10,
    "on bir": 11,
    "on iki": 12,
}

# Monday=0 .. Sunday=6. Longer names come first in the alternation so
# "cümə axşamı" wins over "cümə" and "bazar ertəsi" over "bazar".
_WEEKDAYS: tuple[tuple[str, int], ...] = (
    ("bazar ertəsi", 0),
    ("çərşənbə axşamı", 1),
    ("cümə axşamı", 3),
    ("çərşənbə", 2),
    ("cümə", 4),
    ("şənbə", 5),
    ("bazar", 6),
)
_WEEKDAY_PATTERN = "|".join(re.escape(name) for name, _ in _WEEKDAYS)
_WEEKDAY_INDEX: dict[str, int] = dict(_WEEKDAYS)

_PERIOD_DEFAULT_HOUR: dict[str, int] = {"səhər": 9, "gündüz": 14, "axşam": 20, "gecə": 22}


@dataclass(frozen=True)
class ReminderTimeResult:
    """Outcome of parsing one reminder request.

    ``due_at_utc`` is set only for unambiguous expressions. Ambiguous ones
    provide ``suggested_due_at_utc`` that must be explicitly confirmed.
    """

    due_at_utc: datetime | None
    suggested_due_at_utc: datetime | None
    needs_clarification: bool
    clarification_reason: str | None
    reminder_text: str


def _normalize(text: str) -> str:
    lowered = text.replace("İ", "i").replace("I", "ı").lower()
    return re.sub(r"\s+", " ", lowered.replace("\u00a0", " ")).strip()


class _SpanEater:
    """Extract regex matches sequentially, blanking consumed spans to prevent reuse."""

    def __init__(self, text: str) -> None:
        self.text = text

    def take(self, pattern: str) -> re.Match[str] | None:
        match = re.search(pattern, self.text)
        if match is None:
            return None
        start, end = match.span()
        self.text = self.text[:start] + " " * (end - start) + self.text[end:]
        return match


def _number(value: str) -> int | None:
    stripped = value.strip()
    if stripped.isdigit():
        return int(stripped)
    return _WORD_NUMBERS.get(stripped)


def _to_utc(local_naive: datetime, tz: ZoneInfo) -> datetime:
    return local_naive.replace(tzinfo=tz).astimezone(UTC)


def _month_day(now_local: datetime, day_number: int, *, next_month: bool) -> date | None:
    year, month = now_local.year, now_local.month
    if next_month:
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    else:
        first_attempt = _clamped(year, month, day_number)
        if first_attempt >= now_local.date():
            return first_attempt
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return _clamped(year, month, day_number)


def _clamped(year: int, month: int, day_number: int) -> date:
    return date(year, month, min(day_number, calendar.monthrange(year, month)[1]))


def _next_weekday(now_local: datetime, target_index: int, *, next_week: bool) -> date:
    if next_week:
        monday_next = now_local.date() - timedelta(days=now_local.weekday()) + timedelta(weeks=1)
        return monday_next + timedelta(days=target_index)
    offset = (target_index - now_local.weekday()) % 7
    if offset == 0:
        offset = 7
    return now_local.date() + timedelta(days=offset)


def _adjust_period(hour: int, period: str | None) -> int:
    if period in ("axşam", "gecə") and hour <= 11:
        return hour + 12
    if period == "səhər" and hour == 12:
        return 0
    return hour


def parse_reminder_time(
    message_text: str,
    *,
    clock: Clock,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> ReminderTimeResult:
    """Parse one Azerbaijani reminder message into a UTC due time."""
    try:
        tz = ZoneInfo(timezone_name)
    except (KeyError, ValueError) as error:  # ZoneInfoNotFoundError subclasses KeyError
        raise ValueError(f"Unknown IANA timezone: {timezone_name}") from error

    now_utc = clock.now_utc()
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    now_local = now_utc.astimezone(tz)
    eater = _SpanEater(_normalize(message_text))
    cleaned = lambda: _clean_text(eater.text)  # noqa: E731

    relative = eater.take(rf"\b(\d{{1,3}}|{_WORD_NUM})\s*(saat|dəqiqə|gün)(?:dan|dən)?\s+sonra\b")
    if relative is not None:
        count = _number(relative.group(1))
        if count is None or not 0 < count <= 365:
            return _unclear(cleaned(), "vaxt ifadəsi geçərsizdir")
        delta = (
            timedelta(hours=count)
            if relative.group(2) == "saat"
            else timedelta(minutes=count)
            if relative.group(2) == "dəqiqə"
            else timedelta(days=count)
        )
        return ReminderTimeResult(
            due_at_utc=now_utc + delta,
            suggested_due_at_utc=None,
            needs_clarification=False,
            clarification_reason=None,
            reminder_text=cleaned(),
        )

    day = eater.take(r"\b(birisigün|birisi gün|bu gün|bugün|sabah)\b")
    next_week = eater.take(r"\bgələn həftə\b") is not None
    next_month = eater.take(r"\bgələn ay\b") is not None
    weekday = eater.take(rf"\b({_WEEKDAY_PATTERN})\b")
    month_day = eater.take(r"\bay[ıi]n\s*(\d{1,2})(?:[- ]?(?:cı|çi|da|də|a|ə))?\b")
    period = eater.take(r"\b(səhər|axşam|gündüz|gecə)\b")
    colon_time = eater.take(r"\b(\d{1,2})[:.](\d{2})\b")
    saat_time = eater.take(rf"\bsaat\s*(\d{{1,2}}|{_WORD_NUM})(?:\s*-?(?:ı|i|da|də|a|ə|cı|çi))?\b")
    bare_time = eater.take(r"(?<![\d:.])(\d{1,2})\s*-(?:də|da)\b")

    hour: int | None = None
    minute = 0
    period_name = period.group(1) if period is not None else None
    if colon_time is not None:
        hour, minute = int(colon_time.group(1)), int(colon_time.group(2))
    elif saat_time is not None:
        hour = _number(saat_time.group(1))
    elif bare_time is not None:
        hour = int(bare_time.group(1))
    if hour is not None and (hour > 23 or minute > 59):
        hour = None
        minute = 0
    explicit_time = hour is not None
    if hour is not None:
        hour = _adjust_period(hour, period_name)

    target_date: date | None = None
    if day is not None:
        offset = {"sabah": 1, "birisigün": 2, "birisi gün": 2}.get(day.group(1), 0)
        target_date = now_local.date() + timedelta(days=offset)
    if weekday is not None:
        wd = _next_weekday(now_local, _WEEKDAY_INDEX[weekday.group(1)], next_week=next_week)
        target_date = wd if target_date is None else max(target_date, wd)
    if month_day is not None:
        md = _month_day(now_local, int(month_day.group(1)), next_month=next_month)
        if md is not None:
            target_date = md if target_date is None else max(target_date, md)

    if explicit_time and hour is not None:
        base = (
            datetime.combine(target_date, datetime.min.time())
            if target_date is not None
            else datetime.combine(now_local.date(), datetime.min.time())
        )
        local_naive = base.replace(hour=hour, minute=minute)
        due = _to_utc(local_naive, tz)
        if target_date is None and due <= now_utc + timedelta(seconds=30):
            due = _to_utc(local_naive + timedelta(days=1), tz)
        if due < now_utc:
            return ReminderTimeResult(
                due_at_utc=None,
                suggested_due_at_utc=due,
                needs_clarification=True,
                clarification_reason="vaxt keçmişə düşür",
                reminder_text=cleaned(),
            )
        return ReminderTimeResult(
            due_at_utc=due,
            suggested_due_at_utc=None,
            needs_clarification=False,
            clarification_reason=None,
            reminder_text=cleaned(),
        )

    default_hour = _PERIOD_DEFAULT_HOUR.get(period_name or "", 9)
    if target_date is not None or period_name is not None:
        base_date = target_date if target_date is not None else now_local.date()
        local_naive = datetime.combine(base_date, datetime.min.time()).replace(
            hour=default_hour, minute=0
        )
        suggested = _to_utc(local_naive, tz)
        if suggested <= now_utc:
            suggested = _to_utc(local_naive + timedelta(days=1), tz)
        reason = "vaxt ifadəsi qismidir" if target_date is not None else "dəqiq saat göstərilməyib"
        return ReminderTimeResult(
            due_at_utc=None,
            suggested_due_at_utc=suggested,
            needs_clarification=True,
            clarification_reason=reason,
            reminder_text=cleaned(),
        )

    return _unclear(cleaned(), "vaxt ifadəsi tapılmadı")


def _unclear(reminder_text: str, reason: str) -> ReminderTimeResult:
    return ReminderTimeResult(
        due_at_utc=None,
        suggested_due_at_utc=None,
        needs_clarification=True,
        clarification_reason=reason,
        reminder_text=reminder_text,
    )


def _clean_text(remaining: str) -> str:
    text = re.sub(
        r"\b(xatırlat\w*|xatırlad\w*|xatırlan\w*|xatirlat\w*|xatirlad\w*|xatirlan\w*"
        r"|unutma\w*|yadda saxla\w*)\b",
        " ",
        remaining,
    )
    text = re.sub(r"\b(məni|mənə|günü|gündə)\b", " ", text)
    text = re.sub(r"[.,;:!?]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    return text[0].upper() + text[1:]


_REMINDER_HINTS = ("xatırlat", "xatırlad", "xatırlan", "xatirlat", "xatirlad", "unutma")


def looks_like_reminder_request(text: str) -> bool:
    """Cheap deterministic intent rule (SPEC §6); an LLM is never consulted."""
    normalized = _normalize(text)
    return any(hint in normalized for hint in _REMINDER_HINTS)
