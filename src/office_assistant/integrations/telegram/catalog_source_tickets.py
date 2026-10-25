"""Short-lived actor-bound original-PDF tickets for catalog photo suggestions.

These are only a navigation handle, never a product confirmation or an authorization
to fetch arbitrary FileObject IDs. The delivery service rechecks the DB lineage.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from time import monotonic

from office_assistant.processing.catalog_visual_candidates import PdfPageCandidate

CATALOG_SOURCE_TICKET_KEY = "office_catalog_source_ticket"
CATALOG_SOURCE_TTL_SECONDS = 600


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
        if len(sources) >= 3:
            break
    if not sources:
        return None
    return CatalogSourceTicket(
        token=secrets.token_urlsafe(8),
        actor_telegram_id=actor_telegram_id,
        expires_at=monotonic() + CATALOG_SOURCE_TTL_SECONDS,
        sources=tuple(sources),
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
