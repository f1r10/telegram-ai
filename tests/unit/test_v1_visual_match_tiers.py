"""Visual match evidence tiers must never turn model similarity into identity."""

import pytest

from office_assistant.miniapp.photo_lookup import PhotoLookupResult


@pytest.mark.parametrize(
    ("status", "method", "expected"),
    [
        ("exact", "confirmed_sha256", "EXACT_MATCH"),
        ("candidates", "near_duplicate", "PROBABLE_MATCH"),
        ("candidates", "cross_angle", "SIMILAR"),
        ("candidates", "dino_qdrant_all_active", "SIMILAR"),
        ("source_candidates", "embedded_document", "SIMILAR"),
        ("source_candidates", "dino_qdrant_all_active", "SIMILAR"),
        ("not_found", "no_match", "NO_MATCH"),
        ("unconfirmed", "no_active_confirmation", "NO_MATCH"),
        ("unavailable", "offline_model", None),
    ],
)
def test_visual_evidence_tier_is_not_product_identity_or_absence_on_failure(
    status: str, method: str, expected: str | None
) -> None:
    result = PhotoLookupResult(status, (), method, False, "Sınaq")
    assert result.match_tier == expected
    payload = result.payload()
    assert payload["match_tier"] == expected
    assert "stock" not in payload
    assert "price" not in payload
