"""Deterministic reading of a caption that names the product in a picture (Master Spec §23.7).

``Bu KA45-dir, yadda saxla`` is an explicit human statement, and §23.7 requires it to cost no model
tokens when the code can be read off the text. So this module is a pattern, not a guesser: it only
recognizes the save phrase written in the spec, it only accepts a token shaped like a product code,
and when the caption names more than one candidate it reports the ambiguity instead of choosing.
Everything else stays an ordinary caption, which keeps a prose message from being read as an
instruction the user never wrote.
"""

import re
import unicodedata
from dataclasses import dataclass

from office_assistant.business.normalization import normalize_product_code

_SAVE_PHRASE = re.compile(r"\byadda\s+saxla[\s.!?]*$", re.IGNORECASE)
# A code has to carry a digit: that is what stops an ordinary word ("şəkil", "məhsul") from being
# read as a product identity claim. Recorded as an assumption (Q32).
_CODE_LIKE = re.compile(r"^(?=.*[0-9])[A-Za-z0-9][A-Za-z0-9._\-]+$")
# The Azerbaijani copula that attaches the naming to the thing: ``KA45-dir``, ``KA45-dır``,
# ``KA45-dur``, ``KA45-dür``, and the clipped ``-dı``/``-di`` a fast typist leaves it as.
_COPULA = re.compile(r"-(?:d[ıiəuü]r|d[ıiəuü])$", re.IGNORECASE)
_PUNCTUATION = re.compile(r"[,.:;\"“”()\[\]]")
_FILLER = frozenset({"bu"})


@dataclass(frozen=True, slots=True)
class ProductPictureConfirmation:
    """The caption named exactly one product and asked for the picture to be saved."""

    code: str


@dataclass(frozen=True, slots=True)
class AmbiguousPictureConfirmation:
    """Several product-shaped names appeared, so nothing is guessed (§23.7)."""

    codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NoPictureConfirmation:
    """An ordinary caption: this build writes nothing on the strength of it."""


ReferenceCaption = ProductPictureConfirmation | AmbiguousPictureConfirmation | NoPictureConfirmation


def parse_reference_confirmation(caption: str | None) -> ReferenceCaption:
    """Read ``Bu KA45-dir, yadda saxla`` without a model, or refuse to invent one."""

    if not caption:
        return NoPictureConfirmation()
    text = unicodedata.normalize("NFKC", caption).strip()
    ending = _SAVE_PHRASE.search(text)
    if ending is None:
        return NoPictureConfirmation()
    naming = _PUNCTUATION.sub(" ", text[: ending.start()])
    candidates = [
        code
        for word in naming.split()
        if (code := _without_copula(word)) not in _FILLER and _CODE_LIKE.match(code)
    ]
    if len(candidates) > 1:
        return AmbiguousPictureConfirmation(tuple(candidates))
    if not candidates:
        return NoPictureConfirmation()
    try:
        return ProductPictureConfirmation(normalize_product_code(candidates[0]))
    except ValueError:
        return NoPictureConfirmation()


def _without_copula(word: str) -> str:
    """The word as a name, minus an Azerbaijani copula welded onto its end."""

    return _COPULA.sub("", word.strip())
