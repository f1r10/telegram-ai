"""Offline retrieval of source evidence from embedded PDF, DOCX and XLSX images."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageStat

from office_assistant.infrastructure.database.models import ImageAssetOrigin
from office_assistant.processing.document_visual_candidates import (
    DocumentImageSource,
    find_document_images,
    render_document_image_hits,
)
from office_assistant.processing.semantic_visual import SemanticVisualSearch


def picture(color: tuple[int, int, int], *, fmt: str = "PNG") -> bytes:
    image = Image.new("RGB", (160, 160), color)
    pen = ImageDraw.Draw(image)
    pen.ellipse((25, 15, 130, 140), outline="white", width=12)
    pen.line((15, 110, 145, 30), fill="black", width=7)
    stream = BytesIO()
    image.save(stream, format=fmt)
    return stream.getvalue()


class ColorEncoder:
    def embed(self, paths: list[Path]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for path in paths:
            with Image.open(path) as image:
                channels = ImageStat.Stat(image.convert("RGB")).mean[:3]
            vectors.append([*channels, *([0.0] * 509)])
        return vectors


def source(
    root: Path, number: int, origin: ImageAssetOrigin, locator: dict[str, object],
    body: bytes,
) -> DocumentImageSource:
    name = f"embedded-{number}.png"
    (root / name).write_bytes(body)
    filename = {
        ImageAssetOrigin.PDF_EMBEDDED: "catalog.pdf",
        ImageAssetOrigin.DOCX_EMBEDDED: "catalog.docx",
        ImageAssetOrigin.XLSX_EMBEDDED: "catalog.xlsx",
    }[origin]
    return DocumentImageSource(
        number, number + 100, filename, origin, locator, name, sha256(body).hexdigest()
    )


def test_embedded_sources_from_all_three_file_types_are_searchable(tmp_path: Path) -> None:
    files = [
        source(
            tmp_path, 1, ImageAssetOrigin.PDF_EMBEDDED,
            {"page": 2, "image_index": 1}, picture((230, 15, 15)),
        ),
        source(
            tmp_path, 2, ImageAssetOrigin.DOCX_EMBEDDED,
            {"table_index": 1, "row": 3, "column": 1}, picture((15, 230, 15)),
        ),
        source(
            tmp_path, 3, ImageAssetOrigin.XLSX_EMBEDDED,
            {"sheet": "Məhsullar", "anchor_cell": "D12"}, picture((15, 15, 230)),
        ),
    ]
    for item in files:
        image = (tmp_path / item.storage_key).read_bytes()
        hits, truncated, unavailable = find_document_images(
            image, files, tmp_path, quick_only=True
        )
        assert not truncated and not unavailable
        assert any(hit.source.occurrence_id == item.occurrence_id for hit in hits)
        text = render_document_image_hits(hits)
        assert item.filename in text
        assert "təsdiqlənməmiş" in text
        assert "anbar" in text
    xlsx, _, _ = find_document_images(
        (tmp_path / files[2].storage_key).read_bytes(), files, tmp_path, quick_only=True
    )
    assert "Məhsullar" in render_document_image_hits(xlsx)
    assert "D12" in render_document_image_hits(xlsx)


def test_distinct_phone_photo_retrieves_unverified_docx_semantic_candidate(
    tmp_path: Path,
) -> None:
    reference = source(
        tmp_path, 4, ImageAssetOrigin.DOCX_EMBEDDED,
        {"paragraph_index": 0}, picture((235, 23, 21)),
    )
    semantic = SemanticVisualSearch(
        storage_root=tmp_path,
        model_cache_root=tmp_path / "cache",
        encoder=ColorEncoder(),
        min_score=0.8,
        max_references=20,
    )
    query = picture((232, 25, 24), fmt="JPEG")
    hits, truncated, unavailable = find_document_images(
        query, [reference], tmp_path, semantic=semantic
    )
    assert not truncated and not unavailable
    assert hits and hits[0].source.occurrence_id == 4
    assert hits[0].match_type in {"near_duplicate", "semantic"}
    assert "abzas 1" in render_document_image_hits(hits)


def test_tampered_embedded_image_does_not_match_old_digest(tmp_path: Path) -> None:
    old = picture((240, 15, 15))
    item = source(
        tmp_path, 5, ImageAssetOrigin.XLSX_EMBEDDED,
        {"sheet": "A", "anchor_cell": "A1"}, old,
    )
    (tmp_path / item.storage_key).write_bytes(picture((15, 15, 240)))
    hits, _, _ = find_document_images(old, [item], tmp_path, quick_only=True)
    assert not hits


def test_empty_sources_and_invalid_limits_are_safe(tmp_path: Path) -> None:
    matches, truncated, unavailable = find_document_images(
        picture((60, 110, 180)), [], tmp_path
    )
    assert matches == () and not truncated and not unavailable
