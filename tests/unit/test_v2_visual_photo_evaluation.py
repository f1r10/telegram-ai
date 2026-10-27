"""Offline phone-photo evaluation covers angles, negatives, collisions and privacy."""

import asyncio
import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest
from PIL import Image, ImageDraw

from office_assistant.core.settings import Settings
from office_assistant.processing.image_similarity import VisualCandidate, shortlist
from office_assistant.processing.semantic_visual import (
    CLIP_DIMENSION,
    SemanticReference,
    SemanticVisualSearch,
)
from office_assistant.scripts import evaluate_visual_photos
from office_assistant.scripts.benchmark_visual import TruthCase
from office_assistant.scripts.evaluate_visual_photos import (
    PhotoCase,
    evaluate_photos,
    read_photo_manifest,
    summarize_latency_ms,
)


def photo(color: tuple[int, int, int], *, shape: str) -> bytes:
    image = Image.new("RGB", (128, 128), color)
    pencil = ImageDraw.Draw(image)
    if shape == "front":
        pencil.rectangle((23, 17, 103, 108), fill="white")
        pencil.rectangle((36, 34, 91, 97), fill="black")
        pencil.line((5, 9, 111, 121), fill="yellow", width=4)
    elif shape == "side":
        pencil.polygon([(4, 30), (122, 12), (77, 109)], fill="white")
        pencil.ellipse((13, 43, 56, 121), fill="black")
        pencil.line((116, 5, 20, 106), fill="yellow", width=7)
    else:
        pencil.ellipse((28, 12, 111, 116), fill="white")
        pencil.line((8, 16, 109, 18), fill="black", width=15)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class ColorEncoder:
    """Test-only stand-in: use the uniform upper-left background color."""

    def embed(self, paths: list[Path]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for path in paths:
            with Image.open(path) as image:
                pixel = image.getpixel((0, 0))
                assert isinstance(pixel, tuple)
                rgb = cast(tuple[int, int, int], pixel)
                vectors.append([float(channel) for channel in rgb] + [0.0] * (CLIP_DIMENSION - 3))
        return vectors


class ModelUnavailable:
    def embed(self, paths: list[Path]) -> list[list[float]]:
        del paths
        raise RuntimeError("Offline model unavailable")


def scenario(tmp_path: Path) -> tuple[list[PhotoCase], list[SemanticReference], Path]:
    root = tmp_path / "references"
    query_root = tmp_path / "phone-photos"
    root.mkdir()
    query_root.mkdir()
    approved: list[tuple[str, str, bytes]] = [
        ("KA45", "a-front.png", photo((228, 20, 20), shape="front")),
        ("KA45", "a-side.png", photo((228, 20, 20), shape="side")),
        ("KA46", "b-front.png", photo((20, 20, 228), shape="front")),
    ]
    references: list[SemanticReference] = []
    for code, filename, bytes_value in approved:
        (root / filename).write_bytes(bytes_value)
        references.append(SemanticReference(code, filename, sha256(bytes_value).hexdigest()))
    cases: list[PhotoCase] = []
    inputs: list[tuple[str, str, str | None, str, bytes]] = [
        ("same", "same_bytes", "KA45", "a-front", approved[0][2]),
        ("side", "different_angle", "KA45", "a-side", photo((228, 20, 20), shape="other")),
        ("negative", "unrelated", None, "unknown", photo((20, 228, 20), shape="other")),
    ]
    for id_, scenario_name, expected, filename, data in inputs:
        location = query_root / (filename + ".png")
        location.write_bytes(data)
        cases.append(PhotoCase(TruthCase(id_, scenario_name, expected, ()), location))
    return cases, references, root


def test_real_photo_bytes_evaluate_exact_and_cross_angle_without_identity_claim(
    tmp_path: Path,
) -> None:
    cases, refs, root = scenario(tmp_path)
    # Differently posed photo should not be a dHash/aHash near duplicate.
    assert (
        shortlist(
            cases[1].photo.read_bytes(),
            [VisualCandidate(x.product_code, x.storage_key) for x in refs],
            root,
        )
        == []
    )
    semantic = SemanticVisualSearch(
        storage_root=root,
        model_cache_root=tmp_path / "cache",
        encoder=ColorEncoder(),
        min_score=0.87,
    )
    result = evaluate_photos(cases, refs, storage_root=root, semantic=semantic)
    assert not result.unavailable
    assert not result.incomplete
    assert result.predictions[0].status == "EXACT_MATCH"
    assert result.predictions[1].status == "SIMILAR"  # never an exact identification
    assert result.predictions[1].ranked_codes[0] == "KA45"
    assert result.predictions[2].status == "NO_MATCH"
    assert result.totals["recall_at_1"] == 1.0
    assert result.totals["negative_candidate_rate"] == 0.0
    assert result.scenarios["different_angle"]["top1_recall"] == 1.0
    assert result.latency_ms["samples"] == len(cases)
    assert isinstance(result.latency_ms["p95_ms"], float)
    assert isinstance(result.latency_ms["p50_ms"], float)
    assert result.latency_ms["max_ms"] is not None


def test_local_latency_summary_uses_nearest_rank_p95_and_rejects_bad_samples() -> None:
    summary = summarize_latency_ms([300.0, 100.0, 500.0, 200.0, 400.0])
    assert summary == {
        "samples": 5,
        "p50_ms": 300.0,
        "p95_ms": 500.0,
        "max_ms": 500.0,
    }
    assert summarize_latency_ms([]) == {
        "samples": 0,
        "p50_ms": None,
        "p95_ms": None,
        "max_ms": None,
    }
    with pytest.raises(ValueError):
        summarize_latency_ms([float("nan")])
    with pytest.raises(ValueError):
        summarize_latency_ms([-1.0])


def test_misleading_negative_candidacy_is_measured_not_reported_as_identity(
    tmp_path: Path,
) -> None:
    cases, refs, root = scenario(tmp_path)
    cases = [cases[2]]
    # At a relaxed threshold, an unfamiliar photo may have a visual candidate;
    # that false suggestion must count even though identity was not asserted.
    semantic = SemanticVisualSearch(
        storage_root=root,
        model_cache_root=tmp_path / "cache",
        encoder=ColorEncoder(),
        min_score=0.0,
    )
    result = evaluate_photos(cases, refs, storage_root=root, semantic=semantic)
    assert result.predictions[0].status == "SIMILAR"
    assert result.totals["negative_candidate_rate"] == 1.0
    assert result.totals["false_positive_rate"] == 0.0


def test_tampered_reference_is_never_an_exact_photo_match(tmp_path: Path) -> None:
    cases, refs, root = scenario(tmp_path)
    (root / "a-front.png").write_bytes(photo((1, 2, 3), shape="side"))
    result = evaluate_photos(cases[:1], refs, storage_root=root, semantic=None)
    assert result.incomplete
    assert result.predictions[0].status != "EXACT_MATCH"


def test_no_local_model_cannot_be_falsely_reported_as_calibrated(tmp_path: Path) -> None:
    cases, refs, root = scenario(tmp_path)
    semantic = SemanticVisualSearch(
        storage_root=root,
        model_cache_root=tmp_path / "models",
        encoder=ModelUnavailable(),
    )
    result = evaluate_photos(cases[1:], refs, storage_root=root, semantic=semantic)
    assert result.unavailable


def _manifest(root: Path, rows: list[dict[str, object]]) -> Path:
    destination = root / "manifest.jsonl"
    destination.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return destination


def _row(photo_name: str = "phone.png", **change: object) -> dict[str, object]:
    row: dict[str, object] = {
        "query_id": "angle1",
        "scenario": "different_angle",
        "photo": photo_name,
        "exact_code": "KA-45",
        "similar_codes": ["KA46"],
    }
    row.update(change)
    return row


def test_manifest_normalizes_labels_but_keeps_private_paths_out_of_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "private"
    folder.mkdir()
    folder.joinpath("phone.png").write_bytes(photo((210, 30, 30), shape="side"))
    manifest = _manifest(tmp_path, [_row()])
    cases = read_photo_manifest(manifest, photo_root=folder)
    assert cases[0].truth.exact_code == "KA45"
    assert cases[0].photo == folder / "phone.png"

    async def fake_refs(settings: Settings, *, limit: int) -> list[SemanticReference]:
        del settings, limit
        return [SemanticReference("KA45", "private-asset.png", "a" * 64)]

    monkeypatch.setattr(evaluate_visual_photos, "approved_references", fake_refs)
    result = asyncio.run(
        evaluate_visual_photos.run_evaluation(
            Settings(), manifest=manifest, photo_root=folder, apply=False, limit=80
        )
    )
    serialized = json.dumps(result)
    assert result["status"] == "DRY_RUN"
    assert "phone.png" not in serialized
    assert "KA45" not in serialized
    assert "angle1" not in serialized
    assert len(cast(str, result["manifest_sha256"])) == 64


@pytest.mark.parametrize(
    "row",
    [
        _row(photo_name="../secret.png"),
        _row(photo_name="/tmp/secret.png"),
        _row(query_id="x" * 81),
        _row(exact_code=3),
        _row(similar_codes="not an array"),
        _row(similar_codes=["KA45"]),
        _row(similar_codes=["KA46", "KA-46"]),
        _row(extra_secret="never accept"),
    ],
)
def test_untrusted_manifest_fields_fail_closed(tmp_path: Path, row: dict[str, object]) -> None:
    folder = tmp_path / "photos"
    folder.mkdir()
    folder.joinpath("phone.png").write_bytes(photo((200, 20, 20), shape="front"))
    manifest = _manifest(tmp_path, [row])
    with pytest.raises((ValueError, FileNotFoundError)):
        read_photo_manifest(manifest, photo_root=folder)


def test_duplicate_ids_symlink_escape_and_missing_photos_rejected(tmp_path: Path) -> None:
    folder = tmp_path / "data"
    folder.mkdir()
    folder.joinpath("phone.png").write_bytes(photo((200, 20, 20), shape="front"))
    manifest = _manifest(tmp_path, [_row(), _row(photo_name="phone.png")])
    with pytest.raises(ValueError):
        read_photo_manifest(manifest, photo_root=folder)
    folder.joinpath("link.png").symlink_to(folder / "phone.png")
    manifest = _manifest(tmp_path, [_row(photo_name="link.png")])
    with pytest.raises(ValueError):
        read_photo_manifest(manifest, photo_root=folder)
