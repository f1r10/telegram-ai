"""Offline visual prewarm counts both company photos and PDF artwork."""

import asyncio
from pathlib import Path

import pytest

from office_assistant.core.settings import Settings
from office_assistant.processing.semantic_visual import ReferenceWarmResult, SemanticReference
from office_assistant.scripts import prepare_visual_catalog


def test_visual_catalog_dry_run_covers_more_than_eighty_products(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def approved(
        settings: Settings, *, limit: int
    ) -> list[SemanticReference]:
        del settings, limit
        return [
            SemanticReference(f"SKU{i}", f"photo{i}.jpg", "a" * 64)
            for i in range(120)
        ]

    async def pdf(settings: Settings, *, limit: int) -> list[SemanticReference]:
        del settings, limit
        return [SemanticReference("1", "asset.jpg", "b" * 64)]

    monkeypatch.setattr(prepare_visual_catalog, "approved_references", approved)
    monkeypatch.setattr(prepare_visual_catalog, "pdf_image_references", pdf)
    summary = asyncio.run(
        prepare_visual_catalog.prepare_catalog(
            Settings(file_storage_path=str(tmp_path)), apply=False, limit=500
        )
    )
    assert summary.approved == 120
    assert summary.pdf == 1
    assert summary.prepared == 0
    assert not summary.unavailable
    assert not summary.truncated


def test_visual_catalog_prewarms_both_sources_and_preserves_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def approved(
        settings: Settings, *, limit: int
    ) -> list[SemanticReference]:
        del settings, limit
        return [SemanticReference("SKU1", "photo.jpg", "a" * 64)]

    async def pdf(settings: Settings, *, limit: int) -> list[SemanticReference]:
        del settings, limit
        return [SemanticReference("1", "pdf.jpg", "b" * 64)]

    class Encoder:
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            self.calls = 0

        def warm_references(
            self, references: list[SemanticReference]
        ) -> ReferenceWarmResult:
            self.calls += 1
            return ReferenceWarmResult(len(references), len(references), 0, False)

    monkeypatch.setattr(prepare_visual_catalog, "approved_references", approved)
    monkeypatch.setattr(prepare_visual_catalog, "pdf_image_references", pdf)
    monkeypatch.setattr(prepare_visual_catalog, "SemanticVisualSearch", Encoder)
    summary = asyncio.run(
        prepare_visual_catalog.prepare_catalog(
            Settings(file_storage_path=str(tmp_path)), apply=True, limit=500
        )
    )
    assert (summary.approved, summary.pdf, summary.prepared) == (1, 1, 2)
    assert not summary.unavailable


def test_visual_catalog_reference_limit_rejects_invalid_value(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        asyncio.run(
            prepare_visual_catalog.prepare_catalog(
                Settings(file_storage_path=str(tmp_path)), limit=501
            )
        )
