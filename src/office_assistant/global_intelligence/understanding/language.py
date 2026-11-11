"""Cheap, deterministic language hint for the understanding stage (architecture §3).

This is a signal, not a classifier: Cyrillic script implies Russian, Azerbaijani diacritics or
function words imply Azerbaijani, Turkish function words imply Turkish, otherwise English. It
never calls a provider and never changes routing on its own.
"""

from __future__ import annotations

import re

_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_ASCII_TRANSLATION = str.maketrans(
    {"ə": "e", "ı": "i", "ö": "o", "ü": "u", "ş": "s", "ç": "c", "ğ": "g"}
)
_AZ_WORDS = frozenset(
    {"ucun", "hansi", "nece", "qalib", "kabel", "kamera", "bizde", "sened", "muqavile", "qiymet"}
)
_TR_WORDS = frozenset({"icin", "hangi", "fiyat", "kablo", "tedarikci", "sozlesme", "stok"})


def detect_language(value: str) -> str:
    """Return one of ``az``, ``tr``, ``ru``, ``en`` for the raw user question."""

    if _CYRILLIC.search(value):
        return "ru"
    folded = value.casefold().translate(_ASCII_TRANSLATION)
    tokens = set(_WORD.findall(folded))
    if "ə" in value.casefold() or tokens & _AZ_WORDS:
        return "az"
    if tokens & _TR_WORDS:
        return "tr"
    return "en"
