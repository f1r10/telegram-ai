"""The flag-gated entry point for entity resolution (Phase 3).

Callers ask one question — "who is this mention?" — and get an honest answer: a single identity, the
candidates a person must review, or nothing. With ``gi_entity_resolution_enabled`` off the service
reads no row and returns ``DISABLED``, so an opted-out deployment behaves exactly as it did before
Phase 3 and the flag can be turned off instantly.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.core.settings import Settings
from office_assistant.global_intelligence.entities.access import entity_access
from office_assistant.global_intelligence.entities.resolution import (
    Resolution,
    disabled,
    resolve,
)
from office_assistant.global_intelligence.entities.store import EntityStore


class EntityResolutionService:
    """Resolve a mention for one authenticated actor, within what that actor may see."""

    #: The flag this milestone rides on. Off by default in ``Settings`` and ``.env.example``.
    FLAG = "gi_entity_resolution_enabled"

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        enabled: bool = False,
    ) -> None:
        self.session_factory = session_factory
        self.enabled = enabled

    async def resolve(
        self,
        telegram_user_id: int,
        mention: str,
        *,
        identifier: str | None = None,
        record_candidates: bool = True,
    ) -> Resolution:
        """Resolve ``mention`` for the actor behind ``telegram_user_id``.

        The access decision comes first and comes from the shared function: a user who may use no
        entity feature resolves nothing, and the query never loads a row they may not see. An
        ambiguous or near match is stored as a pending candidate (never merged) unless the caller
        asks not to write, which is what a read-only preview uses.
        """

        if not self.enabled:
            return disabled(mention)
        async with self.session_factory.begin() as session:
            access = await entity_access(session, telegram_user_id)
            store = EntityStore(session)
            records = await store.load(access)
            outcome = resolve(mention, identifier=identifier, records=records)
            if record_candidates and outcome.candidates:
                await store.record_candidates(access, outcome)
            return outcome


def entity_resolution_service(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> EntityResolutionService:
    """Build the service from configuration, so the flag has one place to live."""

    return EntityResolutionService(session_factory, enabled=settings.gi_entity_resolution_enabled)
