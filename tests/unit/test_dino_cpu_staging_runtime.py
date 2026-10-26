"""CPU-only staging runtime must remain opt-in, safe and independent."""

from pathlib import Path

import pytest

from office_assistant.processing.semantic_visual import DINO_DIMENSION
from office_assistant.scripts import prepare_dino_runtime

ROOT = Path(__file__).resolve().parents[2]


def test_dino_probe_uses_only_synthetic_picture_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[Path] = []

    class Encoder:
        def __init__(self, cache_root: Path) -> None:
            assert cache_root == tmp_path

        def embed(self, paths: list[Path]) -> list[list[float]]:
            assert len(paths) == 1
            calls.extend(paths)
            assert paths[0].name == "synthetic.png"
            assert paths[0].exists()
            return [[1.0] + [0.0] * (DINO_DIMENSION - 1)]

    monkeypatch.setattr(prepare_dino_runtime, "LocalDinoMeanEncoder", Encoder)
    def forbid_download(root: Path) -> None:
        del root
        pytest.fail("Default probe must not fetch model weights")

    monkeypatch.setattr(prepare_dino_runtime, "download_dino_weights", forbid_download)
    result = prepare_dino_runtime.probe_dino(tmp_path)
    assert result.dimension == DINO_DIMENSION
    assert len(calls) == 1


def test_dino_probe_rejects_wrong_vector_dimension(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Encoder:
        def __init__(self, cache_root: Path) -> None:
            del cache_root

        def embed(self, paths: list[Path]) -> list[list[float]]:
            del paths
            return [[1.0] * 512]

    monkeypatch.setattr(prepare_dino_runtime, "LocalDinoMeanEncoder", Encoder)
    with pytest.raises(ValueError, match="invalid embedding"):
        prepare_dino_runtime.probe_dino(tmp_path)


def test_explicit_download_is_required(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    downloaded: list[Path] = []

    class Encoder:
        def __init__(self, cache_root: Path) -> None:
            del cache_root

        def embed(self, paths: list[Path]) -> list[list[float]]:
            del paths
            return [[1.0] + [0.0] * (DINO_DIMENSION - 1)]

    monkeypatch.setattr(prepare_dino_runtime, "LocalDinoMeanEncoder", Encoder)
    monkeypatch.setattr(prepare_dino_runtime, "download_dino_weights", downloaded.append)
    prepare_dino_runtime.probe_dino(tmp_path, download_model=True)
    assert downloaded == [tmp_path]


def test_visual_cpu_overlay_never_exposes_ports_or_forces_enabled() -> None:
    compose = (ROOT / "docker-compose.visual-cpu.yml").read_text("utf-8")
    assert "DINO_STAGING_ENABLED:-false" in compose
    assert "VISUAL_SEMANTIC_MODEL_VERSION: dinov2-small-mean-v1" in compose
    assert "Dockerfile.visual-cpu" in compose
    assert "127.0.0.1" not in compose
    assert "ports:" not in compose
    assert "visual-cpu-ops:" in compose
    assert "postgres_data:" not in compose


def test_cpu_dockerfile_leaves_default_installation_untouched() -> None:
    original = (ROOT / "Dockerfile").read_text("utf-8")
    optional = (ROOT / "Dockerfile.visual-cpu").read_text("utf-8")
    assert "torch==2.5.1" not in original
    assert "torch==2.5.1" in optional
    assert "transformers==4.49.0" in optional
    assert '"--no-sync"' in optional
    assert "USER appuser" in optional
