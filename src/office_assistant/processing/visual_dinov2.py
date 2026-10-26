"""Offline CPU DINOv2 Small mean-pooling image encoder.

This adapts the tested *algorithm* in f1r10/local-image-search without
copying its SQLite catalog, external providers, or YOLO/Ultralytics dependency.
No model weights are downloaded by a Telegram query.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from PIL import Image

MODEL_ID = "facebook/dinov2-small"


class _TokenTensor(Protocol):
    def mean(self, *, dim: int) -> _TokenTensor: ...

    def __getitem__(self, key: int) -> _TokenTensor: ...

    def cpu(self) -> _TokenTensor: ...

    def tolist(self) -> list[float]: ...


class _ModelOutput(Protocol):
    last_hidden_state: _TokenTensor


class _Model(Protocol):
    def __call__(self, **kwargs: object) -> _ModelOutput: ...


class _LoadedModel(_Model, Protocol):
    def eval(self) -> _LoadedModel: ...

    def to(self, device: str) -> _LoadedModel: ...


class _Torch(Protocol):
    def set_num_threads(self, count: int) -> None: ...

    def inference_mode(self) -> AbstractContextManager[None]: ...


class _ModelFactory(Protocol):
    def from_pretrained(
        self, model_id: str, *, cache_dir: str, local_files_only: bool
    ) -> _LoadedModel: ...


class _ProcessorFactory(Protocol):
    def from_pretrained(
        self, model_id: str, *, cache_dir: str, local_files_only: bool
    ) -> _Processor: ...


class _Processor(Protocol):
    def __call__(self, *, images: Image.Image, return_tensors: str) -> dict[str, object]: ...


class LocalDinoMeanEncoder:
    """Load pre-provisioned weights locally and return unnormalized 384-vectors.

    SemanticVisualSearch L2-normalizes the output and separates per-version
    vector caches. Cropping is deliberately disabled for watch compatibility:
    COCO YOLOv8n does not include a wristwatch class.
    """

    def __init__(self, cache_root: Path) -> None:
        self.cache_root = cache_root
        self._processor: _Processor | None = None
        self._model: _Model | None = None

    def embed(self, paths: list[Path]) -> list[list[float]]:
        # Heavy imports happen only when an operator explicitly enables this
        # encoder. local_files_only prevents network access for missing weights.
        torch = cast(_Torch, import_module("torch"))
        transformers = import_module("transformers")
        processors = cast(_ProcessorFactory, getattr(transformers, "AutoImageProcessor"))
        models = cast(_ModelFactory, getattr(transformers, "AutoModel"))

        if self._model is None or self._processor is None:
            torch.set_num_threads(2)
            self._processor = processors.from_pretrained(
                MODEL_ID, cache_dir=str(self.cache_root), local_files_only=True
            )
            self._model = models.from_pretrained(
                MODEL_ID, cache_dir=str(self.cache_root), local_files_only=True
            ).eval().to("cpu")
        processor, model = self._processor, self._model
        vectors: list[list[float]] = []
        for path in paths:
            with Image.open(path) as source:
                rgb = source.convert("RGB")
            inputs = processor(images=rgb, return_tensors="pt")
            with torch.inference_mode():
                hidden = model(**inputs).last_hidden_state
                vectors.append(hidden.mean(dim=1)[0].cpu().tolist())
        return vectors
