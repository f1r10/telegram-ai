"""Deterministic answers for the two numeric questions of order intake (V2 §3.1).

The deadline question reuses :mod:`office_assistant.reminders.time_parsing`; these two
have no shared rule because nothing else in the product asks for a lead window or a
warning count. Like the reminder parser this module is pure text in, number out, never
calls a model, and returns ``None`` instead of guessing: the caller asks again.

ASSUMPTION (docs/OPEN-QUESTIONS.md Q48): a bare number answers the question that was
just asked, so ``48`` to «48 saat?» means 48 hours and ``48`` to «neçə dəfə?» means 48
warnings. Only the unit-less form depends on that reading; an explicit unit always wins.
"""

import re

from office_assistant.reminders.time_parsing import WORD_NUM, WORD_NUMBERS, normalize_text

_UNIT_MINUTES: dict[str, int] = {"dəqiqə": 1, "saat": 60, "gün": 1440}
# Longer units first so "saat" cannot be shortened by a partial alternation, and the
# optional dash covers the typed form "2-saat" as well as the spoken "2 saat".
_UNIT_PATTERN = "|".join(re.escape(unit) for unit in _UNIT_MINUTES)
_QUANTITY = rf"\b(\d{{1,4}}|{WORD_NUM})[- ]?({_UNIT_PATTERN})\b"
_BARE_NUMBER = re.compile(r"\A\s*(\d{1,4})\s*\Z")
_FIRST_NUMBER = rf"\b(\d{{1,3}}|{WORD_NUM})\b"
_SKIP_ANSWERS = frozenset({"keç", "kec", "geç", "gec", "yoxdur", "yox", "-", "boş"})


def parse_lead_time_minutes(text: str) -> int | None:
    """Minutes covered by «48 saat», «2 gün», «1 gün 12 saat», «90 dəqiqə» or a bare number.

    Every quantity in the answer counts, so a compound window adds up instead of being
    read as its first part only.
    """

    normalized = normalize_text(text)
    total = 0
    matched = False
    for amount, unit in re.findall(_QUANTITY, normalized):
        value = _number(amount)
        if value is None:
            return None
        total += value * _UNIT_MINUTES[unit]
        matched = True
    if matched:
        return total or None
    bare = _BARE_NUMBER.match(normalized)
    return int(bare.group(1)) * 60 if bare is not None else None


def parse_warning_count(text: str) -> int | None:
    """The first number in the answer; the range check stays with the service."""

    normalized = normalize_text(text)
    match = re.search(_FIRST_NUMBER, normalized)
    if match is None:
        return None
    return _number(match.group(1))


def is_skip_answer(text: str) -> bool:
    """«keç»-type replies: the optional question is deliberately left unanswered."""

    normalized = normalize_text(text)
    if not normalized:
        return True
    return normalized in _SKIP_ANSWERS or normalized.split(" ", 1)[0] in _SKIP_ANSWERS


def _number(value: str) -> int | None:
    return int(value) if value.isdigit() else WORD_NUMBERS.get(value)
