"""Offline fake-encoder tests: distinct photos can retrieve PDF picture evidence."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageStat

from office_assistant.processing.catalog_visual_candidates import (
    PdfImageSource,
    PdfPageCandidate,
    render_pdf_image_candidates,
    semantic_pdf_image_candidates,
)
from office_assistant.processing.semantic_visual import SemanticVisualSearch


class FakeColorEncoder:
    """Deterministic stand-in for local CLIP, never network or inference."""

    def embed(self, paths: list[Path]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for path in paths:
            with Image.open(path) as img:
                rgb = img.convert("RGB")
                averages = ImageStat.Stat(rgb).mean[:3]
            vectors.append([*averages, *([0.0] * 509)])
        return vectors


def _image(color: tuple[int, int, int], fmt: str) -> bytes:
    image = Image.new("RGB", (160, 160), color)
    with BytesIO() as buffer:
        image.save(buffer, format=fmt, quality=88)
        return buffer.getvalue()


def _ref(tmp_path: Path, name: str, data: bytes, occurrence: int) -> PdfImageSource:
    path = tmp_path / name
    path.write_bytes(data)
    return PdfImageSource(
        occurrence,
        "synthetic-catalog.pdf",
        1,
        occurrence,
        name,
        sha256(data).hexdigest(),
        source_file_id=9,
    )


def test_distinct_photo_formats_retrieve_same_visual_pdf_source(tmp_path: Path) -> None:
    # Same product semantics, DIFFERENT image encoding/bytes (no SHA match).
    red_pdf = _image((240, 30, 24), "PNG")
    query_photo = _image((238, 34, 27), "JPEG")
    blue_pdf = _image((24, 30, 240), "PNG")
    assert sha256(query_photo).digest() != sha256(red_pdf).digest()
    refs = [
        _ref(tmp_path, "red.png", red_pdf, 1),
        _ref(tmp_path, "blue.png", blue_pdf, 2),
    ]
    semantic = SemanticVisualSearch(
        storage_root=tmp_path,
        model_cache_root=tmp_path / "model",
        encoder=FakeColorEncoder(),
        min_score=0.80,
        max_references=10,
    )
    candidates, incomplete, unavailable = semantic_pdf_image_candidates(query_photo, refs, semantic)
    assert not unavailable and not incomplete
    assert candidates
    assert candidates[0].occurrence_id == 1
    assert candidates[0].match_type == "semantic"
    assert candidates[0].source_file_id == 9
    assert "təsdiqlənməmiş namizəd" in render_pdf_image_candidates(candidates)


def test_semantic_retrieval_does_not_confirm_unrelated_photos(tmp_path: Path) -> None:
    red_pdf = _image((240, 30, 24), "PNG")
    green_query = _image((20, 245, 20), "JPEG")
    source = _ref(tmp_path, "red.png", red_pdf, 1)
    service = SemanticVisualSearch(
        storage_root=tmp_path,
        model_cache_root=tmp_path / "model",
        encoder=FakeColorEncoder(),
        min_score=0.90,
        max_references=10,
    )
    result, _, unavailable = semantic_pdf_image_candidates(green_query, [source], service)
    assert not unavailable
    assert result == ()


def test_pdf_candidate_never_displays_internal_draw_order_as_visual_number() -> None:
    candidate = PdfPageCandidate(
        "catalog.pdf", 3, 5, source_file_id=12, occurrence_id=43, visual_row=1
    )
    text = render_pdf_image_candidates((candidate,))
    assert "yuxarıdan 1-ci məhsul şəkli" in text
    assert "şəkil 5" not in text
    without_geometry = render_pdf_image_candidates((PdfPageCandidate("catalog.pdf", 3, 5),))
    assert "şəkil 5" not in without_geometry
