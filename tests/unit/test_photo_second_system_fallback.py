"""The second (document/semantic) search system must run when the primary DINO ANN finds nothing.

Regression for the owner's report that an image which exists inside a document is
never found once the primary ANN is warm: an empty ANN result used to short-circuit
the near-duplicate/cross-angle/document fallback. These gates pin the dispatch, the
lower unverified fallback band, and the per-occurrence de-duplication that keeps the
bounded scan honest.
"""

import ast
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest
from PIL import Image

from office_assistant.processing.document_visual_candidates import (
    DOCUMENT_FALLBACK_MIN_SCORE,
)
from office_assistant.processing.semantic_visual import (
    CLIP_DIMENSION,
    SemanticReference,
    SemanticVisualSearch,
)
from office_assistant.processing.visual_ann import MIN_CANDIDATE_SCORE

ROOT = Path(__file__).resolve().parents[2]
HANDLERS = ROOT / "src/office_assistant/integrations/telegram/handlers.py"
MINIAPP = ROOT / "src/office_assistant/miniapp/photo_lookup.py"
DOCUMENTS = ROOT / "src/office_assistant/processing/document_visual_candidates.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _async_function(path: Path, name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(_source(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Async function {name} not found in {path.name}")


def _call_names(node: ast.AST) -> set[str]:
    found: set[str] = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            if isinstance(call.func, ast.Name):
                found.add(call.func.id)
            elif isinstance(call.func, ast.Attribute):
                found.add(call.func.attr)
    return found


def _indexed_page_is_guarded_by_nonempty_ann(path: Path) -> None:
    """The ANN page is only returned when the index produced a candidate."""

    lookup = _async_function(path, "_handle_exact_photo_lookup")
    guarded = False
    for if_node in ast.walk(lookup):
        if not isinstance(if_node, ast.If):
            continue
        body_calls = _call_names(ast.Module(body=list(if_node.body), type_ignores=[]))
        if "_send_indexed_visual_page" not in body_calls:
            continue
        test_src = ast.get_source_segment(_source(path), if_node.test) or ""
        if "indexed.products" in test_src and "indexed.documents" in test_src:
            guarded = True
    assert guarded, "empty ANN must not short-circuit the second search system"


def test_telegram_empty_ann_falls_through_to_second_system() -> None:
    _indexed_page_is_guarded_by_nonempty_ann(HANDLERS)


def test_miniapp_empty_ann_falls_through_to_second_system() -> None:
    lookup = _async_function(MINIAPP, "lookup")
    # The old early "not_found / dino_qdrant_no_candidate" return is gone.
    source = ast.get_source_segment(_source(MINIAPP), lookup) or ""
    assert "dino_qdrant_no_candidate" not in source
    # The ANN return is guarded by a non-empty candidate test.
    guarded = False
    for if_node in ast.walk(lookup):
        if not isinstance(if_node, ast.If):
            continue
        test_src = ast.get_source_segment(_source(MINIAPP), if_node.test) or ""
        if "indexed.products" in test_src and "indexed.documents" in test_src:
            guarded = True
    assert guarded, "Mini App empty ANN must not short-circuit the second search system"


def test_document_fallback_band_is_below_the_ann_near_duplicate_gate() -> None:
    assert 0.5 <= DOCUMENT_FALLBACK_MIN_SCORE < MIN_CANDIDATE_SCORE


def _find_document_images_semantic_threads_min_score() -> None:
    tree = ast.parse(_source(DOCUMENTS))
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "find_document_images"
    )
    args = {arg.arg for arg in func.args.kwonlyargs}
    assert "semantic_min_score" in args
    source = ast.get_source_segment(_source(DOCUMENTS), func) or ""
    assert "min_score=semantic_min_score" in source


def test_find_document_images_threads_the_fallback_band() -> None:
    _find_document_images_semantic_threads_min_score()


def test_telegram_document_scan_requests_the_fallback_band() -> None:
    reply = _async_function(HANDLERS, "_reply_document_catalog_candidates")
    source = ast.get_source_segment(_source(HANDLERS), reply) or ""
    assert "semantic_min_score=DOCUMENT_FALLBACK_MIN_SCORE" in source


def test_active_document_images_deduplicates_per_occurrence() -> None:
    tree = ast.parse(_source(DOCUMENTS))
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "active_document_images"
    )
    source = ast.get_source_segment(_source(DOCUMENTS), func) or ""
    assert ".distinct(ImageAssetOccurrence.id)" in source


def _photo(red: int) -> bytes:
    image = Image.new("RGB", (72, 72), (red, 12, 12))
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class _ColorEncoder:
    def embed(self, paths: list[Path]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for path in paths:
            with Image.open(path) as picture:
                pixel = cast(tuple[int, int, int], picture.convert("RGB").getpixel((0, 0)))
            unit = pixel[0] / 255
            vectors.append([unit, 1 - unit] + [0.0] * (CLIP_DIMENSION - 2))
        return vectors


def test_search_min_score_override_lowers_only_that_call(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    root.mkdir()
    body = _photo(200)
    (root / "sign.png").write_bytes(body)
    references = [SemanticReference("SIGN", "sign.png", sha256(body).hexdigest())]
    search = SemanticVisualSearch(
        storage_root=root,
        model_cache_root=tmp_path / "models",
        encoder=_ColorEncoder(),
        min_score=0.9,
    )
    query = _photo(120)  # cosine ~0.84 against the reference
    assert search.search(query, references).candidates == ()
    surfaced = search.search(query, references, min_score=0.5)
    assert [candidate.product_code for candidate in surfaced.candidates] == ["SIGN"]
    # The instance default band is unchanged for later calls.
    assert search.search(query, references).candidates == ()
    with pytest.raises(ValueError):
        search.search(query, references, min_score=2.0)
