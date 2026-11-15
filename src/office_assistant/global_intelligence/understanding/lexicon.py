"""Domain vocabulary for the Global Intelligence Engine (AZ / TR / RU / EN).

Two independent vocabularies live here:

``INTERNAL_MARKERS``
    Normalized (ASCII-folded, lower-case) substrings that prove a question is about the
    company's own records. The tuple was hard-coded in
    ``internal_evidence/router.py`` until Phase 1; it is moved here and extended with the
    vocabulary the Phase 0 audit measured as missing (defect F1 in
    ``docs/audit/FAILURE_ANALYSIS.md``). Markers are matched with a plain substring test
    against the folded query, so entries must stay folded and must never be a fragment of
    an unrelated word.

``CONCEPTS``
    Canonical concepts the understanding layer recognizes. ``stems`` are prefix stems
    matched against folded query tokens; ``surfaces`` are the concrete normalized word
    forms the bounded typo corrector is allowed to suggest. Canonical names are the ones
    the evaluation scenarios assert (``tests/evaluation/scenarios.py``).

Nothing here is user-facing text, so no Azerbaijani display string belongs in this module
(``AGENTS.md``: bot-facing strings live in ``integrations/telegram/messages.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# --------------------------------------------------------------------------------------------------
# Internal-record markers (moved from internal_evidence/router.py, extended in Phase 1)
# --------------------------------------------------------------------------------------------------

_PHASE0_MARKERS: Final[tuple[str, ...]] = (
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
)

# Measured missing vocabulary (F1): "Plinter üçün kabel var?" and "Kamera üçün hansı
# kabellər var?" were answered from model memory because none of these words counted.
_PHASE1_MARKERS: Final[tuple[str, ...]] = (
    "kamera",
    "kabel",
    "printer",
    "stok",
    "anbar",
    "qalib",
    "techizatci",
    "muqavile",
    "kontrakt",
    "konnektor",
    "kontakt",
    "partiya",
    "qutu",
    "cihaz",
    "avadanliq",
    "kartric",
    "toner",
    "zemanet",
    "satis",
    "sifaris",
    "teklif",
)

INTERNAL_MARKERS: Final[tuple[str, ...]] = _PHASE0_MARKERS + _PHASE1_MARKERS

# --------------------------------------------------------------------------------------------------
# Availability wording (moved from internal_evidence/router.py)
# --------------------------------------------------------------------------------------------------

AVAILABILITY_TOKENS: Final[frozenset[str]] = frozenset({"var", "varmi", "yox", "yoxdur", "yoxmu"})

AVAILABILITY_PREFIXES: Final[tuple[str, ...]] = ("stok", "anbar", "elde", "elinde")

# --------------------------------------------------------------------------------------------------
# Concepts
# --------------------------------------------------------------------------------------------------

DEVICE: Final[str] = "device"
ACCESSORY: Final[str] = "accessory"
BUSINESS: Final[str] = "business"


@dataclass(frozen=True, slots=True)
class ConceptDefinition:
    """One canonical concept: how it is matched and which words may be corrected into it."""

    canonical: str
    kind: str
    stems: tuple[str, ...]
    surfaces: tuple[str, ...]


def _concept(canonical: str, kind: str, stems: tuple[str, ...]) -> ConceptDefinition:
    """Build a definition whose correctable surfaces are derived from its own stems.

    Deriving ``surfaces`` instead of listing them twice keeps the two views from drifting:
    every stem is a legal correction target, and no correctable word can be added that the
    matcher would then fail to recognize.
    """

    return ConceptDefinition(canonical=canonical, kind=kind, stems=stems, surfaces=stems)


CONCEPTS: Final[tuple[ConceptDefinition, ...]] = (
    # Devices.
    _concept("printer", DEVICE, ("printer", "cap", "yazici")),
    _concept("camera", DEVICE, ("kamera", "camera", "fotoaparat", "videokamera")),
    _concept("scanner", DEVICE, ("skaner", "scanner")),
    _concept("monitor", DEVICE, ("monitor", "ekran")),
    # Accessories and connectors.
    _concept("cable", ACCESSORY, ("kabel", "cable", "kabla")),
    _concept("connector", ACCESSORY, ("konnektor", "connector", "razem", "shteker")),
    _concept("adapter", ACCESSORY, ("adapter", "adaptor", "cevirici")),
    _concept("power-supply", ACCESSORY, ("guc", "enerji", "power", "blok", "adapter")),
    _concept("usb-b", ACCESSORY, ("usb",)),
    _concept("ethernet", ACCESSORY, ("ethernet", "lan", "rj45", "poe")),
    _concept("coax", ACCESSORY, ("bnc", "koaksial", "coax")),
    _concept("hdmi", ACCESSORY, ("hdmi", "displayport")),
    # Business concepts.
    _concept("stock", BUSINESS, ("stok", "anbar", "qalib", "elde", "miqdar")),
    _concept("price", BUSINESS, ("qiymet", "price", "maya")),
    _concept("warranty", BUSINESS, ("zemanet", "warranty")),
    _concept("contract", BUSINESS, ("muqavile", "kontrakt", "contract")),
    _concept("supplier", BUSINESS, ("techizatci", "tedarikci", "supplier")),
    _concept("order", BUSINESS, ("sifaris", "order")),
    _concept("payment", BUSINESS, ("odenis", "odeyib", "payment")),
    _concept("proposal", BUSINESS, ("teklif", "proposal")),
)

CONCEPT_BY_CANONICAL: Final[dict[str, ConceptDefinition]] = {
    item.canonical: item for item in CONCEPTS
}

#: Every word the bounded typo corrector may suggest, folded and de-duplicated.
CORRECTABLE_VOCABULARY: Final[tuple[str, ...]] = tuple(
    sorted({surface for item in CONCEPTS for surface in item.surfaces})
)

#: Folded stems that mark a question as being about a device or one of its accessories.
#: Derived from ``CONCEPTS`` so the shipped router and the understanding layer can never
#: drift apart. The router tests these as prefixes of folded tokens, which recognizes the
#: inflected Azerbaijani forms ("kabellər", "printerə", "kameranın") without a stemmer.
#: Unlike the typo corrector this never invents a word the user did not write, so it is
#: safe to use on the routing hot path.
DEVICE_ACCESSORY_STEMS: Final[tuple[str, ...]] = tuple(
    dict.fromkeys(
        stem for item in CONCEPTS if item.kind in (DEVICE, ACCESSORY) for stem in item.stems
    )
)

# --------------------------------------------------------------------------------------------------
# Compatibility hypotheses: device -> connector/cable families it could need
# --------------------------------------------------------------------------------------------------

# These are *hypotheses*, never facts (promt.md 5, "concept expansion ... as hypotheses to be
# verified against evidence"; 9, "compatibility is computed from typed attributes"). Phase 5
# verifies them against connector attributes; Phase 1 only surfaces them for retrieval.
DEVICE_CONNECTOR_HYPOTHESES: Final[dict[str, tuple[str, ...]]] = {
    "printer": ("usb-b", "ethernet", "power-supply"),
    "camera": ("ethernet", "coax", "power-supply", "hdmi"),
    "scanner": ("usb-b", "power-supply"),
    "monitor": ("hdmi", "power-supply"),
}

# --------------------------------------------------------------------------------------------------
# Decomposition
# --------------------------------------------------------------------------------------------------

SUB_QUESTION_DELIMITERS: Final[tuple[str, ...]] = (" və ", " ve ", " həm ", " hem ", " and ")

SUB_QUESTION_PUNCTUATION: Final[tuple[str, ...]] = (",", ";")

# Source groups a sub-question needs, keyed by the canonical concepts it holds.
SUB_QUESTION_SOURCE_HINTS: Final[dict[str, tuple[str, ...]]] = {
    "stock": ("postgres",),
    "price": ("postgres", "document"),
    "payment": ("postgres",),
    "proposal": ("postgres", "document"),
    "contract": ("document",),
    "supplier": ("document", "postgres"),
    "warranty": ("document",),
    "cable": ("document", "graph"),
    "connector": ("document", "graph"),
    "adapter": ("document", "graph"),
    "usb-b": ("document", "graph"),
    "ethernet": ("document", "graph"),
    "coax": ("document", "graph"),
    "hdmi": ("document", "graph"),
    "power-supply": ("document", "graph"),
    "printer": ("document",),
    "camera": ("document",),
}
