"""Local dense and sparse embedding abstractions."""

# FastEmbed's generated vector types are intentionally hidden behind this
# small, typed application boundary.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

from fastembed import SparseTextEmbedding, TextEmbedding


class TextEmbedder(Protocol):
    @property
    def dimension(self) -> int: ...

    @property
    def model_id(self) -> str: ...

    def embed_documents(self, texts: Iterable[str]) -> list[list[float]]: ...

    def embed_queries(self, texts: Iterable[str]) -> list[list[float]]: ...


class SparseEmbedder(Protocol):
    @property
    def model_id(self) -> str: ...

    def embed(self, texts: Iterable[str]) -> list[tuple[list[int], list[float]]]: ...


class FastEmbedTextAdapter:
    """Lazy local FastEmbed adapter; model weights are cached on persistent storage."""

    def __init__(self, model_id: str, cache_dir: str | Path, *, batch_size: int = 32) -> None:
        self._model_id = model_id
        self._cache_dir = str(cache_dir)
        self._batch_size = batch_size
        self._model: Any = None

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimension(self) -> int:
        return int(self.load().embedding_size)

    def embed_documents(self, texts: Iterable[str]) -> list[list[float]]:
        return self._vectors(texts, prefix="passage: ")

    def embed_queries(self, texts: Iterable[str]) -> list[list[float]]:
        return self._vectors(texts, prefix="query: ")

    def _vectors(self, texts: Iterable[str], *, prefix: str) -> list[list[float]]:
        values = [prefix + text if "e5" in self._model_id.lower() else text for text in texts]
        return [
            list(map(float, vector))
            for vector in self.load().embed(values, batch_size=self._batch_size)
        ]

    def load(self) -> Any:
        """Materialize the local model; call before serving traffic to hide cold start."""
        if self._model is None:
            self._model = TextEmbedding(
                model_name=self._model_id,
                cache_dir=self._cache_dir,
                lazy_load=False,
            )
        return self._model


class FastEmbedSparseAdapter:
    def __init__(
        self, model_id: str = "Qdrant/bm25", cache_dir: str | Path = "./var/models"
    ) -> None:
        self._model_id = model_id
        self._cache_dir = str(cache_dir)
        self._model: Any = None

    @property
    def model_id(self) -> str:
        return self._model_id

    def load(self) -> Any:
        """Materialize the local sparse model; call before serving traffic."""
        if self._model is None:
            self._model = SparseTextEmbedding(
                model_name=self._model_id,
                cache_dir=self._cache_dir,
                lazy_load=False,
            )
        return self._model

    def embed(self, texts: Iterable[str]) -> list[tuple[list[int], list[float]]]:
        model = self.load()
        results = []
        for vector in model.embed(list(texts), batch_size=32):
            results.append((list(map(int, vector.indices)), list(map(float, vector.values))))
        return results


def supported_dense_candidates() -> list[dict[str, Any]]:
    return [
        {
            "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "license": "Apache-2.0",
            "dimension": 384,
            "size_gb": 0.22,
            "status": "supported",
        },
        {
            "model": "minishlab/potion-multilingual-128M",
            "license": "MIT",
            "dimension": 256,
            "size_gb": 0.512,
            "status": "supported",
        },
        {
            "model": "intfloat/multilingual-e5-small",
            "license": "MIT",
            "dimension": 384,
            "size_gb": None,
            "status": "not-directly-registered; custom integration requires verification",
        },
    ]
