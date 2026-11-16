"""The relation vocabulary Phase 3 introduces, in one place.

``entity_relations`` stores that two registry identities are connected, so two questions have to be
answered once, in code: which relation kinds exist and between which entity types, and what a person
has decided about one relation. Keeping both enums here means the store, the migration check
constraints and the tests read the same vocabulary instead of three hand-kept lists.

Every relation type is mapped to a fixed pair of entity types, so a caller cannot invent a relation
kind by passing a made-up string and the database constraint, the store and the tests agree.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from office_assistant.infrastructure.database.models import EntityType


class RelationType(StrEnum):
    """The relation kinds this increment supports.

    Deliberately short: the first relation is the one the business data already supports (a product
    the company supplies to a customer). A new kind is added here with its endpoints and a migration
    constraint, never by a free-form string at a call site.
    """

    SUPPLIED_TO = "supplied_to"


class VerificationStatus(StrEnum):
    """What has been decided about one relation.

    ``INFERRED`` is the honest default: a row that was read from a source but not yet confirmed by a
    person. ``CONFIRMED`` is the only status the engine may report as a fact; ``REJECTED`` keeps the
    row and its evidence for the audit trail instead of deleting what a reviewer saw.
    """

    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    REJECTED = "rejected"


#: The (subject, object) entity types each relation kind connects. A tuple, not a second table: the
#: direction is part of the relation's meaning and must not be guessed at a call site.
RELATION_ENDPOINTS: Mapping[RelationType, tuple[EntityType, EntityType]] = MappingProxyType(
    {
        RelationType.SUPPLIED_TO: (EntityType.PRODUCT, EntityType.CUSTOMER),
    }
)


def relation_endpoints(relation_type: RelationType) -> tuple[EntityType, EntityType]:
    """The subject and object entity types of one relation kind.

    Raises :class:`KeyError` for a relation kind with no declared endpoints, which is what keeps the
    two vocabularies from drifting apart.
    """

    return RELATION_ENDPOINTS[relation_type]
