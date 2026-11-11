"""Global Intelligence Engine feature flags (promt.md 18).

Every flag defaults to ``False``, so a deployment that does not opt in keeps the behaviour
it had before the engine existed and the engine can be switched off instantly. The flags are
read from :class:`office_assistant.core.settings.Settings` through the same environment
mechanism as every other setting.

The Phase 1 *deterministic router correction* (the extended internal vocabulary and the
device/accessory concept route) is deliberately **not** behind a flag: those questions are
internal questions that were misrouted to the general-chat path, which is a defect, not a
new engine path. The flags below gate the new orchestration stages.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from office_assistant.core.settings import Settings


class EngineFlags(BaseModel):
    """Master switch plus one flag per new engine stage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    engine_enabled: bool = False
    document_intelligence_enabled: bool = False
    query_understanding_enabled: bool = False
    concept_expansion_enabled: bool = False
    decomposition_enabled: bool = False
    entity_resolution_enabled: bool = False
    relations_enabled: bool = False
    conflict_classifier_enabled: bool = False
    per_statement_confidence_enabled: bool = False
    verification_enabled: bool = False

    def stage(self, name: str) -> bool:
        """True when the master switch and the named stage are both enabled."""

        if not self.engine_enabled:
            return False
        value = getattr(self, name)
        return bool(value)


DEFAULT_FLAGS = EngineFlags()


def engine_flags(settings: Settings) -> EngineFlags:
    """Read the engine flags from application settings."""

    return EngineFlags(
        engine_enabled=settings.gi_engine_enabled,
        document_intelligence_enabled=settings.gi_document_intelligence_enabled,
        query_understanding_enabled=settings.gi_query_understanding_enabled,
        concept_expansion_enabled=settings.gi_concept_expansion_enabled,
        decomposition_enabled=settings.gi_decomposition_enabled,
        entity_resolution_enabled=settings.gi_entity_resolution_enabled,
        relations_enabled=settings.gi_relations_enabled,
        conflict_classifier_enabled=settings.gi_conflict_classifier_enabled,
        per_statement_confidence_enabled=settings.gi_per_statement_confidence_enabled,
        verification_enabled=settings.gi_verification_enabled,
    )
