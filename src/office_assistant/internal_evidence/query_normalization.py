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


def strong_identifiers(value: str) -> tuple[str, ...]:
    """Extract exact machine-like identifiers; never typo-correct them."""

    identifiers = {
        re.sub(r"[-\s]+", "", match.group(1)).upper()
        for match in _STRONG_IDENTIFIER.finditer(unicodedata.normalize("NFKC", value))
    }
    return tuple(sorted(identifiers))
