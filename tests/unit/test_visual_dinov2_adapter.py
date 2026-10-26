"""DINOv2 opt-in integrates with the existing authorized photo search boundary.

No PyTorch/model weights are loaded by these deterministic unit tests.
"""

from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image

from office_assistant.processing.semantic_visual import (
    CLIP_DIMENSION,
    DINO_DIMENSION,
    DINO_MODEL_VERSION,
    SemanticReference,
    SemanticVisualSearch,
    unit_vector,
)
from office_assistant.processing.visual_dinov2 import LocalDinoMeanEncoder


class OfflineDinoStub:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, paths: list[Path]) -> list[list[float]]:
        result: list[list[float]] = []
        for path in paths:
            self.calls += 1
            with Image.open(path) as picture:
                rgb = picture.convert("RGB")
                pixel = rgb.getpixel((0, 0))
            assert isinstance(pixel, tuple)
            red = int(pixel[0]) / 255
            result.append([red, 1.0 - red] + [0.0] * (DINO_DIMENSION - 2))
        return result


def photo(red: int) -> bytes:
    image = Image.new("RGB", (80, 80), (red, 12, 12))
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def test_dino_encoder_is_lazy_and_does_not_require_torch_when_disabled(tmp_path: Path) -> None:
    service = SemanticVisualSearch(
        storage_root=tmp_path,
        model_cache_root=tmp_path,
        version=DINO_MODEL_VERSION,
    )
    assert isinstance(service.encoder, LocalDinoMeanEncoder)
    assert service.dimension == DINO_DIMENSION
    assert service.encoder._model is None  # pyright: ignore[reportPrivateUsage]


def test_dino_mean_vectors_use_separate_384_cache_and_rank_active_skus(
    tmp_path: Path,
) -> None:
    storage = tmp_path / "photos"
    storage.mkdir()
    body = photo(230)
    (storage / "watch.png").write_bytes(body)
    refs = [SemanticReference("WATCH44", "watch.png", sha256(body).hexdigest())]
    encoder = OfflineDinoStub()
    service = SemanticVisualSearch(
        storage_root=storage,
        model_cache_root=tmp_path / "models",
        version=DINO_MODEL_VERSION,
        encoder=encoder,
        min_score=0.9,
    )
    first = service.search(photo(228), refs)
    assert [item.product_code for item in first.candidates] == ["WATCH44"]
    assert first.inspected == 1
    assert encoder.calls == 2
    second = service.search(photo(228), refs)
    assert second.candidates == first.candidates
    assert encoder.calls == 3

    digest = sha256(body).hexdigest()
    cache = (
        tmp_path / "models" / "visual-embeddings" / DINO_MODEL_VERSION
        / digest[:2] / (digest + ".json")
    )
    assert cache.is_file()
    assert not (
        tmp_path / "models" / "visual-embeddings" / "clip-vit-b32-v1"
        / digest[:2] / (digest + ".json")
    ).exists()
    # Revoke all active references: stale DINO cache is not proof of identity.
    assert service.search(photo(228), []).candidates == ()


def test_dimensions_cannot_mix_clip_and_dino() -> None:
    assert unit_vector([1.0] * DINO_DIMENSION) is None
    assert unit_vector([1.0] * DINO_DIMENSION, dimension=DINO_DIMENSION) is not None
    assert unit_vector([1.0] * CLIP_DIMENSION, dimension=DINO_DIMENSION) is None


def test_dino_wrong_dimension_abstains(tmp_path: Path) -> None:
    class WrongEncoder:
        def embed(self, paths: list[Path]) -> list[list[float]]:
            return [[1.0] * CLIP_DIMENSION for _ in paths]

    service = SemanticVisualSearch(
        storage_root=tmp_path,
        model_cache_root=tmp_path,
        encoder=WrongEncoder(),
        version=DINO_MODEL_VERSION,
    )
    assert service.search(photo(230), []).candidates == ()
