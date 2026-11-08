"""Deterministic reading of the documents a photo matched, for the photo answer itself.

The owner sends a picture and expects the answer to say what the source document says,
without first pressing a "Mənbə N" button. This module is deliberately free of Telegram
and database types so the quoting rules are testable offline.

Only text already accepted by ``DocumentRelevancePolicy`` for this question is quoted, a
segment must literally overlap the asked subject, and nothing is paraphrased or inferred:
a document whose lines do not mention the subject produces no block at all. Retrieved
document text stays UNTRUSTED DATA (AGENTS.md invariant 1) — it is presented as a quote
and never as an instruction, a price, or stock state.
"""

import re
from collections.abc import Sequence

from office_assistant.integrations.telegram import messages
from office_assistant.internal_evidence.models import DocumentEvidence
from office_assistant.internal_evidence.query_normalization import (
    normalize_user_query,
    strong_identifiers,
)

MAX_SOURCE_FILES = 2
MAX_QUOTES_PER_FILE = 2
QUOTE_CHARS = 300

# Words shorter than this carry no subject (case-folded, ASCII-folded forms).
_MIN_TERM_CHARS = 3
_MIN_SEGMENT_CHARS = 8
_SEGMENT = re.compile(r"[^\n.;:|]+")


def _subject_terms(question: str) -> set[str]:
    """Folded tokens and strong identifiers the question actually asks about."""

    terms = {
        term for term in normalize_user_query(question).split() if len(term) >= _MIN_TERM_CHARS
    }
    terms.update(identifier.casefold() for identifier in strong_identifiers(question))
    return terms


def _overlaps(segment: str, terms: set[str]) -> bool:
    folded = normalize_user_query(segment)
    return any(term in folded for term in terms)


def _quote(segment: str, limit: int) -> str:
    cleaned = " ".join(segment.split())
    if len(cleaned) > limit:
        return f"{cleaned[: limit - 1].rstrip()}…"
    return cleaned


def render_photo_source_facts(
    question: str,
    evidences: Sequence[DocumentEvidence],
    *,
    max_files: int = MAX_SOURCE_FILES,
    max_quotes: int = MAX_QUOTES_PER_FILE,
    quote_chars: int = QUOTE_CHARS,
) -> str | None:
    """Return the automatic source-document block, or None when nothing is quotable.

    Files keep the evidence order (the strongest visual match first) and each file gets
    at most ``max_quotes`` lines, so one photo answer can never paste a whole catalogue.
    """

    terms = _subject_terms(question)
    if not terms:
        return None
    quoted: dict[tuple[int, str], list[str]] = {}
    for evidence in evidences:
        key = (evidence.file_id, evidence.filename)
        if key in quoted and len(quoted[key]) >= max_quotes:
            continue
        if len(quoted) >= max_files and key not in quoted:
            break
        lines = quoted.setdefault(key, [])
        for segment in _SEGMENT.findall(evidence.text):
            if len(lines) >= max_quotes:
                break
            candidate = _quote(segment, quote_chars)
            if len(candidate) < _MIN_SEGMENT_CHARS or not _overlaps(candidate, terms):
                continue
            if candidate not in lines:
                lines.append(candidate)
        if not lines:
            del quoted[key]
    if not quoted:
        return None
    output: list[str] = []
    for (_, filename), quotes in quoted.items():
        output.append(messages.PHOTO_SOURCE_FACTS_LINE.format(filename=" ".join(filename.split())))
        output.extend(messages.PHOTO_SOURCE_FACTS_QUOTE.format(quote=quote) for quote in quotes)
    output.append(messages.PHOTO_SOURCE_FACTS_DISCLAIMER)
    return "\n".join(output)
