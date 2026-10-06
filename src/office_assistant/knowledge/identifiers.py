"""Conservative identifier aliases for exact-code retrieval."""

import re

_IDENTIFIER = re.compile(r"\b[A-Za-z]{1,8}[- ]?\d{1,8}(?:[- ]?[A-Za-z0-9]+)*\b")


def identifier_aliases(text: str) -> list[str]:
    aliases: set[str] = set()
    for match in _IDENTIFIER.finditer(text):
        raw = match.group(0)
        compact = re.sub(r"[-\s]+", "", raw).lower()
        spaced = re.sub(r"[-\s]+", " ", raw).lower()
        aliases.update({raw.lower(), compact, spaced})
    return sorted(alias for alias in aliases if alias)


def lexical_text(text: str) -> str:
    aliases = identifier_aliases(text)
    return f"{text} {' '.join(aliases)}".strip()


def normalize_query(query: str) -> str:
    return lexical_text(query)
