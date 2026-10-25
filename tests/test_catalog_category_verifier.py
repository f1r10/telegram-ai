"""Local category agreement gates wrong visual catalog matches without vendor calls."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import cast

from PIL import Image

from office_assistant.processing.catalog_category_verifier import (
    _TYPES,
    LocalCatalogCategoryVerifier,
    TypeAssessment,
    classify_photo_vector,
)
from office_assistant.processing.catalog_visual_candidates import (
    PdfImageSource,
    semantic_pdf_image_candidates,
)
from office_assistant.processing.semantic_visual import CLIP_DIMENSION, SemanticVisualSearch

# Test the internal zero-shot vocabulary without exposing it as public API.
# pyright: reportPrivateUsage=false


def _vector(category: int) -> list[float]:
    vector = [0.0] * CLIP_DIMENSION
    vector[category] = 1.0
    return vector


def _image(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (128, 128), color)
    with BytesIO() as out:
        image.save(out, "PNG")
        return out.getvalue()


class FakeTextEncoder:
    def embed(self, descriptions: list[str]) -> list[list[float]]:
        assert len(descriptions) == len(_TYPES)
        return [_vector(i) for i in range(len(descriptions))]


class FakeImageEncoder:
    """Query camera red, same product red; unrelated solar button blue."""

    def embed(self, paths: list[Path]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for path in paths:
            with Image.open(path) as image:
                pixel = image.getpixel((0, 0))
                assert isinstance(pixel, tuple)
                color = cast(tuple[int, int, int], pixel)
            if color[0] > 200:
                vectors.append(_vector(0))  # security camera
            elif color[1] > 200:
                vectors.append(_vector(1))  # wet-floor warning sign
            else:
                vectors.append(_vector(2))  # solar powered button
        return vectors


class FakeBroadImageEncoder:
    """Reproduce a permissive image-image model confusing solar products."""

    def embed(self, paths: list[Path]) -> list[list[float]]:
        del paths
        return [[1.0, *([0.0] * (CLIP_DIMENSION - 1))]]


def test_product_category_decision_is_independent_of_similarity() -> None:
    vectors = FakeTextEncoder().embed([name for _, name in _TYPES])
    result = classify_photo_vector(_vector(0), vectors)
    assert isinstance(result, TypeAssessment)
    assert result.type_name == "security_camera"
    assert result.margin > 0.9
    assert classify_photo_vector([0.0] * CLIP_DIMENSION, vectors).type_name is None


def test_camera_is_not_solar_push_button_but_wet_floor_is_wet_floor(tmp_path: Path) -> None:
    gate = LocalCatalogCategoryVerifier(
        tmp_path,
        image_encoder=cast(object, FakeImageEncoder()),  # type: ignore[arg-type]
        text_encoder=FakeTextEncoder(),
    )
    camera = _image((245, 5, 5))
    wet_floor = _image((4, 247, 3))
    solar_button = _image((4, 5, 248))
    assert gate.agree(camera, solar_button) is False
    assert gate.agree(camera, camera) is True
    assert gate.agree(wet_floor, wet_floor) is True
    assert gate.agree(camera, b"not an image") is None


class OverbroadFakeEncoder:
    def embed(self, paths: list[Path]) -> list[list[float]]:
        del paths
        return [[1.0] * CLIP_DIMENSION]


def test_semantic_photo_candidates_require_category_compatibility(tmp_path: Path) -> None:
    camera = _image((245, 5, 5))
    solar_button = _image((4, 5, 248))
    wet_floor = _image((4, 247, 3))
    for name, data in [("button.png", solar_button), ("wet.png", wet_floor)]:
        (tmp_path / name).write_bytes(data)
    sources = [
        PdfImageSource(
            1, "catalog.pdf", 3, 4,
            "button.png", sha256(solar_button).hexdigest(), source_file_id=7,
        ),
        PdfImageSource(
            2, "catalog.pdf", 2, 4,
            "wet.png", sha256(wet_floor).hexdigest(), source_file_id=7,
        ),
    ]
    semantic = SemanticVisualSearch(
        storage_root=tmp_path,
        model_cache_root=tmp_path / "cache",
        encoder=OverbroadFakeEncoder(),
        min_score=0.0,
        max_references=20,
    )
    gate = LocalCatalogCategoryVerifier(
        tmp_path,
        image_encoder=cast(object, FakeImageEncoder()),  # type: ignore[arg-type]
        text_encoder=FakeTextEncoder(),
    )
    # A deliberately nonspecific image-image model returns both candidates.
    # The independent category gate must reject them for an unknown camera.
    camera_hits, _, camera_unavailable = semantic_pdf_image_candidates(
        camera, sources, semantic, category_verifier=gate
    )
    assert not camera_unavailable
    assert camera_hits == ()
    wet_hits, _, wet_unavailable = semantic_pdf_image_candidates(
        wet_floor, sources, semantic, category_verifier=gate
    )
    assert not wet_unavailable
    assert len(wet_hits) == 1
    assert wet_hits[0].image_index == 4
    assert wet_hits[0].page == 2
