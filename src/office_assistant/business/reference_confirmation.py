"""Deterministic reading of a caption that names the product in a picture (Master Spec §23.7).

``Bu KA45-dir, yadda saxla`` is an explicit human statement, and §23.7 requires it to cost no model
tokens when the code can be read off the text. So this module is a pattern, not a guesser, and four
rules keep an ordinary caption from being read as an instruction the user never wrote (invariant 1):

* the save phrase has to close the caption, so prose *about* saving stays an upload;
* a code has to be *asserted* — carried by an Azerbaijani naming copula (``KA45-dir``,
  ``KA45 dır``), because a bare code inside a sentence is a mention and a mention is not consent;
* a denial (``deyil``) right after the name, or a question mark anywhere in the caption, cancels the
  statement: the user then said the picture is *not* that product, or asked which product it is;
* more than one distinct code in the caption is reported as ambiguity instead of one being chosen.

A code also has to contain a digit, which is what stops an ordinary word ("şəkil", "məhsul") from
becoming an identity claim. Photos only: a document caption carrying the phrase still reaches the
service, which finds no picture in that message and refuses, so nothing is inferred from a document
(Q32, resolved by the owner 2026-10-10).
"""

import re
import unicodedata
from dataclasses import dataclass

from office_assistant.business.normalization import normalize_product_code

# ``\W*$`` closes the phrase without letting a negation in: ``saxlama``, ``saxlamaq`` and
# ``saxlanılsın`` all keep letter characters after ``saxla``, while ``!``, ``.`` or an emoji do not.
_SAVE_PHRASE = re.compile(r"\byadda\s+saxla\W*$", re.IGNORECASE)
_CODE_LIKE = re.compile(r"^(?=.*[0-9])[A-Za-z0-9][A-Za-z0-9._\-]+$")
# The Azerbaijani copula that attaches the naming to the thing: ``KA45-dir``, ``KA45-dır``,
# ``KA45-dur``, ``KA45-dür``, and the clipped ``-dı``/``-di`` a fast typist leaves it as. The hyphen
# is optional, because ``KA45dır`` is the same statement typed without stopping to separate it.
_COPULA = re.compile(r"-?(?:d[ıiəuü]r|d[ıiəuü])$", re.IGNORECASE)
# The same copula standing as its own word: ``Bu KA45 dır, yadda saxla``.
_COPULA_ALONE = re.compile(r"^d[ıiəuü]r?$", re.IGNORECASE)
_DENIAL = re.compile(r"^deyil$", re.IGNORECASE)
_PUNCTUATION = re.compile(r"[,.:;\"“”()\[\]]")


@dataclass(frozen=True, slots=True)
class ProductPictureConfirmation:
    """The caption asserted exactly one product as the subject of the picture."""

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
    # A question mark anywhere means the user was asking what the picture shows, not saying it.
    if ending is None or "?" in text:
        return NoPictureConfirmation()
    words = _PUNCTUATION.sub(" ", text[: ending.start()]).split()
    # Canonical code -> asserted as the picture's identity at least once, in the order the caption
    # named them. A product named twice is still one product, so identity decides ambiguity.
    codes: dict[str, bool] = {}
    for index, word in enumerate(words):
        code = _code_of(word)
        if code is None:
            continue
        codes[code] = codes.get(code, False) or _is_asserted(words, index)
    if len(codes) > 1:
        return AmbiguousPictureConfirmation(tuple(codes))
    named = next((code for code, asserted in codes.items() if asserted), None)
    if named is None:
        return NoPictureConfirmation()
    return ProductPictureConfirmation(named)


def _code_of(word: str) -> str | None:
    """The product code a word carries, or nothing when it carries none.

    The copula is cut before the lookup, so ``KA45-dir`` reaches the registry as ``KA45`` and the
    name cannot be missed because of a suffix the user attached to it.
    """

    name = _COPULA.sub("", word.strip())
    if not _CODE_LIKE.match(name):
        return None
    try:
        return normalize_product_code(name)
    except ValueError:
        return None


def _is_asserted(words: list[str], index: int) -> bool:
    """Whether the code at ``index`` is named as what the picture shows, not merely mentioned."""

    has_copula = _COPULA.search(words[index]) is not None or (
        index + 1 < len(words) and _COPULA_ALONE.match(words[index + 1]) is not None
    )
    if not has_copula:
        return False
    return not any(_DENIAL.match(word) for word in words[index + 1 : index + 3])
