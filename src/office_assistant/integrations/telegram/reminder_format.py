"""Azerbaijani rendering of reminder datetimes."""

from datetime import datetime
from zoneinfo import ZoneInfo

from office_assistant.integrations.telegram import messages


def when_azerbaijani(due_at_utc: datetime, timezone_name: str) -> str:
    """Render an UTC instant in the user's IANA timezone with an Azerbaijani weekday."""
    local = due_at_utc.astimezone(ZoneInfo(timezone_name))
    weekday = messages.WEEKDAY_NAMES[local.weekday()]
    return f"{weekday}, {local.strftime('%d.%m.%Y %H:%M')} ({timezone_name})"
