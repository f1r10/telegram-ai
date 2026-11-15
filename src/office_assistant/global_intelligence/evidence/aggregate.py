"""Evidence aggregation: bundle -> labelled statements (promt.md 7, 13.1).

The aggregator does not retrieve anything and does not call a model. It takes the bundle the
existing evidence service already builds and states, for every citation, what that citation can
actually support - using :func:`~.confidence.statement_status` rather than assuming that the
presence of a citation means confirmation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from office_assistant.internal_evidence.models import (
    EvidenceBundle,
    EvidenceConflict,
    UnifiedCitation,
)

from .confidence import StatementStatus, statement_status


@dataclass(frozen=True, slots=True)
class Statement:
    """One statement of an answer, with the evidence it claims and its epistemic state."""

    text: str
    status: StatementStatus
    evidence_id: str | None = None
    locator: Mapping[str, str] | None = None

    @property
    def is_well_supported_confirmed(self) -> bool:
        """True only for ``confirmed`` statements that carry a source *and* a locator."""

        return (
            self.status is StatementStatus.CONFIRMED
            and bool(self.evidence_id)
            and bool(self.locator)
        )


@dataclass(frozen=True, slots=True)
class AggregatedEvidence:
    """The statements an answer may make, with the sources it may cite."""

    statements: tuple[Statement, ...]
    citations: tuple[UnifiedCitation, ...]
    conflicts: tuple[EvidenceConflict, ...]
    warnings: tuple[str, ...]


def _locator(citation: UnifiedCitation) -> Mapping[str, str] | None:
    if not citation.locator:
        return None
    return {str(key): str(value) for key, value in citation.locator.items()}


def aggregate(bundle: EvidenceBundle, *, hypotheses: Sequence[str] = ()) -> AggregatedEvidence:
    """Label every citation in ``bundle`` and add the open hypotheses as probable statements.

    Hypotheses (e.g. "a camera probably needs an Ethernet or coax cable") are recorded as
    ``ehtimal`` with no evidence id, so a later phase can either corroborate them against typed
    attributes or drop them - they are never promoted to a confirmed company fact here.
    """

    statements: list[Statement] = []
    for citation in bundle.citations:
        locator = _locator(citation)
        statements.append(
            Statement(
                text=citation.label,
                status=statement_status(evidence_id=citation.evidence_id, locator=locator),
                evidence_id=citation.evidence_id,
                locator=locator,
            )
        )
    statements.extend(
        Statement(
            text=item,
            status=statement_status(evidence_id=None, locator=None, inferred=True),
        )
        for item in hypotheses
    )
    return AggregatedEvidence(
        statements=tuple(statements),
        citations=tuple(bundle.citations),
        conflicts=tuple(bundle.conflicts),
        warnings=tuple(bundle.warnings),
    )
