"""Deterministic reading of the naming caption (Master Spec §23.7): no model, no guessing.

A caption authorizes only when it *instructs* — `saxla`, `yadda saxla` or `bağla` closing it — and
*binds* exactly one product to this picture, by a naming copula or by the picture word right after
the code. A mention, a denial, a question, several codes, or prose about saving instead of an order
all write nothing.
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
        # The shape the spec names.
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
        # A trailing mark that is not a letter cannot undo a closed instruction.
        ("Bu KA45-dir, yadda saxla ✅", "KA45"),
        # One product named twice is one product, not a choice between two.
        ("Bu KA45-dir, KA45-dır, yadda saxla", "KA45"),
        # The owner's accepted vocabulary (2026-10-10): an explicit save or link order that binds
        # this picture to one product, with or without the copula.
        ("KA45 şəklini yadda saxla", "KA45"),
        ("KA45 şəkilini yadda saxla", "KA45"),
        ("Bu şəkli KA45 məhsuluna bağla", "KA45"),
        ("KA45 üçün referans şəkil kimi saxla", "KA45"),
        ("Bu KA45 məhsulunun şəklidir, yadda saxla", "KA45"),
        ("Bu KA45 məhsuldur, yadda saxla", "KA45"),
        ("Bu şəkli KA45 üçün saxla", "KA45"),
        ("Bu şəkli KA45-dır, saxla", "KA45"),
        ("KA45-dir, zəhmət olmasa yadda saxla", "KA45"),
        ("Bu KA45 referans şəkil kimi, yadda saxla", "KA45"),
        # An explicit model identifier is not confused with the adjacent price.
        ("Bu casio 1234 modelidir 129 azn yadda saxla", "1234"),
        ("Bu KA45-dir, qiyməti 129 AZN, yadda saxla", "KA45"),
        ("Bu KA45 modelidir, 129 manat, yadda saxla", "KA45"),
    ],
)
def test_an_instruction_that_binds_one_product_is_read_without_a_model(
    caption: str, code: str
) -> None:
    parsed = parse_reference_confirmation(caption)

    assert parsed == ProductPictureConfirmation(code)


@pytest.mark.parametrize(
    "caption",
    [
        # No save or link order at all: an ordinary statement about the product.
        "Bu KA45-dir",
        "Bu məhsul KA45, anbardadır",
        "KA45 şəklini göndərdim",
        # The code is mentioned, not bound — nothing here says this picture is KA45's.
        "Bu məhsul KA45, yadda saxla",
        "KA45 anbarda var, yadda saxla",
        "Bu KA45-in qiyməti, yadda saxla",
        "KA45 barədə məlumat ver, yadda saxla",
        "Bu məhsul şəkli KA45, yadda saxla",
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
        # A negated verb is not an order: ``saxlama``, ``bağlama``, ``saxlanılsın``, ``saxlasana``.
        "Bu KA45-dir, yadda saxlama",
        "Bu KA45-dir, zəhmət olmasa yadda saxlama",
        "KA45 şəklini yadda saxlama",
        "Bu şəkli KA45 məhsuluna bağlama",
        "Bu KA45-dir, yadda saxlanılsın",
        "Bu KA45-dir, yadda saxlasana",
        # Prose about saving rather than an order has to leave the verb out of the closing position.
        "yadda saxla, bu KA45-dir",
        "Bunu yadda saxlamaq lazımdır, KA45-dir",
        # The user denied the naming, so there is nothing to confirm.
        "Bu KA45-dir deyil, yadda saxla",
        "Bu KA45 dır deyil, yadda saxla",
        "Bu KA45 məhsulunun şəkili deyil, yadda saxla",
        # A question asks which product the picture shows; only an answer states it.
        "Bu KA45-dir? yadda saxla",
        "Bu KA45-dir, yadda saxla?",
        "KA45 nədir, yadda saxla?",
        "KA45 şəklini saxlamalıyam?",
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
        # Two bound names are still a choice the user did not make.
        ("Bu A1-dir, B2-dir, yadda saxla", ("A1", "B2")),
        ("KA45 şəklini və KA46 şəklini yadda saxla", ("KA45", "KA46")),
        # With two names in front of it, a denial cannot rescue the caption either.
        ("KA45 şəklini deyil, KA46 şəklini yadda saxla", ("KA45", "KA46")),
        # Spelling is normalized first: KA-45 and ka45 are one product, KA45 and KA46 are two.
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


def test_a_hyphenated_suffix_keeps_its_letters_instead_of_becoming_a_known_code() -> None:
    """Strict identity: the parser guesses nothing, so the registry answers "no such product"."""

    parsed = parse_reference_confirmation("KA45-in şəklini yadda saxla")

    # The hyphen is a separator and the suffix is letters, so ``KA45-in`` reads as ``KA45IN`` — a
    # different identity from ``KA45``, which is why an invented match here would be the bug.
    assert parsed == ProductPictureConfirmation("KA45IN")


def test_non_price_numeric_competing_identifiers_remain_ambiguous() -> None:
    result = parse_reference_confirmation("Bu 1234 modelidir, digər kod 129-dur, yadda saxla")
    assert result == AmbiguousPictureConfirmation(("1234", "129"))
