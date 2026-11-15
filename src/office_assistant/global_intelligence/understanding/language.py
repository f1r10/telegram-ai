"""Language detection for Azerbaijani, Turkish, Russian, English and mixed input.

The product is Azerbaijani-first (promt.md 14), so an unmarked, Latin-script question is
treated as Azerbaijani. That default is recorded in ``docs/OPEN-QUESTIONS.md`` rather than
guessed silently.

ASSUMPTION: an undecidable short message is Azerbaijani, not English.
"""

from __future__ import annotations

import re
from typing import Final

from .normalizer import normalize

CYRILLIC = re.compile(r"[\u0400-\u04FF]")

#: Azerbaijani schwa: present in Azerbaijani, absent from Turkish.
AZ_SPECIFIC_CHARACTERS: Final[str] = "ə"

DEFAULT_LANGUAGE: Final[str] = "az"

AZ_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "ucun",
        "hansi",
        "nece",
        "necedi",
        "necedir",
        "nedir",
        "varmi",
        "lazimdir",
        "bizde",
        "qiymeti",
        "zemanet",
        "muqavile",
        "sened",
        "gonder",
        "melumat",
        "satis",
        "anbar",
    }
)
TR_MARKERS: Final[frozenset[str]] = frozenset(
    {"icin", "hangi", "midir", "fiyat", "urun", "lazim", "stokta", "kac"}
)
EN_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "which",
        "what",
        "how",
        "many",
        "the",
        "is",
        "are",
        "does",
        "need",
        "stock",
        "price",
        "cable",
        "printer",
        "warranty",
    }
)


def detect_language(question: str) -> str:
    """Return one of ``az``, ``tr``, ``ru``, ``en`` or ``mixed``."""

    if CYRILLIC.search(question):
        return "ru"
    if AZ_SPECIFIC_CHARACTERS in question.casefold():
        return "az"
    tokens = set(normalize(question).tokens)
    scores = {
        "az": len(tokens & AZ_MARKERS),
        "tr": len(tokens & TR_MARKERS),
        "en": len(tokens & EN_MARKERS),
    }
    best = max(scores.values())
    if best == 0:
        return DEFAULT_LANGUAGE
    winners = sorted(language for language, score in scores.items() if score == best)
    return winners[0] if len(winners) == 1 else "mixed"
