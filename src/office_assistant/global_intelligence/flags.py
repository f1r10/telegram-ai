"""Feature flags for every new engine path. All default ``False`` = today's shipped behaviour.

A phase turns its own flag on only after the harness shows the scenario moved from fail to
pass and the numbers are recorded in ``docs/evaluation/BENCHMARK_RESULTS.md`` (architecture
§5). Flags are read from :class:`Settings` through the same env-var mechanism as every other
option, so no new configuration concept is introduced.
"""

from __future__ import annotations

from pydantic import BaseModel

from office_assistant.core.settings import Settings


class EngineFlags(BaseModel):
    engine_enabled: bool = False
    query_understanding_enabled: bool = False
    concept_expansion_enabled: bool = False
    decomposition_enabled: bool = False
    entity_resolution_enabled: bool = False
    relations_enabled: bool = False
    conflict_classifier_enabled: bool = False
    per_statement_confidence_enabled: bool = False
    verification_enabled: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> EngineFlags:
        return cls(
            engine_enabled=settings.gi_engine_enabled,
            query_understanding_enabled=settings.gi_query_understanding_enabled,
            concept_expansion_enabled=settings.gi_concept_expansion_enabled,
            decomposition_enabled=settings.gi_decomposition_enabled,
            entity_resolution_enabled=settings.gi_entity_resolution_enabled,
            relations_enabled=settings.gi_relations_enabled,
            conflict_classifier_enabled=settings.gi_conflict_classifier_enabled,
            per_statement_confidence_enabled=settings.gi_per_statement_confidence_enabled,
            verification_enabled=settings.gi_verification_enabled,
        )
