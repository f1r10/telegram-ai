"""Entire ANN retrieval has one deadline, including database and CPU inference."""

import asyncio
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.processing import visual_ann
from office_assistant.processing.semantic_visual import DINO_MODEL_VERSION, SemanticVisualSearch
from office_assistant.processing.visual_ann import IndexedVisualLookup, VisualANNIndex


class SlowSession:
    async def __aenter__(self) -> "SlowSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def scalar(self, _query: object) -> str:
        await asyncio.sleep(0.20)
        return "ready"


class BrokenSession(SlowSession):
    async def scalar(self, _query: object) -> str:
        raise OSError("Synthetic database outage")


class SessionFactory:
    def __init__(self, session: SlowSession) -> None:
        self.session = session

    def __call__(self) -> SlowSession:
        return self.session


class Encoder:
    version = DINO_MODEL_VERSION


@pytest.mark.parametrize("session", [SlowSession(), BrokenSession()])
def test_visual_index_db_timeouts_or_outages_return_honest_unavailable(
    monkeypatch: pytest.MonkeyPatch, session: SlowSession
) -> None:
    monkeypatch.setattr(visual_ann, "VISUAL_QUERY_TIMEOUT_SECONDS", 0.02)
    lookup = IndexedVisualLookup(
        cast(async_sessionmaker[AsyncSession], SessionFactory(session)),
        cast(VisualANNIndex, object()),
        cast(SemanticVisualSearch, Encoder()),
    )

    async def run() -> None:
        response = await asyncio.wait_for(lookup.search(b"synthetic"), timeout=0.5)
        assert response.unavailable
        assert not response.products and not response.documents

    asyncio.run(run())
