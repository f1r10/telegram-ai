"""Typed stage contracts for the engine (architecture §3.1).

``QueryUnderstanding`` is the output of stage 1; ``QueryPlan`` the output of stage 2. Both are
Pydantic models with ``extra='forbid'`` so a planner cannot smuggle undeclared fields. The plan
always converts to a deterministic ``EvidencePlan`` so the engine can degrade to the shipped
behaviour when a provider fails.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from office_assistant.global_intelligence.evidence.confidence import StatementStatus
from office_assistant.internal_evidence.models import (
    EvidencePlan,
    PlannedToolCall,
    RoutingCategory,
    StrictModel,
)


def _empty_sources() -> list[SourceType]:
    return []


def _empty_hypotheses() -> list[ConceptHypothesis]:
    return []


def _empty_corrections() -> list[TypoCorrection]:
    return []


def _empty_sub_questions() -> list[SubQuestion]:
    return []


def _empty_tool_calls() -> list[PlannedToolCall]:
    return []


class SourceType(StrEnum):
    POSTGRES = "postgres"
    DOCUMENT = "document"
    IMAGE = "image"
    GRAPH = "graph"
    MEMORY = "memory"
    WEB = "web"


class SubQuestion(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    concepts: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    sources: list[SourceType] = Field(default_factory=_empty_sources)


class RetrievalBudget(StrictModel):
    max_tool_calls: int = Field(default=4, ge=0, le=4)
    max_documents: int = Field(default=5, ge=0, le=20)
    max_sub_questions: int = Field(default=4, ge=1, le=8)


class ConceptHypothesis(StrictModel):
    concept: str
    related: list[str] = Field(default_factory=list)
    basis: str = Field(default="", max_length=500)
    status: StatementStatus = StatementStatus.PROBABLE


class TypoCorrection(StrictModel):
    original: str
    corrected: str
    distance: int = Field(ge=1, le=2)


class QueryUnderstanding(StrictModel):
    original_query: str
    normalized_query: str
    corrected_query: str
    language: str = "az"
    identifiers: tuple[str, ...] = ()
    concepts: tuple[str, ...] = ()
    hypotheses: list[ConceptHypothesis] = Field(default_factory=_empty_hypotheses)
    corrections: list[TypoCorrection] = Field(default_factory=_empty_corrections)
    ambiguous_tokens: list[str] = Field(default_factory=list)
    sub_questions: list[SubQuestion] = Field(default_factory=_empty_sub_questions)
    requires_clarification: bool = False


class QueryPlan(StrictModel):
    category: RoutingCategory
    intent: str = Field(max_length=100)
    sub_questions: list[SubQuestion] = Field(default_factory=_empty_sub_questions)
    sources: list[SourceType] = Field(default_factory=_empty_sources)
    use_structured_tools: bool = False
    use_document_search: bool = False
    allow_general_knowledge: bool = False
    business_tool_calls: list[PlannedToolCall] = Field(
        default_factory=_empty_tool_calls, max_length=4
    )
    requires_clarification: bool = False
    clarification_options: list[str] = Field(default_factory=list)
    reason_code: str = Field(max_length=100)
    retrieval_budget: RetrievalBudget = Field(default_factory=RetrievalBudget)
    deterministic: bool = True

    def to_evidence_plan(self) -> EvidencePlan:
        """The shipped, deterministic plan this QueryPlan degrades to."""

        return EvidencePlan(
            category=self.category,
            intent=self.intent,
            use_structured_tools=self.use_structured_tools,
            use_document_search=self.use_document_search,
            allow_general_knowledge=self.allow_general_knowledge,
            business_tool_calls=self.business_tool_calls,
            requires_clarification=self.requires_clarification,
            reason_code=self.reason_code,
        )
