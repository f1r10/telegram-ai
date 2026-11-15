# This test reads the worker's sanitized-error helper to pin the exact message an operator sees.
# pyright: reportPrivateUsage=false
"""Phase 2 M9: the parse budget is opt-in, killable and honest about its platform limits.

The boundary is an availability control, not a sandbox. These tests pin the four properties the
milestone promises: defaults change nothing, the gate is ``gi_document_intelligence_enabled``, an
over-budget parse is killed with a sanitized code instead of hanging, and a platform that cannot cap
memory says so instead of pretending.
"""

import time
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from office_assistant.core.settings import Settings
from office_assistant.processing.budgets import (
    PARSE_MEMORY_LIMIT,
    PARSE_TIME_LIMIT,
    ParseAborted,
    ParseBudget,
    memory_limit_supported,
    parse_budget_from_settings,
    run_bounded,
)
from office_assistant.processing.worker_service import _bounded_error, parse_with_budget


def _identity(value: str) -> str:
    return value


def _sleep(seconds: float) -> str:
    time.sleep(seconds)
    return "done"


def _allocate(megabytes: int) -> str:
    block = bytearray(megabytes * 1024 * 1024)
    return str(len(block))


def _boom() -> str:
    raise ValueError("parser exploded")


class _SlowParser:
    """A parser whose work exceeds any small budget, picklable for the child process."""

    name = "slow"
    version = "0"

    def __init__(self) -> None:
        self.calls = 0

    def parse(self, path: Path, *, source_file_id: int, media_type: str) -> Any:
        self.calls += 1
        time.sleep(10)
        raise AssertionError("the parent must not wait for this")


class _FastParser:
    name = "fast"
    version = "0"

    def __init__(self) -> None:
        self.calls = 0

    def parse(self, path: Path, *, source_file_id: int, media_type: str) -> Any:
        self.calls += 1
        return f"{path.name}:{source_file_id}:{media_type}"


# --- configuration ---------------------------------------------------------------------------


def test_defaults_keep_todays_unbounded_parse() -> None:
    settings = Settings()

    assert settings.parse_wall_clock_seconds is None
    assert settings.parse_memory_limit_mb is None
    assert parse_budget_from_settings(settings) is None


def test_the_boundary_requires_the_document_intelligence_flag() -> None:
    settings = Settings(
        gi_document_intelligence_enabled=False,
        parse_wall_clock_seconds=30,
        parse_memory_limit_mb=512,
    )

    assert parse_budget_from_settings(settings) is None


def test_a_configured_budget_is_built_when_opted_in() -> None:
    settings = Settings(
        gi_document_intelligence_enabled=True,
        parse_wall_clock_seconds=12.5,
        parse_memory_limit_mb=512,
    )

    assert parse_budget_from_settings(settings) == ParseBudget(
        wall_clock_seconds=12.5, memory_limit_mb=512
    )


def test_opted_in_without_a_ceiling_is_still_unbounded() -> None:
    settings = Settings(gi_document_intelligence_enabled=True)

    assert parse_budget_from_settings(settings) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"wall_clock_seconds": 0},
        {"wall_clock_seconds": -1},
        {"memory_limit_mb": 32},
    ],
)
def test_a_budget_that_enforces_nothing_is_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        ParseBudget(**kwargs)


def test_memory_support_is_reported_truthfully() -> None:
    import importlib.util

    assert memory_limit_supported() is (importlib.util.find_spec("resource") is not None)


# --- the boundary ----------------------------------------------------------------------------


def test_a_bounded_call_returns_its_result() -> None:
    result = run_bounded(partial(_identity, "ok"), ParseBudget(wall_clock_seconds=30))

    assert result == "ok"


def test_a_slow_parse_is_killed_at_the_wall_clock() -> None:
    started = time.monotonic()

    with pytest.raises(ParseAborted) as caught:
        run_bounded(partial(_sleep, 60), ParseBudget(wall_clock_seconds=0.5))

    assert caught.value.code == PARSE_TIME_LIMIT
    # The parent returned on the budget, not when the child would have finished.
    assert time.monotonic() - started < 20


def test_a_child_failure_is_reported_as_a_sanitized_code() -> None:
    with pytest.raises(ParseAborted) as caught:
        run_bounded(_boom, ParseBudget(wall_clock_seconds=30))

    assert caught.value.code == "ValueError"
    assert "parser exploded" not in str(caught.value)
    assert _bounded_error(caught.value) == "ValueError"


@pytest.mark.skipif(
    not memory_limit_supported(),
    reason="this platform cannot cap a child's address space (no RLIMIT_AS)",
)
def test_a_memory_hungry_parse_is_stopped_at_the_ceiling() -> None:
    with pytest.raises(ParseAborted) as caught:
        run_bounded(
            partial(_allocate, 512),
            ParseBudget(wall_clock_seconds=60, memory_limit_mb=128),
        )

    assert caught.value.code == PARSE_MEMORY_LIMIT


# --- worker integration ----------------------------------------------------------------------


def test_no_budget_runs_the_parser_in_process(tmp_path: Path) -> None:
    parser = _FastParser()

    result = parse_with_budget(
        parser,
        tmp_path / "doc.pdf",
        source_file_id=7,
        media_type="application/pdf",
        budget=None,
    )

    assert result == "doc.pdf:7:application/pdf"
    assert parser.calls == 1


def test_a_configured_budget_aborts_a_slow_parser(tmp_path: Path) -> None:
    parser = _SlowParser()

    with pytest.raises(ParseAborted) as caught:
        parse_with_budget(
            parser,
            tmp_path / "slow.pdf",
            source_file_id=8,
            media_type="application/pdf",
            budget=ParseBudget(wall_clock_seconds=0.5),
        )

    assert caught.value.code == PARSE_TIME_LIMIT
    # The work happened in a child, so the parent-side counter is untouched.
    assert parser.calls == 0
