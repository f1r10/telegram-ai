"""Azerbaijani presentation of the order intake questions, review page and plan.

Nothing here decides a number: the instant comes from the reminder parser, the plan from
:func:`office_assistant.orders.schedule.review_instants`, and both are shown exactly as
the application layer computed them (Master contract §0.4 and invariant 3).
"""

from datetime import datetime, timedelta

from office_assistant.application.orders import OrderView
from office_assistant.infrastructure.database.models import Order
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.reminder_format import when_azerbaijani
from office_assistant.orders import schedule, workflow

_FIELD_LABELS: dict[str, str] = {
    "deadline": messages.ORDERS_FIELD_DEADLINE,
    "lead_time": messages.ORDERS_FIELD_LEAD_TIME,
    "warnings": messages.ORDERS_FIELD_WARNINGS,
    "requester": messages.ORDERS_FIELD_REQUESTER,
    "description": messages.ORDERS_FIELD_DESCRIPTION,
    "notes": messages.ORDERS_FIELD_NOTES,
}
_FIELD_PROMPTS: dict[str, str] = {
    "deadline": messages.ORDERS_DEADLINE_PROMPT,
    "lead_time": messages.ORDERS_LEAD_PROMPT,
    "warnings": messages.ORDERS_WARNINGS_PROMPT,
    "requester": messages.ORDERS_REQUESTER_PROMPT,
    "description": messages.ORDERS_DESCRIPTION_PROMPT,
    "notes": messages.ORDERS_NOTES_PROMPT,
}


def label(field: str) -> str:
    return _FIELD_LABELS.get(field, field)


def question(field: str) -> str:
    """One intake question with its progress, so six answers are visibly enough."""

    fields = list(workflow.INTAKE_ORDER)
    index = fields.index(field) + 1 if field in fields else 1
    return (
        messages.ORDERS_INTAKE_TITLE.format(index=index, total=len(fields), field=label(field))
        + f"\n\n{_FIELD_PROMPTS.get(field, label(field))}"
    )


def reask(field: str, detail: str | None = None) -> str:
    """Refuse one answer in Azerbaijani, then repeat the question it belongs to."""

    refusal = messages.ORDERS_ANSWER_REJECTED.format(field=label(field))
    note = f" ({detail})" if detail else ""
    return f"{refusal}{note}\n\n{_FIELD_PROMPTS.get(field, label(field))}"


def review_text(view: OrderView) -> str:
    """Everything the user must see before the order becomes authoritative."""

    order = view.order
    zone = order.timezone or messages.ORDERS_UNKNOWN_ZONE
    deadline = (
        when_azerbaijani(order.deadline_utc, zone)
        if order.deadline_utc is not None
        else messages.ORDERS_UNKNOWN_VALUE
    )
    lines = [
        messages.ORDERS_REVIEW_TITLE,
        _line("deadline", deadline),
        _line("lead_time", duration(order.lead_time_minutes or 0)),
        _line("warnings", str(order.warning_count or 0)),
        _line("requester", order.requested_by or messages.ORDERS_UNKNOWN_VALUE),
        _line("description", order.description or messages.ORDERS_UNKNOWN_VALUE),
        _line("notes", order.notes or messages.ORDERS_NONE_VALUE),
    ]
    return "\n".join(lines)


def plan_text(view: OrderView, *, now_utc: datetime) -> str:
    """The schedule a confirmation is about to write, including what is already stale."""

    order = view.order
    if order.deadline_utc is None or not order.lead_time_minutes or not order.warning_count:
        return messages.ORDERS_PLAN_PENDING
    lead = int(order.lead_time_minutes)
    count = int(order.warning_count)
    try:
        instants = schedule.review_instants(
            deadline_utc=order.deadline_utc, lead_time_minutes=lead, warning_count=count
        )
    except schedule.InvalidSchedule as error:
        return messages.ORDERS_PLAN_INVALID.format(reason=str(error))
    zone = order.timezone or messages.ORDERS_UNKNOWN_ZONE
    future = [instant for instant in instants if instant > now_utc]
    interval = int((timedelta(minutes=lead) / len(instants)).total_seconds() // 60)
    body = messages.ORDERS_PLAN_SUMMARY.format(
        count=len(instants),
        lead=duration(lead),
        interval=duration(interval),
        deadline=when_azerbaijani(order.deadline_utc, zone),
    )
    if future:
        body += "\n" + messages.ORDERS_PLAN_FIRST_LAST.format(
            first=when_azerbaijani(future[0], zone),
            last=when_azerbaijani(future[-1], zone),
            planned=len(future),
        )
    stale = len(instants) - len(future)
    if stale:
        body += "\n" + messages.ORDERS_PLAN_STALE.format(count=stale)
    return body


def reminder_text(order: Order, *, index: int, total: int, now_utc: datetime) -> str:
    """One delivered review reminder, worded from the record as it stands now.

    The deadline and the remaining time are read from the order row and computed against
    the send-time clock rather than carried in the queue, so a delivery that was retried or
    delayed still states the truth the database holds (invariant 3).
    """

    zone = order.timezone or messages.ORDERS_UNKNOWN_ZONE
    lines = [
        messages.ORDERS_REMINDER_SUBJECT.format(order_id=order.id, index=index, total=total),
        "",
        _line("description", _reminder_excerpt(order.description)),
        _line("requester", _reminder_excerpt(order.requested_by)),
    ]
    deadline = order.deadline_utc
    if deadline is None:
        return "\n".join(lines + [_line("deadline", messages.ORDERS_UNKNOWN_VALUE)])
    seconds = (deadline - now_utc).total_seconds()
    minutes = int(abs(seconds) // 60)
    interval = duration(minutes) if minutes else messages.ORDERS_LESS_THAN_MINUTE
    lines.append(_line("deadline", when_azerbaijani(deadline, zone)))
    if seconds > 0:
        lines.append(
            messages.ORDERS_REVIEW_LINE.format(
                field=messages.ORDERS_FIELD_REMAINING, value=interval
            )
        )
    elif seconds == 0:
        lines.append(messages.ORDERS_REMINDER_DUE_NOW)
    else:
        lines.append(messages.ORDERS_REMINDER_PAST_DEADLINE.format(over=interval))
    return "\n".join(lines)


def _reminder_excerpt(value: str | None) -> str:
    # ASSUMPTION Q49: reserve 1,400 UTF-16 units for each free-text field so the
    # complete deadline/remaining-time block always fits a single Telegram message.
    value = value or messages.ORDERS_UNKNOWN_VALUE
    encoded = value.encode("utf-16-le")
    if len(encoded) <= 2800:
        return value
    excerpt = encoded[:2794].decode("utf-16-le", errors="ignore")
    return messages.ORDERS_TRUNCATED_VALUE.format(value=excerpt)


def duration(minutes: int) -> str:
    """«1 gün 12 saat 30 dəqiqə» from a minute count, largest unit first."""

    if minutes <= 0:
        return messages.ORDERS_NONE_VALUE
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts: list[str] = []
    for value, unit in ((days, "gün"), (hours, "saat"), (mins, "dəqiqə")):
        if value:
            parts.append(f"{value} {unit}")
    return " ".join(parts)


def _line(field: str, value: str) -> str:
    return messages.ORDERS_REVIEW_LINE.format(field=label(field), value=value)
