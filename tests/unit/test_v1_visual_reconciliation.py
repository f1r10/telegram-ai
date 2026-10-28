"""Derived visual ANN reconciliation cannot discard active PostgreSQL sources."""

import asyncio
from types import SimpleNamespace
from typing import cast

from qdrant_client import AsyncQdrantClient, models
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.processing.semantic_visual import DINO_MODEL_VERSION
from office_assistant.processing.visual_ann import VisualANNIndex, point_id


def test_reconciliation_removes_only_stale_points() -> None:
    class Rows:
        def all(self) -> list[tuple[int, str]]:
            return [(24, "a" * 64)]

    class Session:
        async def __aenter__(self) -> "Session":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def execute(self, statement: object) -> Rows:
            assert "image_asset_occurrences" in str(statement)
            return Rows()

    class Factory:
        def __call__(self) -> Session:
            return Session()

    class Client:
        def __init__(self) -> None:
            self.removed: list[str] = []

        async def scroll(self, **kwargs: object) -> tuple[list[SimpleNamespace], None]:
            del kwargs
            return (
                [
                    SimpleNamespace(
                        id=point_id("document", "24"),
                        payload={
                            "kind": "document",
                            "record_id": "24",
                            "digest": "a" * 64,
                            "version": DINO_MODEL_VERSION,
                        },
                    ),
                    SimpleNamespace(
                        id=point_id("document", "25"),
                        payload={
                            "kind": "document",
                            "record_id": "25",
                            "digest": "b" * 64,
                            "version": DINO_MODEL_VERSION,
                        },
                    ),
                ],
                None,
            )

        async def delete(self, **kwargs: object) -> None:
            selected = cast(models.PointIdsList, kwargs["points_selector"])
            self.removed.extend(str(value) for value in selected.points)

    async def run() -> None:
        index = VisualANNIndex("http://127.0.0.1:6333")
        client = Client()
        index.client = cast(AsyncQdrantClient, client)
        inspected, removed = await index.prune_stale(
            cast(async_sessionmaker[AsyncSession], Factory())
        )
        assert (inspected, removed) == (2, 1)
        assert client.removed == [point_id("document", "25")]

    asyncio.run(run())
