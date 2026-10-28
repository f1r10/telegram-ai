"""V1 visual search parity and non-secret CPU deployment prerequisites."""

from pathlib import Path

from office_assistant.infrastructure.database.models import ImageAssetOrigin
from office_assistant.miniapp.photo_lookup import PhotoLookupResult, ranked_ann_candidates
from office_assistant.processing.document_visual_candidates import DocumentImageHit, DocumentImageSource
from office_assistant.processing.visual_ann import IndexedDocument, IndexedProduct, IndexedResults

ROOT = Path(__file__).resolve().parents[2]


def test_miniapp_ranks_products_and_document_sources_together() -> None:
    source = DocumentImageSource(
        7, 10, "warehouse.xlsx", ImageAssetOrigin.XLSX_EMBEDDED,
        {"sheet": "Qiymet", "anchor_cell": "A12"},
        "image/7.png", "a" * 64, "Ad: Metal qələm",
    )
    results = IndexedResults(
        (IndexedProduct("KA10", 0.98), IndexedProduct("KA11", 0.86)),
        (IndexedDocument(source, 0.93),),
    )
    ranked = ranked_ann_candidates(results)
    assert [r["kind"] for r in ranked] == ["product", "source", "product"]
    assert [r["score"] for r in ranked] == [0.98, 0.93, 0.86]
    assert ranked[1]["filename"] == "warehouse.xlsx"
    assert "A12" in str(ranked[1]["location"])
    assert "Metal qələm" in str(ranked[1]["description"])
    # Both independent evidence classes are returned, never silently substituted.
    result = PhotoLookupResult(
        "candidates",
        ("KA10", "KA11"),
        "dino_qdrant_all_active",
        False,
        "Vizual namizədlər, məhsul təsdiqi deyil.",
        (DocumentImageHit(source, "semantic", 0.93),),
        ranked,
    )
    payload = result.payload()
    assert payload["codes"] == ["KA10", "KA11"]
    assert payload["ranked"] == list(ranked)
    assert "sources" in payload
    assert "stock" not in payload
    assert "price" not in payload


def test_ranked_candidates_have_bounded_output_and_never_echo_source_storage_keys() -> None:
    results = IndexedResults(
        tuple(IndexedProduct(f"KA{i}", 1.0 - i / 1000) for i in range(100)),
        (),
    )
    ranked = ranked_ann_candidates(results)
    assert len(ranked) == 40
    assert ranked[0]["code"] == "KA0"
    assert ranked[-1]["code"] == "KA39"
    assert all("storage_key" not in row and "digest" not in row for row in ranked)


def test_cpu_compose_overlay_points_backfill_to_internal_qdrant() -> None:
    overlay = (ROOT / "docker-compose.visual-cpu.yml").read_text()
    assert "QDRANT_URL: http://qdrant:6333" in overlay
    assert "DINO_STAGING_MIN_SCORE:-0.82" in overlay
    assert "DINO_STAGING_ENABLED:-true" in overlay
    assert "qdrant:\n        condition: service_healthy" in overlay
    # Normal base remains unchanged. Model activation requires the optional CPU image.
    staging = (ROOT / "docker-compose.staging.yml").read_text()
    assert "dockerfile: Dockerfile" in staging
