"""Engine orchestrator skeleton (architecture §2/§3).

Phase 1 delivers the deterministic understanding and planning stages and measures them offline
through the evaluation harness. Wiring this engine into the live Telegram handler is deferred and
gated by ``gi_engine_enabled`` + ``gi_query_understanding_enabled`` (both default ``False``), so the
shipped ``internal_evidence`` path stays byte-identical until a later phase flips a flag with a
recorded fail->pass benchmark delta. The stages here are pure and provider-free; retrieval,
reasoning and verification are added by their own phases.
"""

from __future__ import annotations

from office_assistant.global_intelligence.flags import EngineFlags
from office_assistant.global_intelligence.models import QueryPlan, QueryUnderstanding
from office_assistant.global_intelligence.planning.planner import plan_query
from office_assistant.global_intelligence.understanding import understand_query


class GlobalIntelligenceEngine:
    """Compose understanding -> planning. No side effects, no provider calls in Phase 1."""

    def __init__(self, flags: EngineFlags | None = None) -> None:
        self.flags = flags if flags is not None else EngineFlags()

    def understand(self, question: str) -> QueryUnderstanding:
        return understand_query(question)

    def plan(self, question: str) -> QueryPlan:
        return plan_query(question, self.understand(question))


__all__ = ["GlobalIntelligenceEngine"]
