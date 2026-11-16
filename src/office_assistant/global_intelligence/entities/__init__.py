"""Phase-3 entity registry, deterministic resolution and the sourced relation layer.

``resolution.resolve`` decides; ``access`` says what the actor may see; ``store`` reads and writes
the registry rows; ``service`` ties them together behind ``gi_entity_resolution_enabled``.
``relations`` holds the one-hop query over ``entity_relations`` and the store that cannot write a
relation without a source; ``types`` is the relation/verification vocabulary the migration and the
code share.
"""

from office_assistant.global_intelligence.entities.access import (
    ENTITY_FEATURES,
    EntityAccess,
    allowed_entity_types,
    entity_access,
    entity_scope,
    entity_type_scope,
)
from office_assistant.global_intelligence.entities.relations import (
    DEFAULT_MULTI_HOP_DEPTH,
    MAX_MULTI_HOP_DEPTH,
    MAX_RELATION_HOPS,
    MAX_RELATIONS_PER_ENTITY,
    RelationHop,
    RelationService,
    RelationStore,
    multi_hop_statement,
    one_hop_statement,
    relation_service,
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
from office_assistant.global_intelligence.entities.types import (
    RELATION_ENDPOINTS,
    RelationType,
    VerificationStatus,
    relation_endpoints,
)

__all__ = [
    "CANDIDATE_SIMILARITY",
    "DEFAULT_MULTI_HOP_DEPTH",
    "ENTITY_FEATURES",
    "MAX_CANDIDATES",
    "MAX_ENTITY_RECORDS",
    "MAX_MULTI_HOP_DEPTH",
    "MAX_RELATION_HOPS",
    "MAX_RELATIONS_PER_ENTITY",
    "RELATION_ENDPOINTS",
    "Candidate",
    "CandidateReason",
    "EntityAccess",
    "EntityRecord",
    "EntityResolutionService",
    "EntityStore",
    "MatchStrength",
    "RelationHop",
    "RelationService",
    "RelationStore",
    "RelationType",
    "Resolution",
    "ResolutionStatus",
    "VerificationStatus",
    "allowed_entity_types",
    "disabled",
    "entity_access",
    "entity_resolution_service",
    "entity_scope",
    "entity_type_scope",
    "normalize_identifier",
    "multi_hop_statement",
    "normalize_mention",
    "one_hop_statement",
    "relation_endpoints",
    "relation_service",
    "resolve",
]
