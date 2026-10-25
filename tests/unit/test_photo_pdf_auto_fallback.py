"""PDF image evidence is included in ordinary photo lookup when no approved SKU photo exists.

The quick fallback must not invoke semantic inference or promote PDF illustrations
to authoritative product identity.
"""

import ast
from pathlib import Path

HANDLERS = Path(__file__).resolve().parents[2] / "src/office_assistant/integrations/telegram/handlers.py"


def _function(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(HANDLERS.read_text(encoding="utf-8"))
    return next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)


def test_unlabelled_photo_can_search_pdf_image_evidence_without_approved_sku() -> None:
    lookup = _function("_handle_exact_photo_lookup")
    quick_calls = [
        call
        for call in ast.walk(lookup)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_reply_pdf_catalog_candidates"
        and any(k.arg == "quick_only" and isinstance(k.value, ast.Constant) and k.value.value is True for k in call.keywords)
    ]
    assert len(quick_calls) == 1
    conditional = next(
        node
        for node in ast.walk(lookup)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.BoolOp)
        and any(isinstance(value, ast.UnaryOp) and isinstance(value.operand, ast.Name) and value.operand.id == "references" for value in node.test.values)
        and any(call in ast.walk(node.test) for call in quick_calls)
    )
    assert conditional is not None


def test_quick_pdf_fallback_is_bounded_and_does_not_run_semantic_model() -> None:
    fallback = _function("_reply_pdf_catalog_candidates")
    source = ast.get_source_segment(HANDLERS.read_text(encoding="utf-8"), fallback)
    assert source is not None
    assert "timeout=2.0 if quick_only else 5.0" in source
    assert "not quick_only and services.semantic_visual is not None" in source
