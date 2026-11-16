"""Scenario B/I evidence for Phase 3: what entity resolution decides, measured offline.

The corpus is generated here, so nothing private enters the repository and every number is
reproducible. Each case is one mention with the identity it must reach, or ``None`` when the right
answer is "no identity". Two questions are measured, matching Phase 3's exit criteria:

* **B (cross-document)** — the same product named by a code, a canonical name, an alias and a
  differently-cased form across documents must reach one identity.
* **I (no wrong merges)** — a near miss (``Plinter M404``, ``M404X``) must stay unresolved with
  candidates at most, and a different model (``M405``) must never reach ``M404``.

Precision here is "of the identities the resolver claimed, how many were right": a wrong claim
is the failure mode that matters, because it silently answers about somebody else's record.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from office_assistant.business.normalization import (
    normalize_customer_name,
    normalize_product_alias,
    normalize_product_code,
)
from office_assistant.global_intelligence.entities import (
    EntityRecord,
    MatchStrength,
    ResolutionStatus,
    resolve,
)
from office_assistant.infrastructure.database.models import EntityType


def _product(
    entity_id: str, name: str, *, code: str | None = None, aliases: Sequence[str] = ()
) -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        entity_type=EntityType.PRODUCT,
        canonical_name=name,
        normalized_name=normalize_product_alias(name),
        aliases=tuple(normalize_product_alias(alias) for alias in aliases),
        identifiers=(normalize_product_code(code),) if code else (),
    )


def _customer(entity_id: str, name: str, *, aliases: Sequence[str] = ()) -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        entity_type=EntityType.CUSTOMER,
        canonical_name=name,
        normalized_name=normalize_customer_name(name),
        aliases=tuple(normalize_customer_name(alias) for alias in aliases),
    )


#: The registry the cases resolve against. Small on purpose: every record is one a reviewer can
#: check by eye, and the pairs that must stay distinct are named.
RESOLUTION_REGISTRY: tuple[EntityRecord, ...] = (
    _product(
        "product-m404",
        "HP LaserJet Pro M404dn",
        code="M404",
        aliases=("HP M404", "M404dn", "Printer HP"),
    ),
    _product("product-m405", "HP LaserJet Pro M405dn", code="M405", aliases=("HP M405",)),
    _product("product-usb-b", "USB-B 3m printer cable", code="CBL-USB-B-3"),
    _customer("customer-delta", "Delta MMC", aliases=("Delta",)),
    _customer("customer-epsilon", "Epsilon Group"),
    # Two identities that share a canonical name: the resolver must report ambiguity, not pick one.
    _customer("customer-atlas-a", "Atlas"),
    _customer("customer-atlas-b", "Atlas"),
)


@dataclass(frozen=True, slots=True)
class ResolutionCase:
    """One labelled mention: the identity it must reach, or ``None`` when it reaches none."""

    case_id: str
    mention: str
    expected_entity_id: str | None
    identifier: str | None = None
    #: The scenario this case is evidence for, so a reader can see B and I separately.
    scenario: str = "B"


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """What the resolver actually did for one case."""

    case_id: str
    scenario: str
    expected_entity_id: str | None
    status: ResolutionStatus
    entity_id: str | None
    matched_by: MatchStrength | None
    candidate_ids: tuple[str, ...]

    @property
    def correct_claim(self) -> bool:
        """A resolved row that names the expected identity is right; nothing else is a claim."""

        return (
            self.status is ResolutionStatus.RESOLVED and self.entity_id == self.expected_entity_id
        )

    @property
    def false_merge(self) -> bool:
        """A resolution to the wrong identity — the failure that must never happen."""

        return (
            self.status is ResolutionStatus.RESOLVED and self.entity_id != self.expected_entity_id
        )

    @property
    def correctly_withheld(self) -> bool:
        """A case with no expected identity that was not resolved to anything."""

        return self.expected_entity_id is None and self.status is not ResolutionStatus.RESOLVED


CASES: tuple[ResolutionCase, ...] = (
    # --- B: one identity, several surface forms seen in different documents -------------------
    ResolutionCase("b-code", "M404", "product-m404", identifier="M404"),
    ResolutionCase("b-canonical", "HP LaserJet Pro M404dn", "product-m404"),
    ResolutionCase("b-alias", "M404dn", "product-m404"),
    ResolutionCase("b-alias-spaced", "HP M404", "product-m404"),
    ResolutionCase("b-case-form", "hp  m404", "product-m404"),
    ResolutionCase("b-customer-canonical", "Delta MMC", "customer-delta"),
    ResolutionCase("b-customer-alias", "delta", "customer-delta"),
    # An identifier outranks an alias that points elsewhere: the code is the strongest evidence.
    ResolutionCase("b-identifier-outranks-alias", "HP M404", "product-m405", identifier="M405"),
    # --- I: near misses and neighbours stay distinct ------------------------------------------
    ResolutionCase("i-typo", "Plinter M404", None, scenario="I"),
    ResolutionCase("i-model-neighbour", "M405", "product-m405", identifier="M405", scenario="I"),
    ResolutionCase("i-unseen-suffix", "M404X", None, scenario="I"),
    ResolutionCase("i-shared-name", "Atlas", None, scenario="I"),
    ResolutionCase("i-unrelated", "Kamera", None, scenario="I"),
)


def run_cases(cases: Sequence[ResolutionCase] = CASES) -> tuple[CaseOutcome, ...]:
    """Resolve every case against :data:`RESOLUTION_REGISTRY` and keep what happened."""

    outcomes: list[CaseOutcome] = []
    for case in cases:
        resolution = resolve(case.mention, identifier=case.identifier, records=RESOLUTION_REGISTRY)
        outcomes.append(
            CaseOutcome(
                case_id=case.case_id,
                scenario=case.scenario,
                expected_entity_id=case.expected_entity_id,
                status=resolution.status,
                entity_id=resolution.record.entity_id if resolution.record else None,
                matched_by=resolution.matched_by,
                candidate_ids=tuple(candidate.entity_id for candidate in resolution.candidates),
            )
        )
    return tuple(outcomes)


def _ratio(part: int, total: int) -> float | None:
    """Rounded ratio, or ``None`` when there is nothing to divide by (Phase-2 convention)."""

    return round(part / total, 4) if total else None


def summarise(outcomes: Sequence[CaseOutcome]) -> dict[str, int | float | None]:
    """The numbers Phase 3 reports: precision, recall, and the false merges that must be zero."""

    resolved = [outcome for outcome in outcomes if outcome.status is ResolutionStatus.RESOLVED]
    resolvable = [outcome for outcome in outcomes if outcome.expected_entity_id is not None]
    correct = [outcome for outcome in outcomes if outcome.correct_claim]
    return {
        "cases": len(outcomes),
        "resolvable_cases": len(resolvable),
        "resolved": len(resolved),
        "correct": len(correct),
        "wrong_claims": len([outcome for outcome in outcomes if outcome.false_merge]),
        "withheld": len([outcome for outcome in outcomes if outcome.correctly_withheld]),
        "ambiguous": len(
            [outcome for outcome in outcomes if outcome.status is ResolutionStatus.AMBIGUOUS]
        ),
        "resolution_precision": _ratio(len(correct), len(resolved)),
        "resolution_recall": _ratio(len(correct), len(resolvable)),
        "false_merge_rate": _ratio(
            len([outcome for outcome in outcomes if outcome.false_merge]), len(resolved)
        ),
    }


def report() -> dict[str, object]:
    """The whole measured picture, in the shape ``BENCHMARK_RESULTS.md`` records."""

    outcomes = run_cases()
    return {
        "corpus": "generated (tests/evaluation/entity_resolution.py)",
        "registry_records": len(RESOLUTION_REGISTRY),
        **summarise(outcomes),
        "cases_detail": [
            {
                "case": outcome.case_id,
                "scenario": outcome.scenario,
                "status": outcome.status.value,
                "expected": outcome.expected_entity_id,
                "entity": outcome.entity_id,
                "matched_by": outcome.matched_by.value if outcome.matched_by else None,
                "candidates": list(outcome.candidate_ids),
            }
            for outcome in outcomes
        ],
    }


__all__ = [
    "CASES",
    "RESOLUTION_REGISTRY",
    "CaseOutcome",
    "ResolutionCase",
    "report",
    "run_cases",
    "summarise",
]
