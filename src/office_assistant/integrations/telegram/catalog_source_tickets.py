"""Short-lived actor-bound original-PDF tickets for catalog photo suggestions.

These are only a navigation handle, never a product confirmation or an authorization
to fetch arbitrary FileObject IDs. The delivery service rechecks the DB lineage.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from time import monotonic

from office_assistant.processing.catalog_visual_candidates import PdfPageCandidate

CATALOG_SOURCE_TICKET_KEY = "office_catalog_source_ticket"
CATALOG_SOURCE_TTL_SECONDS = 600
MAX_SOURCE_BUTTONS = 3


@dataclass(frozen=True, slots=True)
class CatalogSource:
    number: int
    file_id: int
    occurrence_id: int


@dataclass(frozen=True, slots=True)
class CatalogSourceTicket:
    token: str
    actor_telegram_id: int
    expires_at: float
    sources: tuple[CatalogSource, ...]


def issue_catalog_ticket(
    actor_telegram_id: int,
    matches: tuple[PdfPageCandidate, ...],
) -> CatalogSourceTicket | None:
    """Issue at most three original file links, deduplicated by PDF ID."""

    if actor_telegram_id <= 0:
        return None
    sources: list[CatalogSource] = []
    used_files: set[int] = set()
    for match in matches:
        file_id = match.source_file_id
        occurrence_id = match.occurrence_id
        if (
            file_id is None
            or occurrence_id is None
            or file_id <= 0
            or occurrence_id <= 0
            or file_id in used_files
        ):
            continue
        sources.append(CatalogSource(len(sources) + 1, file_id, occurrence_id))
        used_files.add(file_id)
        if len(sources) >= MAX_SOURCE_BUTTONS:
            break
    if not sources:
        return None
    return _ticket(actor_telegram_id, tuple(sources))


def issue_ranked_catalog_ticket(
    actor_telegram_id: int,
    entries: Sequence[tuple[int, int, int]],
) -> CatalogSourceTicket | None:
    """Label every visual source button with the rank the user just read.

    A ranked visual page mixes confirmed product pictures and document
    occurrences, so its rows are numbered overall (1, 2, 3 ...) while only some of
    those rows carry a source file. Renumbering the file buttons 1..3 by their own
    position silently swaps them onto a different candidate, which is why the
    per-file sequence above is not reused here: `entries` pairs each printed rank
    with its exact occurrence, so every later button press and preview resolves the
    pair the user saw instead of an unrelated row.
    """

    if actor_telegram_id <= 0:
        return None
    sources: list[CatalogSource] = []
    used_occurrences: set[int] = set()
    for number, file_id, occurrence_id in entries:
        if number < 1 or file_id <= 0 or occurrence_id <= 0 or occurrence_id in used_occurrences:
            continue
        sources.append(CatalogSource(number, file_id, occurrence_id))
        used_occurrences.add(occurrence_id)
        if len(sources) >= MAX_SOURCE_BUTTONS:
            break
    if not sources:
        return None
    return _ticket(actor_telegram_id, tuple(sources))


def _ticket(
    actor_telegram_id: int,
    sources: tuple[CatalogSource, ...],
) -> CatalogSourceTicket:
    return CatalogSourceTicket(
        token=secrets.token_urlsafe(8),
        actor_telegram_id=actor_telegram_id,
        expires_at=monotonic() + CATALOG_SOURCE_TTL_SECONDS,
        sources=sources,
    )


def resolve_catalog_ticket(
    state: object,
    actor_telegram_id: int,
    number: int,
    *,
    token: str | None = None,
    now: float | None = None,
) -> CatalogSource | None:
    """Reject expired, cross-user and replayed tokens before DB/file access."""

    if not isinstance(state, CatalogSourceTicket):
        return None
    if (
        state.actor_telegram_id != actor_telegram_id
        or (monotonic() if now is None else now) >= state.expires_at
        or (token is not None and not secrets.compare_digest(token, state.token))
    ):
        return None
    return next((source for source in state.sources if source.number == number), None)


def resolve_catalog_source(
    state: object,
    actor_telegram_id: int,
    requested: int,
) -> CatalogSource | None:
    """Resolve a spoken source as the printed rank first, then as the Nth offered row.

    Ranked visual buttons keep the candidate's overall number, which may be 4, 7 or
    11 rather than 1, 2, 3. A person who says "birinci mənbə" means the first source
    actually offered, not the label "1", so an unmatched rank falls back to ordinal
    position. Expiry and actor binding are rechecked, never bypassed.
    """

    direct = resolve_catalog_ticket(state, actor_telegram_id, requested)
    if direct is not None or not isinstance(state, CatalogSourceTicket):
        return direct
    if state.actor_telegram_id != actor_telegram_id or monotonic() >= state.expires_at:
        return None
    if not 1 <= requested <= len(state.sources):
        return None
    return state.sources[requested - 1]
