"""Concept recognition and hypothesis expansion (promt.md 5, 9).

Recognition is a prefix-stem match over folded tokens, so inflected Azerbaijani forms
("kabellər", "printerinə", "anbarda") are recognized without a stemmer. A token that
matches no stem is offered to the bounded typo corrector, and the corrected form is
matched again - that is how "plinter" becomes "printer" (scenario A).

Connector hypotheses are deliberately *hypotheses*: "camera" expands to the connector
families a camera could need, and every hypothesis is labelled probable until Phase 5
verifies it against typed connector attributes (promt.md 9).
"""

from __future__ import annotations

from dataclasses import dataclass

from .lexicon import ACCESSORY, CONCEPTS, DEVICE, DEVICE_CONNECTOR_HYPOTHESES
from .normalizer import NormalizedQuery, normalize
from .typos import TokenAmbiguity, correct_tokens


@dataclass(frozen=True, slots=True)
class ConceptMatch:
    """One canonical concept recognized in the query."""

    canonical: str
    kind: str
    token: str
    #: The folded token that produced the match, when a typo correction was involved.
    corrected_from: str | None = None


@dataclass(frozen=True, slots=True)
class ConceptAnalysis:
    """Everything the concept layer concluded about one query."""

    normalized: NormalizedQuery
    matches: tuple[ConceptMatch, ...]
    corrections: tuple[tuple[str, str], ...]
    ambiguous: tuple[TokenAmbiguity, ...]

    @property
    def canonicals(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.canonical for item in self.matches))

    @property
    def devices(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.canonical for item in self.matches if item.kind == DEVICE))

    @property
    def accessories(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(item.canonical for item in self.matches if item.kind == ACCESSORY)
        )

    def has(self, canonical: str) -> bool:
        return any(item.canonical == canonical for item in self.matches)

    @property
    def ambiguous_tokens(self) -> tuple[str, ...]:
        """The tokens the corrector refused to guess."""
        return tuple(item.token for item in self.ambiguous)

    @property
    def clarification_options(self) -> tuple[str, ...]:
        """Every alternative the user may pick from, de-duplicated and stable."""
        return tuple(
            dict.fromkeys(
                candidate for ambiguity in self.ambiguous for candidate in ambiguity.candidates
            )
        )


@dataclass(frozen=True, slots=True)
class CompatibilityHypothesis:
    """A device and a connector family that *might* fit it. Never a fact."""

    device: str
    connector_family: str


def _match_token(token: str, *, corrected_from: str | None = None) -> list[ConceptMatch]:
    return [
        ConceptMatch(
            canonical=concept.canonical,
            kind=concept.kind,
            token=token,
            corrected_from=corrected_from,
        )
        for concept in CONCEPTS
        if any(token.startswith(stem) for stem in concept.stems)
    ]


def analyze_concepts(question: str) -> ConceptAnalysis:
    normalized = normalize(question)
    typo_result = correct_tokens(normalized.tokens, protected=normalized.protected_tokens)
    matches: list[ConceptMatch] = []
    seen: set[tuple[str, str]] = set()
    for original, corrected in zip(normalized.tokens, typo_result.corrected_tokens, strict=True):
        corrected_from = original if original != corrected else None
        for match in _match_token(corrected, corrected_from=corrected_from):
            key = (match.canonical, match.token)
            if key not in seen:
                seen.add(key)
                matches.append(match)
    return ConceptAnalysis(
        normalized=normalized,
        matches=tuple(matches),
        corrections=tuple((item.original, item.corrected) for item in typo_result.corrections),
        ambiguous=typo_result.ambiguous,
    )


def connector_hypotheses(devices: tuple[str, ...]) -> tuple[CompatibilityHypothesis, ...]:
    """Expand devices into the connector families they could need, without duplicates."""

    hypotheses: list[CompatibilityHypothesis] = []
    seen: set[tuple[str, str]] = set()
    for device in devices:
        for family in DEVICE_CONNECTOR_HYPOTHESES.get(device, ()):
            key = (device, family)
            if key not in seen:
                seen.add(key)
                hypotheses.append(CompatibilityHypothesis(device=device, connector_family=family))
    return tuple(hypotheses)


def device_and_accessory(question: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Cheap accessor used by the deterministic router's concept fast path."""

    analysis = analyze_concepts(question)
    return analysis.devices, analysis.accessories
