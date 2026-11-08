"""DINO model must be pre-warmed so the primary ANN search works from the first photo.

Deterministic offline tests: no PyTorch/model weights, no network, no Telegram.
"""

import ast
from pathlib import Path

from office_assistant.processing.semantic_visual import SemanticVisualSearch
from office_assistant.processing.visual_dinov2 import LocalDinoMeanEncoder

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "src/office_assistant/main.py"


class WarmEncoder:
    def __init__(self) -> None:
        self.warmed = 0

    def embed(self, paths: list[Path]) -> list[list[float]]:  # pragma: no cover - unused
        return []

    def warm(self) -> None:
        self.warmed += 1


class NoWarmEncoder:
    def embed(self, paths: list[Path]) -> list[list[float]]:  # pragma: no cover - unused
        return []


class FailingWarmEncoder:
    def embed(self, paths: list[Path]) -> list[list[float]]:  # pragma: no cover - unused
        return []

    def warm(self) -> None:
        raise RuntimeError("weights missing")


def _search(tmp_path: Path, encoder: object) -> SemanticVisualSearch:
    return SemanticVisualSearch(
        storage_root=tmp_path,
        model_cache_root=tmp_path,
        encoder=encoder,  # pyright: ignore[reportArgumentType]
    )


def test_dino_encoder_warm_uses_the_same_model_load_path(tmp_path: Path) -> None:
    encoder = LocalDinoMeanEncoder(tmp_path)
    loads: list[int] = []

    def fake_ensure() -> tuple[object, object]:
        loads.append(1)
        return object(), object()

    encoder._ensure_loaded = fake_ensure  # pyright: ignore[reportPrivateUsage]
    encoder.warm()
    assert loads == [1]


def test_warm_model_true_when_encoder_supports_warm(tmp_path: Path) -> None:
    encoder = WarmEncoder()
    assert _search(tmp_path, encoder).warm_model() is True
    assert encoder.warmed == 1


def test_warm_model_false_when_encoder_has_no_warm_hook(tmp_path: Path) -> None:
    assert _search(tmp_path, NoWarmEncoder()).warm_model() is False


def test_warm_model_false_when_weights_missing_never_raises(tmp_path: Path) -> None:
    assert _search(tmp_path, FailingWarmEncoder()).warm_model() is False


def test_startup_prewarms_visual_model_before_serving_turns() -> None:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "warm_model"
    ]
    assert len(calls) == 1
    # The pre-warm must be guarded by the enabled visual model being present.
    guarded = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and any(
            isinstance(test_node, ast.Compare)
            and isinstance(test_node.left, ast.Name)
            and test_node.left.id == "semantic_visual"
            for test_node in ast.walk(node.test)
        )
        and any(warm in ast.walk(node) for warm in calls)
    )
    assert guarded is not None
