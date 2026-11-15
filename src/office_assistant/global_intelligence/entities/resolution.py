"""Deterministic entity resolution (Phase 3, audit defect F7).

One rule outranks every other here: **similarity is evidence, never an identity**. A mention
resolves only on an exact business identifier, an exact canonical name or an exact accepted alias.
Anything weaker becomes a scored candidate for the existing human review path, so a high score can
never silently merge two records (scenario I: no wrong merges).

The module is pure: it takes the records an authorised query already loaded and returns a decision.
It writes nothing, calls nothing and knows no database, which is what makes the strength order
testable without PostgreSQL.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from office_assistant.business.normalization import (
    normalize_customer_name,
    normalize_product_alias,
    normalize_product_code,
)
from office_assistant.infrastructure.database.models import EntityType


#: How a mention matched. The order below is the strength order: a weaker match never outranks a
#: stronger one, and a tie at the strongest level is ambiguity rather than a pick.
class MatchStrength(StrEnum):
    IDENTIFIER = "identifier"
    CANONICAL_NAME = "canonical_name"
    ALIAS = "alias"


class ResolutionStatus(StrEnum):
    """What the caller may do with the result.

    ``DISABLED`` means the feature is off, which is not the same answer as "no idea".
    """

    DISABLED = "disabled"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class CandidateReason(StrEnum):
    """Why a candidate was proposed. Both reasons route to a human, never to a merge."""

    AMBIGUOUS_EXACT = "ambiguous_exact"
    NAME_SIMILARITY = "name_similarity"


#: Similarity a near miss needs before it is worth a reviewer's attention. This bound decides only
#: which rows are *shown*; no score ever produces a resolution. ASSUMPTION
#: (docs/OPEN-QUESTIONS.md Q66).
CANDIDATE_SIMILARITY = 0.80
#: How many candidates one unresolved mention may propose, so a large registry cannot flood review.
MAX_CANDIDATES = 5


@dataclass(frozen=True, slots=True)
class EntityRecord:
    """One identity the caller is allowed to see, with every form that names it.

    ``aliases`` and ``identifiers`` are already normalised by the query layer, so this record can be
    compared without knowing which table a form came from.
    """

    entity_id: str
    entity_type: EntityType
    canonical_name: str
    normalized_name: str
    aliases: tuple[str, ...] = ()
    identifiers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Candidate:
    """A near match offered to a reviewer, never a resolution."""

    entity_id: str
    entity_type: EntityType
    canonical_name: str
    score: float
    reason: CandidateReason


@dataclass(frozen=True, slots=True)
class Resolution:
    """The outcome of one mention: at most one identity, or the candidates that need a person."""

    status: ResolutionStatus
    mention: str
    #: The mention normalised the way each entity type normalises its own names. A product code and
    #: a customer name are not normalised the same way, so both are kept instead of guessing one.
    normalized_product: str
    normalized_customer: str
    matched_by: MatchStrength | None = None
    record: EntityRecord | None = None
    candidates: tuple[Candidate, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.status is ResolutionStatus.RESOLVED

    def normalized_for(self, entity_type: EntityType) -> str:
        """The mention as that entity type would normalise it (used to key a candidate row)."""

        return (
            self.normalized_product
            if entity_type is EntityType.PRODUCT
            else self.normalized_customer
        )


def normalize_mention(mention: str) -> tuple[str, str]:
    """The mention normalised as a product form and as a customer name.

    Both normalisers are the existing business ones: a second normaliser would let the same string
    match in one code path and not the other. A form that normalises to nothing (say ``"---"``) is
    reported as empty rather than raised, because a bad mention is not an error.
    """

    try:
        product = normalize_product_alias(mention)
    except ValueError:
        product = ""
    try:
        customer = normalize_customer_name(mention)
    except ValueError:
        customer = ""
    return product, customer


def normalize_identifier(identifier: str | None) -> str | None:
    """Normalise an optional business identifier; an unusable one is simply absent."""

    if identifier is None:
        return None
    try:
        return normalize_product_code(identifier)
    except ValueError:
        return None


def disabled(mention: str) -> Resolution:
    """The exact answer when the milestone is off: nothing was read and nothing was decided."""

    product, customer = normalize_mention(mention)
    return Resolution(
        status=ResolutionStatus.DISABLED,
        mention=mention,
        normalized_product=product,
        normalized_customer=customer,
    )


def resolve(
    mention: str,
    *,
    identifier: str | None = None,
    records: Sequence[EntityRecord],
    candidate_similarity: float = CANDIDATE_SIMILARITY,
    max_candidates: int = MAX_CANDIDATES,
) -> Resolution:
    """Resolve one mention against the records the caller is allowed to see.

    The steps are ordered and each one either decides or falls through:

    1. an exact identifier match (a product code) is the strongest evidence there is;
    2. else an exact canonical name;
    3. else an exact accepted alias;
    4. else similarity, which can only ever produce candidates.

    A tie at the deciding step is ``AMBIGUOUS`` with every tied record as a candidate — picking one
    would be an invented identity, and the caller has a review flow for exactly this case.
    """

    product_norm, customer_norm = normalize_mention(mention)
    normalized = {
        EntityType.PRODUCT: product_norm or normalize_identifier(mention) or "",
        EntityType.CUSTOMER: customer_norm,
    }
    wanted_identifier = normalize_identifier(identifier)

    def outcome(
        status: ResolutionStatus,
        *,
        matched_by: MatchStrength | None = None,
        record: EntityRecord | None = None,
        candidates: tuple[Candidate, ...] = (),
    ) -> Resolution:
        return Resolution(
            status=status,
            mention=mention,
            normalized_product=normalized[EntityType.PRODUCT],
            normalized_customer=normalized[EntityType.CUSTOMER],
            matched_by=matched_by,
            record=record,
            candidates=candidates,
        )

    if not normalized[EntityType.PRODUCT] and not normalized[EntityType.CUSTOMER]:
        return outcome(ResolutionStatus.UNRESOLVED)

    matched: dict[MatchStrength, list[EntityRecord]] = {}
    for record in records:
        form = normalized.get(record.entity_type, "")
        if wanted_identifier is not None and wanted_identifier in record.identifiers:
            strength = MatchStrength.IDENTIFIER
        elif form and form == record.normalized_name:
            strength = MatchStrength.CANONICAL_NAME
        elif form and form in record.aliases:
            strength = MatchStrength.ALIAS
        else:
            continue
        matched.setdefault(strength, []).append(record)

    for strength in (MatchStrength.IDENTIFIER, MatchStrength.CANONICAL_NAME, MatchStrength.ALIAS):
        hits = matched.get(strength)
        if not hits:
            continue
        if len(hits) == 1:
            return outcome(ResolutionStatus.RESOLVED, matched_by=strength, record=hits[0])
        ordered = sorted(hits, key=lambda item: (item.canonical_name, item.entity_id))
        return outcome(
            ResolutionStatus.AMBIGUOUS,
            matched_by=strength,
            candidates=tuple(
                Candidate(
                    entity_id=item.entity_id,
                    entity_type=item.entity_type,
                    canonical_name=item.canonical_name,
                    score=1.0,
                    reason=CandidateReason.AMBIGUOUS_EXACT,
                )
                for item in ordered[:max_candidates]
            ),
        )

    scored: list[tuple[float, EntityRecord]] = []
    for record in records:
        form = normalized.get(record.entity_type, "")
        if not form:
            continue
        score = _similarity(form, record.normalized_name)
        if score >= candidate_similarity:
            scored.append((score, record))
    scored.sort(key=lambda item: (-item[0], item[1].canonical_name, item[1].entity_id))
    candidates = tuple(
        Candidate(
            entity_id=record.entity_id,
            entity_type=record.entity_type,
            canonical_name=record.canonical_name,
            score=round(score, 4),
            reason=CandidateReason.NAME_SIMILARITY,
        )
        for score, record in scored[:max_candidates]
    )
    return outcome(
        ResolutionStatus.AMBIGUOUS if candidates else ResolutionStatus.UNRESOLVED,
        candidates=candidates,
    )


def _similarity(left: str, right: str) -> float:
    """Character-level similarity of two already-normalised forms, in ``0.0 .. 1.0``."""

    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return difflib.SequenceMatcher(None, left, right).ratio()
