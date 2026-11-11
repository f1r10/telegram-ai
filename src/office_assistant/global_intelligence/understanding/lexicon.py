"""AZ/TR/RU/EN domain vocabulary for query understanding (architecture §3, defect F1/F3).

The shipped ``internal_evidence.router`` keeps its own small ``internal_markers`` tuple; this
lexicon is the *extended* vocabulary the new understanding layer uses. It is additive: the
router is untouched so flag-off behaviour stays byte-identical. Stems are written in the
ASCII-folded, case-folded form produced by ``normalize_user_query`` (ə->e, ı->i, ç->c, ...),
except Cyrillic stems which normalization leaves intact.
"""

from __future__ import annotations

# Canonical concept -> accepted normalized surface stems. A concept is recognized when a
# query token starts with one of its stems, so Azerbaijani inflections ("printerinə",
# "kabellər", "anbarda") match without enumerating every suffix.
CONCEPT_STEMS: dict[str, tuple[str, ...]] = {
    "printer": ("printer", "cap", "принтер"),
    "cable": ("kabel", "cable", "kablo", "кабел"),
    "camera": ("kamera", "camera", "камера"),
    "stock": ("stok", "anbar", "qalib", "elde", "elinde", "sklad", "остат"),
    "price": ("qiymet", "price", "fiyat", "cena", "цен"),
    "warranty": ("zemanet", "warranty", "garanti", "гарант"),
    "contract": ("muqavile", "contract", "sozlesme", "dogovor", "договор"),
    "supplier": ("techizatci", "supplier", "tedarikci", "postavshik", "поставщ"),
    "order": ("sifaris", "order", "siparis", "zakaz", "заказ"),
    "usb-b": ("usb",),
}

# Concepts that, on their own, signal a company/internal question rather than small talk.
DOMAIN_CONCEPTS: frozenset[str] = frozenset(CONCEPT_STEMS)

# Device -> connector/cable families inferred from domain knowledge, never from prose
# similarity (defect F3). Each inference is a PROBABLE hypothesis until a source corroborates
# it (architecture §4, promt.md §5). These are hypotheses, not confirmed compatibility facts.
DEVICE_CONNECTORS: dict[str, tuple[str, ...]] = {
    "printer": ("usb-b", "power"),
    "camera": ("bnc", "rj45", "power"),
}

# Extended internal markers: the shipped router tuple plus the measured missing company
# vocabulary (kamera, kabel, printer, stok, anbar, təchizatçı, müqavilə, ...). Used only by the
# new planner; the shipped router keeps its own tuple.
INTERNAL_MARKERS: frozenset[str] = frozenset(
    {
        "azn",
        "mmc",
        "bizim",
        "musteri",
        "menbe",
        "fayl",
        "sened",
        "mehsul",
        "bizde",
        "biz ",
        "satiriq",
        "neceye",
        "qiymet",
        "markali",
        "printer",
        "kabel",
        "kamera",
        "stok",
        "anbar",
        "qalib",
        "zemanet",
        "muqavile",
        "techizatci",
        "sifaris",
        "satis",
        "cap",
    }
)

# Known-correct domain words used as the *only* candidates for bounded typo correction. Codes
# and identifiers are never corrected (promt.md §5); short words (<4 chars) are excluded by the
# corrector to avoid collisions. "plinter" -> "printer" is the measured F2 case.
TYPO_VOCABULARY: frozenset[str] = frozenset(
    {
        "printer",
        "kabel",
        "cable",
        "kablo",
        "kamera",
        "camera",
        "stok",
        "anbar",
        "qiymet",
        "price",
        "fiyat",
        "zemanet",
        "warranty",
        "garanti",
        "muqavile",
        "contract",
        "techizatci",
        "supplier",
        "sifaris",
        "order",
        "qalib",
        "musteri",
        "mehsul",
        "sened",
        "teklif",
    }
)
