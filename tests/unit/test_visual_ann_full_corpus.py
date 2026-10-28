"""DINOv2 visual ANN is separated, gated, and not a 500-source scan."""

import asyncio
from types import SimpleNamespace
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.processing.semantic_visual import DINO_DIMENSION, DINO_MODEL_VERSION
from office_assistant.processing.visual_ann import IndexedVisualPoint, VisualANNIndex, point_id
from office_assistant.scripts.rebuild_visual_ann import rebuild


def test_point_ids_have_stable_distinct_namespaces() -> None:
    assert point_id("product", "42") != point_id("document", "42")
    assert point_id("product", "42") == point_id("product", "42")


def test_ann_upserts_normalized_dimensions_only() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.points: list[object] = []

        async def upsert(self, **kwargs: object) -> None:
            self.points.extend(cast(list[object], kwargs["points"]))

        async def query_points(self, **kwargs: object) -> SimpleNamespace:
            del kwargs
            return SimpleNamespace(
                points=[
                    SimpleNamespace(
                        payload={
                            "kind": "document", "record_id": "123",
                            "digest": "a" * 64, "version": DINO_MODEL_VERSION,
                        },
                        score=0.94,
                    ),
                    SimpleNamespace(payload={"kind": "product", "record_id": "none"}, score=1),
                ]
            )

    async def run() -> None:
        index = VisualANNIndex("http://127.0.0.1:6333")
        client = FakeClient()
        index.client = cast(type(index.client), client)
        vector = (1.0,) + (0.0,) * (DINO_DIMENSION - 1)
        assert await index.upsert(
            [
                IndexedVisualPoint("document", "123", "a" * 64, vector),
                IndexedVisualPoint("document", "bad", "a" * 64, (1.0,)),
            ]
        ) == 1
        assert len(client.points) == 1
        rows = await index.query(vector)
        assert rows == [("document", "123", "a" * 64, 0.94)]

    asyncio.run(run())


def test_backfill_dry_run_counts_ten_thousand_plus_sources_without_model_or_network() -> None:
    class Result:
        def __init__(self, rows: list[tuple[int, str, str]]) -> None:
            self.rows = rows

        def all(self) -> list[tuple[int, str, str]]:
            return self.rows

    class Session:
        async def __aenter__(self) -> "Session":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def execute(self, statement: object) -> Result:
            query = str(statement)
            if "product_image_relations" in query:
                return Result([])
            beginning = progress["count"] + 1
            ending = min(beginning + 63, 10001)
            rows = [(i, f"source_{i}.png", "a" * 64) for i in range(beginning, ending + 1)]
            progress["count"] += len(rows)
            return Result(rows)

    class Factory:
        def __call__(self) -> Session:
            return Session()

    class Encoder:
        version = DINO_MODEL_VERSION

    class Index:
        async def ensure(self) -> None:
            raise AssertionError("Dry run must not create Qdrant collection")

    progress = {"count": 0}

    async def run() -> None:
        counts = await rebuild(
            cast(async_sessionmaker[AsyncSession], Factory()),
            cast(object, Encoder()),  # type: ignore[arg-type]
            cast(object, Index()),  # type: ignore[arg-type]
            apply=False,
            batch_size=64,
        )
        assert counts == (0, 10001, 0)
        assert progress["count"] == 10001

    asyncio.run(run())
