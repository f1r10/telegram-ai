"""Parse an explicit catalog registration command without deriving stock or prices."""

import re
from dataclasses import dataclass

from office_assistant.business.normalization import normalize_product_code

_PRODUCT_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{1,63}\Z")


@dataclass(frozen=True, slots=True)
class ProductRegistrationDraft:
    code: str
    name: str


def parse_product_registration(argument: str) -> ProductRegistrationDraft:
    """An administrator explicitly supplies both SKU and human display name."""

    parts = [value.strip() for value in argument.split("|")]
    if len(parts) != 2:
        raise ValueError("Format: /mehsul_elave 1234 | Casio 1234")
    code, name = parts
    if not _PRODUCT_CODE.fullmatch(code):
        raise ValueError("Məhsul kodu 2–64 simvolluq hərf/rəqəm kodu olmalıdır.")
    if not 2 <= len(name) <= 120 or any(ord(char) < 32 for char in name):
        raise ValueError("Məhsul adı 2–120 simvol olmalıdır.")
    return ProductRegistrationDraft(normalize_product_code(code), name)
