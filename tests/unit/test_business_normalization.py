import pytest

from office_assistant.business.normalization import (
    normalize_currency,
    normalize_customer_alias,
    normalize_customer_name,
    normalize_product_alias,
    normalize_product_code,
)


@pytest.mark.parametrize("value", ["KA45", "ka45", "KA-45", "KA 45", "KA_45"])
def test_product_code_variants_share_canonical_identity(value: str) -> None:
    assert normalize_product_code(value) == "KA45"


def test_product_codes_do_not_aggressively_transliterate_letters() -> None:
    assert normalize_product_code("KƏ-45") == "KƏ45"
    assert normalize_product_code("KƏ-45") != normalize_product_code("KE-45")
    assert normalize_product_alias("KA-45") == normalize_product_alias("KA 45") == "KA45"


@pytest.mark.parametrize("value", ["ABC MMC", "abc mmc", "ABC   MMC", "ABC, MMC"])
def test_customer_name_variants_are_comparable(value: str) -> None:
    assert normalize_customer_name(value) == "abc mmc"
    assert normalize_customer_alias(value) == "abc mmc"


def test_customer_suffixes_and_azerbaijani_letters_are_preserved() -> None:
    assert normalize_customer_name("İşıq Şirkəti MMC") == "i̇şıq şirkəti mmc"
    assert normalize_customer_name("ABC MMC") != normalize_customer_name("ABC LLC")


def test_currency_is_normalized_and_validated() -> None:
    assert normalize_currency(" azn ") == "AZN"
    with pytest.raises(ValueError):
        normalize_currency("manat")
