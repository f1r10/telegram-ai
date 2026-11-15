"""Orchestration entry point for the Global Intelligence Engine (promt.md 4, 5).

The engine composes the three Phase-1 layers into one object a caller can hold: the
deterministic understanding layer, the deterministic-first planner, and the evidence
aggregator. Nothing here performs I/O on import, opens a session, or contacts a provider
unless a model was explicitly supplied, and every optional stage is gated by
:class:`~.flags.EngineFlags`.

Phase 1 ships that contract and the deterministic path. Adaptive retrieval, entity
resolution, the conflict classifier and verification belong to later phases (promt.md 18);
until they land the engine returns the validated plan and never claims a capability it does
not have, so a caller can enable ``gi_engine_enabled`` without getting placeholder answers.
"""

from __future__ import annotations

from collections.abc import Sequence

from office_assistant.ai.provider import LanguageModel
from office_assistant.core.settings import Settings
from office_assistant.internal_evidence.models import EvidenceBundle

from .evidence import AggregatedEvidence, aggregate
from .flags import DEFAULT_FLAGS, EngineFlags, engine_flags
from .models import QueryUnderstanding
from .planning import PlanOutcome, QueryPlanner
from .understanding import understand


class GlobalIntelligenceEngine:
    """Deterministic-first orchestration over the existing evidence service."""

    def __init__(
        self,
        *,
        model: LanguageModel | None = None,
        flags: EngineFlags = DEFAULT_FLAGS,
    ) -> None:
        self.flags = flags
        self._planner = QueryPlanner(model=model, flags=flags)

    @classmethod
    def from_settings(
        cls, settings: Settings, *, model: LanguageModel | None = None
    ) -> GlobalIntelligenceEngine:
        """Build an engine whose stages are set by the ``gi_*`` environment flags."""

        return cls(model=model, flags=engine_flags(settings))

    @property
    def enabled(self) -> bool:
        """True when the master switch is on, i.e. a caller may route turns through here."""

        return self.flags.engine_enabled

    def understand(self, question: str) -> QueryUnderstanding:
        """Deterministic understanding of one message; never touches a provider."""

        return understand(question)

    async def plan(
        self,
        user_id: int,
        question: str,
        *,
        understanding: QueryUnderstanding | None = None,
    ) -> PlanOutcome:
        """Route the message, deterministically first, LLM second, never losing the turn."""

        return await self._planner.plan(user_id, question, understanding=understanding)

    def aggregate(
        self, bundle: EvidenceBundle, *, hypotheses: Sequence[str] = ()
    ) -> AggregatedEvidence:
        """Label the bundle's citations and record the open hypotheses as probable."""

        return aggregate(bundle, hypotheses=hypotheses)
