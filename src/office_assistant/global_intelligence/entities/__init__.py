"""Phase-3 entity registry, aliases and deterministic resolution.

``resolution.resolve`` decides; ``access`` says what the actor may see; ``store`` reads and writes
the rows; ``service`` ties them together behind ``gi_entity_resolution_enabled``.
"""

from office_assistant.global_intelligence.entities.access import (
    ENTITY_FEATURES,
    EntityAccess,
    allowed_entity_types,
    entity_access,
    entity_scope,
)
from office_assistant.global_intelligence.entities.resolution import (
    CANDIDATE_SIMILARITY,
    MAX_CANDIDATES,
    Candidate,
    CandidateReason,
    EntityRecord,
    MatchStrength,
    Resolution,
    ResolutionStatus,
    disabled,
    normalize_identifier,
    normalize_mention,
    resolve,
)
from office_assistant.global_intelligence.entities.service import (
    EntityResolutionService,
    entity_resolution_service,
)
from office_assistant.global_intelligence.entities.store import MAX_ENTITY_RECORDS, EntityStore

__all__ = [
    "CANDIDATE_SIMILARITY",
    "ENTITY_FEATURES",
    "MAX_CANDIDATES",
    "MAX_ENTITY_RECORDS",
    "Candidate",
    "CandidateReason",
    "EntityAccess",
    "EntityRecord",
    "EntityResolutionService",
    "EntityStore",
    "MatchStrength",
    "Resolution",
    "ResolutionStatus",
    "allowed_entity_types",
    "disabled",
    "entity_access",
    "entity_resolution_service",
    "entity_scope",
    "normalize_identifier",
    "normalize_mention",
    "resolve",
]
