"""Deterministic reading of the naming caption (Master Spec §23.7): no model, no guessing.

A caption authorizes only when it *states* that the picture shows one product: the save phrase has
to close it, the code has to carry a naming copula, nothing may deny that statement, and no question
may be asked about it. Anything else writes nothing.
"""

import pytest

from office_assistant.business.reference_confirmation import (
    AmbiguousPictureConfirmation,
    NoPictureConfirmation,
    ProductPictureConfirmation,
    parse_reference_confirmation,
)


@pytest.mark.parametrize(
    ("caption", "code"),
    [
        ("Bu KA45-dir, yadda saxla", "KA45"),
        ("Bu KA45-dir, yadda saxla.", "KA45"),
        ("Bu KA45 dır, yadda saxla", "KA45"),
        ("Bu KA45-dur, yadda saxla", "KA45"),
        ("Bu KA45-dər, yadda saxla", "KA45"),
        ("Bu KA45-dı, yadda saxla", "KA45"),
        ("KA45-dir yadda saxla", "KA45"),
        ("bu ka45-dir, yadda saxla", "KA45"),
        ("  Bu KA-45-dir yadda saxla  ", "KA45"),
        ("Şəkil bu KA45-dir. Yadda saxla", "KA45"),
        # The copula welded on without the hyphen is the same statement.
        ("Bu KA45dır, yadda saxla", "KA45"),
        # A trailing mark that is not a letter cannot undo a closed phrase.
        ("Bu KA45-dir, yadda saxla ✅", "KA45"),
        # One product named twice is one product, not a choice between two.
        ("Bu KA45-dir, KA45-dır, yadda saxla", "KA45"),
    ],
)
def test_one_named_product_is_read_without_a_model(caption: str, code: str) -> None:
    parsed = parse_reference_confirmation(caption)

    assert parsed == ProductPictureConfirmation(code)


@pytest.mark.parametrize(
    "caption",
    [
        # The copula hides the code inside an ordinary sentence.
        "Bu KA45-dir, yadda saxlama",
        "Bu KA45-dir, zəhmət olmasa yadda saxlama",
        # The save phrase has to close the caption, so prose about saving stays an upload.
        "yadda saxla, bu KA45-dir",
        "Bunu yadda saxlamaq lazımdır, KA45-dir",
        # A different verb of keeping is not the phrase that was agreed on.
        "Bu KA45-dir, yadda saxlanılsın",
        "Bu KA45-dir, yadda saxlasana",
        "Bu KA45-dir, zəhmət olmasa saxla",
        # No product-shaped name at all.
        "Bu şəkli yadda saxla",
        "Bu məhsuldur, yadda saxla",
        "Salam, necəsən?",
        # A bare digit is not an identity claim.
        "Bu 5-dir, yadda saxla",
        "",
        "   ",
    ],
)
def test_an_ordinary_caption_writes_nothing(caption: str) -> None:
    assert parse_reference_confirmation(caption) == NoPictureConfirmation()


@pytest.mark.parametrize(
    "caption",
    [
        # A code inside a sentence says what the message is about, not what the picture shows.
        "Bu məhsul KA45, yadda saxla",
        "KA45 anbarda var, yadda saxla",
        "Bu KA45-in qiyməti, yadda saxla",
        "KA45 barədə məlumat ver, yadda saxla",
        # The user denied the naming, so there is nothing to confirm.
        "Bu KA45-dir deyil, yadda saxla",
        "Bu KA45 dır deyil, yadda saxla",
        # A question asks which product the picture shows; only an answer states it.
        "Bu KA45-dir? yadda saxla",
        "Bu KA45-dir, yadda saxla?",
    ],
)
def test_a_mention_a_denial_or_a_question_is_not_authorization(caption: str) -> None:
    assert parse_reference_confirmation(caption) == NoPictureConfirmation()


def test_a_missing_caption_is_ordinary() -> None:
    assert parse_reference_confirmation(None) == NoPictureConfirmation()


@pytest.mark.parametrize(
    ("caption", "codes"),
    [
        ("Bu KA45-dir, KA46 deyil, yadda saxla", ("KA45", "KA46")),
        ("Bu KA45 və KA46-dır, yadda saxla", ("KA45", "KA46")),
        # Two asserted names are still a choice the user did not make.
        ("Bu A1-dir, B2-dir, yadda saxla", ("A1", "B2")),
        # Spelling is normalized first, so KA-45 and ka45 are one product, KA45 and KA46 are two.
        ("Bu KA45-dir, ka-46 dır, yadda saxla", ("KA45", "KA46")),
    ],
)
def test_several_names_are_reported_instead_of_chosen(caption: str, codes: tuple[str, ...]) -> None:
    parsed = parse_reference_confirmation(caption)

    assert isinstance(parsed, AmbiguousPictureConfirmation)
    # The caption's own order, in the canonical spelling the registry uses.
    assert parsed.codes == codes


def test_the_read_code_is_the_canonical_form_used_for_lookups() -> None:
    parsed = parse_reference_confirmation("bu ka-45-dir, yadda saxla")

    assert parsed == ProductPictureConfirmation("KA45")
