"""Conservative web query and URL normalization."""

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from office_assistant.internal_evidence.query_normalization import strong_identifiers
from office_assistant.web_evidence.models import WebSearchRequest

_SPACE = re.compile(r"\s+")
_EXPLICIT_FRESH_WORDS = re.compile(
    r"\b(?:indi|hazırda|bu\s+gün|bugün|latest|current|today|news)\b|\bson\s+xəbər\b",
    re.IGNORECASE,
)
# Prices and stock change often, so their cached answer must stay short-lived even when the
# user did not ask for a time window.
_TIME_SENSITIVE_WORDS = re.compile(
    r"\b(?:qiymət|qiyməti|qiymətlər|stok|məhdud|satış|kurs|kurusu)\b",
    re.IGNORECASE,
)
_TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def normalize_web_query(value: str) -> str:
    """Normalize Unicode/spacing while preserving punctuation and identifiers."""

    return _SPACE.sub(" ", unicodedata.normalize("NFKC", value)).strip()


def is_fresh_query(value: str) -> bool:
    """Only an explicit 'right now / latest' request narrows the search to one day."""

    return bool(_EXPLICIT_FRESH_WORDS.search(normalize_web_query(value)))


def is_time_sensitive_query(value: str) -> bool:
    """Whether a cached answer for this query goes stale quickly."""

    normalized = normalize_web_query(value)
    return bool(
        _TIME_SENSITIVE_WORDS.search(normalized) or _EXPLICIT_FRESH_WORDS.search(normalized)
    )


# An undated hit stays: absence of a date is not proof of age, and SearXNG rarely supplies one.
_WINDOW_DAYS: dict[str, int] = {"day": 1, "month": 31, "year": 366}


def is_within_window(
    published_at: datetime | None,
    freshness: Literal["day", "month", "year"] | None,
    *,
    now: datetime,
) -> bool:
    """Whether a hit's own publication date satisfies the requested time window."""

    if published_at is None or freshness is None:
        return True
    window = _WINDOW_DAYS.get(freshness)
    if window is None:
        return True
    start = published_at if published_at.tzinfo else published_at.replace(tzinfo=UTC)
    return start >= now - timedelta(days=window)


def matches_requested_identifier(query: str, title: str, snippet: str) -> bool:
    """Reject search hits for other objects before fetching or synthesizing."""
    requested = set(strong_identifiers(query))
    return not requested or bool(requested.intersection(strong_identifiers(f"{title}\n{snippet}")))


def clean_answer_language(answer: str) -> str:
    """Localize source labels and repair Cyrillic letters only in mixed-script words."""
    labels = {
        "STRUCTURED_AUTHORITATIVE": "Daxili biznes bazası",
        "INTERNAL_DOCUMENT": "Daxili sənəd",
        "EXTERNAL_WEB": "İnternet",
    }
    letters = str.maketrans(
        {
            "Д": "D",
            "д": "d",
            "З": "Z",
            "з": "z",
            "А": "A",
            "а": "a",
            "Е": "E",
            "е": "e",
            "О": "O",
            "о": "o",
            "С": "C",
            "с": "c",
            "Х": "X",
            "х": "x",
            "Р": "P",
            "р": "p",
            "І": "I",
            "і": "i",
            "Ј": "J",
            "ј": "j",
        }
    )

    def repair(match: re.Match[str]) -> str:
        word = match.group()
        if word.startswith(("http://", "https://", "`")):
            return word
        if word in labels:
            return labels[word]
        if re.search(r"[A-Za-zƏəİıÖöÜüŞşÇçĞğ]", word) and re.search(r"[\u0400-\u04ff]", word):
            return word.translate(letters)
        return word

    return re.sub(r"https?://\S+|`[^`]*`|\w+", repair, answer)


def cache_key(request: WebSearchRequest) -> tuple[str, str]:
    normalized = normalize_web_query(request.query)
    payload = {
        "query": normalized.casefold(),
        "max_results": request.max_results,
        "language": request.language,
        "freshness": request.freshness,
        "allowed_domains": sorted(item.casefold() for item in request.allowed_domains),
        "blocked_domains": sorted(item.casefold() for item in request.blocked_domains),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), normalized


def canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query: list[tuple[str, str]] = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        folded = key.casefold()
        if folded.startswith("utm_") or folded in _TRACKING_KEYS:
            continue
        query.append((key, item))
    scheme = parts.scheme.casefold()
    hostname = (parts.hostname or "").casefold().rstrip(".")
    port = parts.port
    netloc = hostname
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, urlencode(query, doseq=True), ""))
