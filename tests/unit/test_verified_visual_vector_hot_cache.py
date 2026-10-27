"""Hot vector cache cannot override active relations, source hashes or storage fences."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest
from PIL import Image

from office_assistant.processing.semantic_visual import (
    CLIP_DIMENSION,
    SemanticReference,
    SemanticVisualSearch,
)


def photo(red: int) -> bytes:
    stream = BytesIO()
    Image.new("RGB", (80, 80), (red, 10, 10)).save(stream, format="PNG")
    return stream.getvalue()


class RedEncoder:
    def embed(self, paths: list[Path]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for path in paths:
            with Image.open(path) as image:
                pixel = image.getpixel((0, 0))
                assert isinstance(pixel, tuple)
                red = cast(tuple[int, int, int], pixel)[0] / 255
                vectors.append([red, 1.0 - red] + [0.0] * (CLIP_DIMENSION - 2))
        return vectors


def test_unchanged_verified_vectors_skip_repeated_file_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "storage"
    root.mkdir()
    reference_bytes = photo(220)
    (root / "approved.png").write_bytes(reference_bytes)
    ref = SemanticReference("WATCH7", "approved.png", sha256(reference_bytes).hexdigest())
    search = SemanticVisualSearch(
        storage_root=root,
        model_cache_root=tmp_path / "cache",
        encoder=RedEncoder(),
        min_score=0.90,
    )
    verified: list[str] = []
    original = search._verified_reference  # pyright: ignore[reportPrivateUsage]

    def check(reference: SemanticReference) -> bytes | None:
        verified.append(reference.storage_key)
        return original(reference)

    monkeypatch.setattr(search, "_verified_reference", check)
    query = photo(222)
    assert [hit.product_code for hit in search.search(query, [ref]).candidates] == ["WATCH7"]
    assert [hit.product_code for hit in search.search(query, [ref]).candidates] == ["WATCH7"]
    assert verified == ["approved.png"]
    # Caller-provided DB relations are always authoritative: revocation is instant.
    assert search.search(query, []).candidates == ()
    # A valid re-approval may reuse the already verified vector, not authorization.
    assert [hit.product_code for hit in search.search(query, [ref]).candidates] == ["WATCH7"]


def test_changed_reference_rechecks_digest_and_abstains(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    root.mkdir()
    ref_bytes = photo(220)
    target = root / "approved.png"
    target.write_bytes(ref_bytes)
    ref = SemanticReference("WATCH7", "approved.png", sha256(ref_bytes).hexdigest())
    search = SemanticVisualSearch(
        storage_root=root,
        model_cache_root=tmp_path / "cache",
        encoder=RedEncoder(),
        min_score=0.90,
    )
    assert search.search(photo(222), [ref]).candidates
    # Change same path; mtime/ctime and/or size invalidate the cached vector.
    target.write_bytes(photo(0))
    assert search.search(photo(222), [ref]).candidates == ()
    # Even when the source vanishes, stale memory must not be trusted.
    target.unlink()
    assert search.search(photo(222), [ref]).candidates == ()


def test_retargeted_symlink_cannot_reuse_previous_vector(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    root.mkdir()
    inside = root / "approved.png"
    body = photo(220)
    inside.write_bytes(body)
    target = root / "alias.png"
    target.symlink_to(inside)
    ref = SemanticReference("WATCH7", "alias.png", sha256(body).hexdigest())
    search = SemanticVisualSearch(
        storage_root=root,
        model_cache_root=tmp_path / "cache",
        encoder=RedEncoder(),
        min_score=0.90,
    )
    assert search.search(photo(222), [ref]).candidates
    target.unlink()
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(body)
    target.symlink_to(outside)
    assert search.search(photo(222), [ref]).candidates == ()
