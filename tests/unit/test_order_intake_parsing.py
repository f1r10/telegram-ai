"""The two numeric intake answers, tested at the parser instead of through the wizard.

``orders.intake_parsing`` is pure text in, number out: it never calls a model and returns
``None`` rather than guessing, because the caller asks again (V2 §3.1). The adapter only
ever typed the digit forms, so the spelled-out numbers, every skip word and the
``total or None`` edge are pinned here — they are the forms the owner actually types.
"""

import pytest

from office_assistant.orders.intake_parsing import (
    is_skip_answer,
    parse_lead_time_minutes,
    parse_warning_count,
)


@pytest.mark.parametrize(
    ("answer", "minutes"),
    [
        ("48 saat", 2880),
        ("2 gün", 2880),
        ("90 dəqiqə", 90),
        ("1 gün 12 saat", 2160),
        ("2-saat", 120),
        ("48", 2880),
    ],
)
def test_a_written_lead_window_becomes_minutes(answer: str, minutes: int) -> None:
    assert parse_lead_time_minutes(answer) == minutes


@pytest.mark.parametrize(
    ("answer", "minutes"),
    [
        ("iki saat", 120),
        ("beş dəqiqə", 5),
        ("səkkiz saat", 480),
        ("on bir saat", 660),
        ("on iki saat", 720),
        ("bir gün iki saat", 1560),
        ("BİR GÜN", 1440),
        ("  üç  saat  ", 180),
    ],
)
def test_a_spelled_lead_window_becomes_minutes(answer: str, minutes: int) -> None:
    """ORDERS-WORKFLOW.md promises the word forms, not only the digits."""

    assert parse_lead_time_minutes(answer) == minutes


@pytest.mark.parametrize("answer", ["sabah saat 10-da", "tez", "", "   ", "saat", "bir neçə saat"])
def test_an_answer_without_a_lead_window_is_refused_not_guessed(answer: str) -> None:
    assert parse_lead_time_minutes(answer) is None


def test_a_zero_window_is_not_a_window_and_the_owner_is_asked_again() -> None:
    """``total or None`` — otherwise «0 saat» would read as a completed answer."""

    assert parse_lead_time_minutes("0 saat") is None


@pytest.mark.parametrize(
    ("answer", "count"),
    [
        ("5", 5),
        ("beş", 5),
        ("on bir", 11),
        ("üç dəfə", 3),
        ("  7  ", 7),
        ("5 xəbərdarlıq et", 5),
    ],
)
def test_the_warning_count_reads_the_first_number(answer: str, count: int) -> None:
    assert parse_warning_count(answer) == count


def test_an_out_of_range_count_still_parses_because_the_service_owns_the_range() -> None:
    """The parser must not silently disagree with the documented 1..10 rejection."""

    assert parse_warning_count("15") == 15


@pytest.mark.parametrize("answer", ["yoxdur", "çox", "", "beşinci", "sabah"])
def test_an_answer_without_a_number_is_refused_not_guessed(answer: str) -> None:
    """``beşinci`` must not be harvested as 5: the word boundary is the whole rule."""

    assert parse_warning_count(answer) is None


@pytest.mark.parametrize(
    "answer",
    [
        "keç",
        "kec",
        "geç",
        "gec",
        "yox",
        "yoxdur",
        "boş",
        "-",
        "KEÇ",
        "keç ər məni",
        "",
        "   ",
    ],
)
def test_a_skip_reply_leaves_the_optional_question_unanswered(answer: str) -> None:
    """An empty reply skips too, and so does a reply that opens with a skip word."""

    assert is_skip_answer(answer) is True


@pytest.mark.parametrize("answer", ["bəli", "5", "qeyd yoxdur var", "hmm bir dəqiqə"])
def test_a_reply_that_is_not_a_skip_reaches_the_draft(answer: str) -> None:
    """A skip word in the middle of a sentence does not make the sentence a skip."""

    assert is_skip_answer(answer) is False


def test_the_skip_rule_reads_the_whole_reply_or_its_first_word_and_nothing_else() -> None:
    """Q48 (5): the rule is a prefix test, so punctuation and a trailing clause both matter.

    «keç,» is not a skip word and «yoxdur demə» opens with one; both are pinned here so a
    future change to this rule is a decision, not an accident.
    """

    assert is_skip_answer("keç, boş ver") is False
    assert is_skip_answer("yoxdur demə") is True
