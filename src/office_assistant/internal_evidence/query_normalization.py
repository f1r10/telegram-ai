"""Light conversational normalization without changing authoritative identities."""

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")
_STRONG_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z]{1,8}(?:[- ]?\d{1,8}[A-Za-z0-9]*)+)(?![A-Za-z0-9])"
)
_ASCII_TRANSLATION = str.maketrans(
    {"ə": "e", "ı": "i", "ö": "o", "ü": "u", "ş": "s", "ç": "c", "ğ": "g"}
)
_RETRIEVAL_CANONICAL = {
    "qiymet": "qiymət",
    "mehsul": "məhsul",
    "teklif": "təklif",
    "menbe": "mənbə",
    "gonder": "göndər",
    "zemanet": "zəmanət",
    "odenis": "ödəniş",
    "sert": "şərt",
    "sened": "sənəd",
    "ferq": "fərq",
}


def normalize_user_query(value: str) -> str:
    """Return an ASCII-friendly signal for intent matching only."""

    normalized = unicodedata.normalize("NFKC", value).casefold().translate(_ASCII_TRANSLATION)
    normalized = re.sub(r"[^\w\s-]", " ", normalized)
    normalized = re.sub(r"\b(\d{1,2})\s*-?\s*c[ıiuu]?\b", r"\1ci", normalized)
    return _WHITESPACE.sub(" ", normalized).strip()


def retrieval_query(value: str) -> str:
    """Keep the original query and add narrow ASCII/canonical lexical hints."""

    normalized = normalize_user_query(value)
    canonical = " ".join(_RETRIEVAL_CANONICAL.get(token, token) for token in normalized.split())
    return " ".join(dict.fromkeys(item for item in (value.strip(), normalized, canonical) if item))


_AZ_DIACRITICS = frozenset("əƏıöÖüÜşŞçÇğĞ")
_IDENTIFIER_CORE = re.compile(r"^(.*\d)([A-Za-z]*)$")
_LEADING_LETTERS = re.compile(r"^[^\W\d_]+")
# Conservative ASCII-folded Azerbaijani case suffixes that are never genuine model-code tails.
_AZ_CASE_SUFFIXES = frozenset({"dan", "den", "lar", "ler", "nin", "nun", "na", "ne"})


def strong_identifiers(value: str, *, strip_case_suffix: bool = False) -> tuple[str, ...]:
    """Extract exact machine-like identifiers; never typo-correct them.

    With ``strip_case_suffix`` an Azerbaijani case suffix attached to a code is removed
    ("M404dən" -> "M404") while a genuine ASCII model tail ("M404dn") is preserved. The default
    keeps the shipped behaviour byte-identical; the new understanding layer opts in.
    """

    text = unicodedata.normalize("NFKC", value)
    identifiers: set[str] = set()
    for match in _STRONG_IDENTIFIER.finditer(text):
        token = re.sub(r"[-\s]+", "", match.group(1))
        if strip_case_suffix:
            token = _strip_case_suffix(token, text, match.end(1))
        identifiers.add(token.upper())
    return tuple(sorted(identifiers))


def _strip_case_suffix(token: str, text: str, end: int) -> str:
    """Drop an Azerbaijani inflection absorbed into the ASCII tail of a code.

    The identifier regex stops at the first non-ASCII letter, so "M404dən" captures "M404d".
    A following Azerbaijani diacritic ("ən") proves the tail is an inflection, not a model
    suffix; a pure-ASCII tail is only dropped when it spells a known case suffix.
    """

    core = _IDENTIFIER_CORE.match(token)
    if core is None or not core.group(2):
        return token
    following = _LEADING_LETTERS.match(text[end:])
    continuation = following.group(0) if following else ""
    if any(char in _AZ_DIACRITICS for char in continuation):
        return core.group(1)
    folded = (core.group(2) + continuation).translate(_ASCII_TRANSLATION).casefold()
    return core.group(1) if folded in _AZ_CASE_SUFFIXES else token
