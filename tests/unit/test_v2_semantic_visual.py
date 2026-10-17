"""Offline semantic visual candidates remain non-authoritative and fail closed."""

import asyncio
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest
from PIL import Image

from office_assistant.core.settings import Settings
from office_assistant.processing.semantic_visual import (
    CLIP_DIMENSION,
    LocalClipEncoder,
    SemanticReference,
    SemanticVisualSearch,
    balanced_references,
    unit_vector,
)
from office_assistant.scripts import prepare_visual_references


def photo(red: int, *, fmt: str = "PNG") -> bytes:
    image = Image.new("RGB", (72, 72), (red, 12, 12))
    out = BytesIO()
    image.save(out, format=fmt)
    return out.getvalue()


class ColorEncoder:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, paths: list[Path]) -> list[list[float]]:
        self.calls += len(paths)
        vectors: list[list[float]] = []
        for path in paths:
            with Image.open(path) as picture:
                pixel = picture.convert("RGB").getpixel((0, 0))
                assert isinstance(pixel, tuple)
                red = cast(tuple[int, int, int], pixel)[0]
            unit = red / 255
            vectors.append([unit, 1 - unit] + [0.0] * (CLIP_DIMENSION - 2))
        return vectors


class OfflineFailure:
    def embed(self, paths: list[Path]) -> list[list[float]]:
        del paths
        raise ValueError("model unavailable (private local path)")


def test_cross_angle_candidate_is_ranked_but_not_declared_identity(tmp_path: Path) -> None:
    root, cache = tmp_path / "storage", tmp_path / "models"
    root.mkdir()
    red = photo(230, fmt="JPEG")
    blue = photo(15)
    (root / "front.jpg").write_bytes(red)
    (root / "side.jpg").write_bytes(photo(226))
    (root / "other.png").write_bytes(blue)
    refs = [
        SemanticReference("KA45", "front.jpg", sha256(red).hexdigest()),
        SemanticReference("KA45", "side.jpg", sha256((root / "side.jpg").read_bytes()).hexdigest()),
        SemanticReference("KA46", "other.png", sha256(blue).hexdigest()),
    ]
    encoder = ColorEncoder()
    search = SemanticVisualSearch(
        storage_root=root, model_cache_root=cache, encoder=encoder, min_score=0.85
    )
    result = search.search(photo(234), refs)
    assert not result.unavailable
    assert not result.incomplete
    assert result.inspected == 3
    assert [candidate.product_code for candidate in result.candidates] == ["KA45"]
    assert all(0.85 <= candidate.score <= 1.0 for candidate in result.candidates)
    first_calls = encoder.calls
    assert first_calls == 4
    # After caching, no local inference or model download is needed.
    result2 = search.search(photo(234), refs)
    assert result2 == result
    assert encoder.calls == first_calls + 1
    digest = sha256(photo(234)).hexdigest()
    query_cache = cache / "visual-embeddings" / "clip-vit-b32-v1" / digest[:2]
    assert not (query_cache / (digest + ".json")).exists()
    # The active-reference query has removed this confirmed photo: stale
    # cached feature files may exist but are never treated as active authority.
    assert search.search(photo(234), []).candidates == ()


def test_unknown_and_uncertain_images_abstain(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    root.mkdir()
    original = photo(250)
    (root / "red.png").write_bytes(original)
    refs = [SemanticReference("KA45", "red.png", sha256(original).hexdigest())]
    search = SemanticVisualSearch(
        storage_root=root,
        model_cache_root=tmp_path / "models",
        encoder=ColorEncoder(),
        min_score=0.9,
    )
    result = search.search(photo(0), refs)
    assert result.candidates == ()
    assert not result.unavailable
    assert search.search(b"not an image", refs).candidates == ()
    missing = [SemanticReference("KA45", "missing.jpg", "a" * 64)]
    assert search.search(photo(250), missing).candidates == ()


def test_changed_digest_and_symlink_escape_never_score(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    root.mkdir()
    (root / "safe.jpg").write_bytes(photo(255))
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(photo(255))
    (root / "link.jpg").symlink_to(outside)
    refs = [
        SemanticReference("MUTATED", "safe.jpg", "0" * 64),
        SemanticReference("LEAK", "link.jpg", "0" * 64),
        SemanticReference("TRAVERSAL", "../outside.jpg", "0" * 64),
    ]
    search = SemanticVisualSearch(
        storage_root=root,
        model_cache_root=tmp_path / "model",
        encoder=ColorEncoder(),
        min_score=0.0,
    )
    assert search.search(photo(255), refs).candidates == ()


def test_unavailable_model_degrades_instead_of_guessing(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    root.mkdir()
    search = SemanticVisualSearch(
        storage_root=root, model_cache_root=tmp_path / "cache", encoder=OfflineFailure()
    )
    result = search.search(photo(240), [])
    assert result.unavailable
    assert result.candidates == ()


def test_reference_limits_and_validation(tmp_path: Path) -> None:
    values = (
        [0.0] * CLIP_DIMENSION,
        [1.0, float("nan")] + [0.0] * (CLIP_DIMENSION - 2),
        [2.0],
    )
    for value in values:
        assert unit_vector(value) is None
    root = tmp_path / "root"
    root.mkdir()
    refs = [SemanticReference(f"KA{index}", "bad.png", "a" * 64) for index in range(10)]
    search = SemanticVisualSearch(
        storage_root=root,
        model_cache_root=tmp_path / "models",
        encoder=ColorEncoder(),
        max_references=2,
    )
    result = search.search(photo(220), refs)
    assert result.incomplete
    assert result.candidates == ()
    with pytest.raises(ValueError):
        SemanticVisualSearch(storage_root=root, model_cache_root=tmp_path, version="../bad")
    with pytest.raises(ValueError):
        SemanticVisualSearch(storage_root=root, model_cache_root=tmp_path, min_score=2.0)
    with pytest.raises(ValueError):
        search.search(photo(220), refs, limit=0)


def test_local_clip_import_is_late_and_provisioned_only(tmp_path: Path) -> None:
    # Constructing a disabled or pre-provisioned search never downloads a model.
    instance = LocalClipEncoder(tmp_path)
    assert instance.cache_root == tmp_path


def test_explicit_reference_preparation_is_idempotent_and_safe(tmp_path: Path) -> None:
    root, cache = tmp_path / "references", tmp_path / "cache"
    root.mkdir()
    body = photo(230)
    (root / "approved.png").write_bytes(body)
    reference = SemanticReference("KA45", "approved.png", sha256(body).hexdigest())
    encoder = ColorEncoder()
    search = SemanticVisualSearch(
        storage_root=root,
        model_cache_root=cache,
        encoder=encoder,
    )
    warmed = search.warm_references([reference])
    assert warmed.warmed == 1
    assert warmed.inspected == 1
    assert not warmed.unavailable
    assert search.warm_references([reference]).warmed == 1
    assert encoder.calls == 1
    (root / "approved.png").write_bytes(photo(0))
    assert search.warm_references([reference]).skipped == 1
    assert search.warm_references([]).inspected == 0


def test_missing_local_model_is_visible_to_reference_preparation(tmp_path: Path) -> None:
    root = tmp_path / "references"
    root.mkdir()
    body = photo(220)
    (root / "item.png").write_bytes(body)
    search = SemanticVisualSearch(
        storage_root=root,
        model_cache_root=tmp_path / "cache",
        encoder=OfflineFailure(),
    )
    result = search.warm_references(
        [SemanticReference("KA45", "item.png", sha256(body).hexdigest())]
    )
    assert result.unavailable
    assert result.warmed == 0


def test_operator_dryrun_never_starts_clip_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_approved(settings: Settings, *, limit: int) -> list[SemanticReference]:
        del settings, limit
        return [SemanticReference("KA45", "image.jpg", "0" * 64)]

    monkeypatch.setattr(prepare_visual_references, "approved_references", fake_approved)
    result = asyncio.run(
        prepare_visual_references.prepare_references(
            Settings(file_storage_path=str(tmp_path)),
            apply=False,
            limit=80,
        )
    )
    assert result.inspected == 1
    assert result.prepared == 0
    assert not result.unavailable
    with pytest.raises(ValueError):
        asyncio.run(
            prepare_visual_references.prepare_references(
                Settings(file_storage_path=str(tmp_path)),
                apply=True,
                limit=0,
            )
        )


def test_many_angles_of_one_sku_cannot_hide_other_products_from_reference_limit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    red = photo(230)
    blue = photo(15)
    references: list[SemanticReference] = []
    for index in range(12):
        # Distinct storage paths model separate confirmed photos of one SKU.
        name = f"red-{index}.png"
        (root / name).write_bytes(red)
        references.append(SemanticReference("KA45", name, sha256(red).hexdigest()))
    (root / "blue.png").write_bytes(blue)
    references.append(SemanticReference("KA46", "blue.png", sha256(blue).hexdigest()))
    selected = balanced_references(references, limit=2)
    assert [item.product_code for item in selected] == ["KA45", "KA46"]
    assert balanced_references(references, limit=2) == selected
    assert len(balanced_references(references, limit=20)) == 13
    search = SemanticVisualSearch(
        storage_root=root,
        model_cache_root=tmp_path / "models",
        encoder=ColorEncoder(),
        min_score=0.98,
        max_references=2,
    )
    result = search.search(blue, references, limit=2)
    assert result.incomplete
    assert [item.product_code for item in result.candidates] == ["KA46"]


def test_empty_reference_sampler_and_invalid_limit_fail_closed() -> None:
    assert balanced_references([], limit=5) == []
    with pytest.raises(ValueError):
        balanced_references([], limit=0)
    with pytest.raises(ValueError):
        balanced_references([], limit=501)
