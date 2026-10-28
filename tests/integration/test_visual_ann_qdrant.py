"""Real Qdrant integration contract for isolated 384-d visual ANN collection."""

import asyncio
import os
from uuid import uuid4

import pytest

from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.processing.semantic_visual import DINO_DIMENSION
from office_assistant.processing.visual_ann import IndexedVisualPoint, VisualANNIndex


@pytest.mark.integration
def test_visual_ann_creates_isolated_cosine_index_and_queries_synthetic_vectors() -> None:
    address = os.environ.get("QDRANT_URL")
    if not address:
        pytest.skip("QDRANT_URL is required for real ANN integration")
    collection = "office_visual_integration_" + uuid4().hex
    index = VisualANNIndex(address, collection=collection)

    async def run() -> None:
        try:
            await index.ensure()
            first = (1.0, 0.0) + (0.0,) * (DINO_DIMENSION - 2)
            second = (0.0, 1.0) + (0.0,) * (DINO_DIMENSION - 2)
            assert (
                await index.upsert(
                    [
                        IndexedVisualPoint("product", "one", "a" * 64, first),
                        IndexedVisualPoint("document", "two", "b" * 64, second),
                    ]
                )
                == 2
            )
            results = await index.query(first, limit=2)
            assert len(results) == 2
            assert results[0][0:3] == ("product", "one", "a" * 64)
            assert 0.99 <= results[0][3] <= 1.01
            assert {row[0] for row in results} == {"product", "document"}
            settings = load_settings()
            if all(
                (
                    settings.postgres_host,
                    settings.postgres_db,
                    settings.postgres_user,
                    settings.postgres_password,
                )
            ):
                engine = create_database_engine(settings)
                try:
                    inspected, removed = await index.prune_stale(
                        create_session_factory(engine), batch_size=1
                    )
                    assert inspected == 2
                    assert removed == 2
                    assert await index.query(first, limit=2) == []
                finally:
                    await engine.dispose()
        finally:
            try:
                await index.client.delete_collection(collection)
            finally:
                await index.close()

    asyncio.run(run())
