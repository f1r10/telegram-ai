"""Additional safeguards against different objects sharing coarse image hashes."""

from office_assistant.processing.image_similarity import (
    ImageFingerprint,
    fingerprint_near_duplicate,
)


def test_coarse_hash_collision_is_not_a_product_candidate() -> None:
    # Identical 64-bit grayscale hashes and 4x4 color summaries are
    # insufficient: distinct 16x16 interior arrangements must be rejected.
    original = ImageFingerprint(
        dhash=0x12345678,
        ahash=0x98765432,
        color_tiles=(125,) * 48,
        fine_tiles=(20,) * 768,
    )
    unrelated = ImageFingerprint(
        dhash=original.dhash,
        ahash=original.ahash,
        color_tiles=original.color_tiles,
        fine_tiles=(230,) * 768,
    )
    assert not fingerprint_near_duplicate(original, unrelated)


def test_jpeg_level_fine_color_variation_is_still_a_candidate() -> None:
    original = ImageFingerprint(
        dhash=0x12345678,
        ahash=0x98765432,
        color_tiles=(125,) * 48,
        fine_tiles=(125,) * 768,
    )
    recompressed = ImageFingerprint(
        dhash=original.dhash,
        ahash=original.ahash,
        color_tiles=(128,) * 48,
        fine_tiles=(131,) * 768,
    )
    assert fingerprint_near_duplicate(original, recompressed)
