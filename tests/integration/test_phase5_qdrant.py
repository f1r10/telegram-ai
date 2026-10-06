import asyncio

import pytest

from office_assistant.knowledge.models import DocumentChunk, RetrievalFilter
from office_assistant.knowledge.qdrant_store import QdrantKnowledgeStore

pytestmark = pytest.mark.integration


def test_qdrant_collection_alias_hybrid_filter_and_delete() -> None:
    store = QdrantKnowledgeStore(
        "http://localhost:16333",
        alias="phase5_test_chunks",
        collection="phase5_test_chunks_v1",
    )

    async def scenario() -> None:
        if not await store.health_check():
            pytest.skip("Qdrant is not available on localhost:16333")
        await store.ensure_collection(3)
        chunks = [
            DocumentChunk(
                "00000000000000000000000000000045",
                1,
                2,
                1,
                "KA45 zəmanət müddəti 24 aydır",
                "KA45 zəmanət müddəti 24 aydır",
                ("paragraph",),
                {"blocks": [{"page": 1}]},
            ),
            DocumentChunk(
                "00000000000000000000000000000046",
                2,
                3,
                1,
                "KA46 zəmanət müddəti 12 aydır",
                "KA46 zəmanət müddəti 12 aydır",
                ("paragraph",),
                {"blocks": [{"page": 1}]},
            ),
        ]
        sparse = [([1, 2], [1.0, 0.5]), ([2, 3], [1.0, 0.5])]
        await store.upsert_chunks(
            chunks,
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            sparse,
            source_filename="fixture.txt",
            media_type="text/plain",
            embedding_model="test-model",
        )
        dense = await store.search_dense([1.0, 0.0, 0.0], limit=1)
        hybrid = await store.search_hybrid([1.0, 0.0, 0.0], sparse[0], limit=2)
        filtered = await store.search_dense(
            [0.0, 1.0, 0.0], limit=2, filters=RetrievalFilter(file_id=2)
        )
        assert dense[0].chunk_id == "00000000000000000000000000000045"
        assert hybrid
        assert filtered[0].chunk_id == "00000000000000000000000000000046"
        await store.delete_file(1)
        assert all(
            result.source_file_id != 1
            for result in await store.search_dense([1.0, 0.0, 0.0], limit=5)
        )
        await store.client.delete_collection(store.collection)

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(store.close())
