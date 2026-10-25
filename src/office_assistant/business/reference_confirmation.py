"""Deterministic reading of a caption that names the product in a picture (Master Spec §23.7).

``Bu KA45-dir, yadda saxla`` is an explicit human statement, and §23.7 requires it to cost no model
tokens when the code can be read off the text. So this module is a pattern, not a guesser. Three
parts have to be present at once, or nothing is read (invariant 1):

* an explicit save or link instruction closing the caption — ``saxla``, ``yadda saxla``, ``bağla``;
* exactly one distinct product code, compared after the registry's own identity normalization, so
  ``KA-45`` and ``ka45`` are one product while ``KA45`` and ``KA46`` are two — several codes are
  reported as ambiguity and none of them is chosen;
* a binding that says this code *is* the product of the picture: an Azerbaijani naming copula
  (``KA45-dir``, ``KA45 dır``, ``KA45dır``) or a picture word right after it (``KA45 şəklini``,
  ``KA45 məhsuluna``, ``KA45 üçün``, ``KA45 məhsulunun şəklidir``, ``KA45 referans şəkil``).

A bare mention is not a binding, so ``Bu məhsul KA45, yadda saxla`` stays an ordinary caption. A
denial (``deyil`` anywhere in the naming part) and a question mark anywhere in the caption cancel
the statement, because the user then said the picture is *not* that product or asked which it is.
Prose about saving rather than an order — ``saxlama``, ``saxlanılsın``, ``yadda saxla, bu KA45-dir``
— is no instruction at all. A code keeps the letters it was typed with after normalization, so
``KA45-in`` is read as ``KA45IN`` and not guessed to be ``KA45``: inventing an identity is exactly
what strict product-code matching forbids, and the registry then answers "no such product".

Nothing is authoritative here: a parse only queues a claim and ``/saxla`` is the commit boundary
(Q31). Photos only — a document caption still reaches the service, which finds no picture in the
message and refuses (Q32).
"""

import re
import unicodedata
from dataclasses import dataclass

from office_assistant.business.normalization import normalize_product_code

# The instruction has to close the caption, and only as a whole word: ``saxlama``, ``saxlamaq`` and
# ``saxlanılsın`` all keep letters after the verb, while ``!``, ``.`` or an emoji do not.
_INSTRUCTION = re.compile(r"\b(?:saxla|bağla)\W*$", re.IGNORECASE)
_CODE_LIKE = re.compile(r"^(?=.*[0-9])[A-Za-z0-9][A-Za-z0-9._\-]+$")
# The copula that attaches the naming to the thing: ``KA45-dir``, ``KA45-dır``, ``KA45-dur``,
# ``KA45-dür``, the clipped ``-dı``/``-di``, and the same ending with no hyphen (``KA45dır``).
_COPULA = re.compile(r"-?(?:d[ıiəuü]r|d[ıiəuü])$", re.IGNORECASE)
# The same copula standing as its own word: ``Bu KA45 dır, yadda saxla``.
_COPULA_ALONE = re.compile(r"^d[ıiəuü]r?$", re.IGNORECASE)
# A code binds the picture to a product when the word after it is the picture, the product, its
# reference role, or what the picture is for: ``KA45 şəklini``, ``KA45 məhsuluna``, ``KA45 üçün``,
# ``KA45 referans şəkil kimi``. Prefixes, because Azerbaijani folds case endings into the word —
# and ``şəkli``, ``şəkilini`` and ``şəklidir`` all count, so the optional vowel is in the pattern.
_BINDING = re.compile(r"^(?:şəki?l|məhsul|referans|üçün|model)", re.IGNORECASE)
# A price is not another product code: "KA45 modelidir, 129 AZN, yadda saxla".
# Only explicit currency suffixes are ignored, not other bare numeric tokens.
_PRICE_UNIT = re.compile(r"^(?:azn|manat(?:ı)?|₼|usd|eur|tl)$", re.IGNORECASE)
_DENIAL = re.compile(r"^deyil$", re.IGNORECASE)
_PUNCTUATION = re.compile(r"[,.:;\"“”()\[\]]")


@dataclass(frozen=True, slots=True)
class ProductPictureConfirmation:
    """The caption instructed that this product's picture be kept."""

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
    """Read a save-or-link instruction that binds one product to this picture, or refuse."""

    if not caption:
        return NoPictureConfirmation()
    text = unicodedata.normalize("NFKC", caption).strip()
    instruction = _INSTRUCTION.search(text)
    # A question mark anywhere means the user was asking what the picture shows, not saying it.
    if instruction is None or "?" in text:
        return NoPictureConfirmation()
    words = _PUNCTUATION.sub(" ", text[: instruction.start()]).split()
    # Canonical code -> bound to the picture at least once, in the order the caption named them. A
    # product named twice is still one product, so identity rather than spelling decides ambiguity.
    codes: dict[str, bool] = {}
    for index, word in enumerate(words):
        code = _code_of(word)
        following = words[index + 1] if index + 1 < len(words) else ""
        # Do not mistake the amount in "129 AZN" for a competing SKU.
        if code is None or (word.isdecimal() and _PRICE_UNIT.fullmatch(following)):
            continue
        codes[code] = codes.get(code, False) or _is_bound(words, index)
    if len(codes) > 1:
        return AmbiguousPictureConfirmation(tuple(codes))
    if any(_DENIAL.match(word) for word in words):
        return NoPictureConfirmation()
    named = next((code for code, bound in codes.items() if bound), None)
    if named is None:
        return NoPictureConfirmation()
    return ProductPictureConfirmation(named)


def _code_of(word: str) -> str | None:
    """The product code a word carries, or nothing when it carries none.

    The copula is cut before the lookup, so ``KA45-dir`` reaches the registry as ``KA45`` and a
    suffix the user attached to the name cannot make the product unfindable.
    """

    name = _COPULA.sub("", word.strip())
    if not _CODE_LIKE.match(name):
        return None
    try:
        return normalize_product_code(name)
    except ValueError:
        return None


def _is_bound(words: list[str], index: int) -> bool:
    """Whether the code at ``index`` is named as the product of this picture, not mentioned."""

    following = words[index + 1] if index + 1 < len(words) else ""
    return (
        _COPULA.search(words[index]) is not None
        or _COPULA_ALONE.match(following) is not None
        or _BINDING.match(following) is not None
    )
