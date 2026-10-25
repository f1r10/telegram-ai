"""Regression gate for operator-facing image lookup latency budgets.

These bounds apply to the optional, non-authoritative similarity stages;
they do not measure Telegram transfer, database or event-loop overhead.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _timeouts(path: str, function_name: str) -> list[float]:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name
    )
    values: list[float] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "asyncio"
            and node.func.attr == "wait_for"
        ):
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "timeout"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, (int, float))
            ):
                values.append(float(keyword.value.value))
    return values


def test_telegram_optional_image_lookup_has_bounded_stages() -> None:
    budgets = _timeouts(
        "src/office_assistant/integrations/telegram/handlers.py",
        "_handle_exact_photo_lookup",
    )
    assert 2.0 in budgets
    assert 4.0 in budgets
    assert sum(value for value in budgets if value in (2.0, 4.0)) <= 6.0


def test_miniapp_optional_semantic_photo_lookup_fails_fast() -> None:
    budgets = _timeouts(
        "src/office_assistant/miniapp/photo_lookup.py",
        "lookup",
    )
    assert 5.0 in budgets
    assert max(budgets) <= 5.0
