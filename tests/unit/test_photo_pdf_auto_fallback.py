"""Ordinary Telegram photo queries search active PDF/DOCX/XLSX evidence."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HANDLERS = ROOT / "src/office_assistant/integrations/telegram/handlers.py"


def _function(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(HANDLERS.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Function {name} not found")


def test_ordinary_photo_search_checks_embedded_documents_after_sku_lookup() -> None:
    lookup = _function("_handle_exact_photo_lookup")
    calls = [
        call
        for call in ast.walk(lookup)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_reply_document_catalog_candidates"
    ]
    assert len(calls) == 1
    assert any(
        isinstance(node, ast.If) and calls[0] in ast.walk(node.test) for node in ast.walk(lookup)
    )


def test_embedded_source_lookup_is_bounded_with_semantic_candidates() -> None:
    fallback = _function("_reply_document_catalog_candidates")
    source = ast.get_source_segment(HANDLERS.read_text(encoding="utf-8"), fallback)
    assert source is not None
    assert "active_document_images" in source
    assert "find_document_images" in source
    assert "semantic=services.semantic_visual" in source
    assert "timeout=8.0" in source
    assert "issue_catalog_ticket" in source


def test_explicit_pdf_lookup_retains_page_enrichment() -> None:
    fallback = _function("_reply_pdf_catalog_candidates")
    source = ast.get_source_segment(HANDLERS.read_text(encoding="utf-8"), fallback)
    assert source is not None
    assert "read_pdf_visual_details" in source
    assert "semantic_pdf_image_candidates" in source
