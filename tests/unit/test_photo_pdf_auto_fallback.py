"""Read-only PDF visual evidence fallback stays bounded and non-authoritative."""

import ast
from pathlib import Path

HANDLERS = (
    Path(__file__).resolve().parents[2]
    / "src/office_assistant/integrations/telegram/handlers.py"
)


def _function(name: str) -> ast.AsyncFunctionDef:
    source = HANDLERS.read_text(encoding="utf-8")
    tree = ast.parse(source)
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )


def test_ordinary_photo_search_also_checks_pdf_after_failed_product_match() -> None:
    lookup = _function("_handle_exact_photo_lookup")
    quick_calls = [
        call
        for call in ast.walk(lookup)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_reply_pdf_catalog_candidates"
        and any(
            keyword.arg == "quick_only"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in call.keywords
        )
    ]
    assert len(quick_calls) == 1
    assert any(
        isinstance(node, ast.If) and quick_calls[0] in ast.walk(node.test)
        for node in ast.walk(lookup)
    )


def test_quick_pdf_fallback_is_bounded_and_skips_semantic_model() -> None:
    fallback = _function("_reply_pdf_catalog_candidates")
    source = ast.get_source_segment(
        HANDLERS.read_text(encoding="utf-8"), fallback
    )
    assert source is not None
    assert "timeout=2.0 if quick_only else 5.0" in source
    assert "not quick_only and services.semantic_visual is not None" in source
