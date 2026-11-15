"""Pydantic contracts for the Global Intelligence Engine (promt.md 5).

``QueryPlan`` is the validated output of the understanding + planning stages. It mirrors the
sketch in the task brief and stays additive: nothing here changes ``internal_evidence``
models, which remain the contracts the shipped service already consumes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Same strictness contract the shipped internal-evidence models use."""

    model_config = ConfigDict(extra="forbid")


class SourceType(StrEnum):
    """Source groups the planner can select (kept in step with the harness vocabulary)."""

    POSTGRES = "postgres"
    DOCUMENT = "document"
    IMAGE = "image"
    GRAPH = "graph"
    MEMORY = "memory"
    WEB = "web"


def _empty_source_types() -> list[SourceType]:
    return []


class SubQuestion(StrictModel):
    """One independent part of the user's message, with the sources it needs."""

    text: str = Field(min_length=1, max_length=500)
    required_sources: list[SourceType] = Field(default_factory=_empty_source_types)
    intent: str = Field(default="", max_length=100)


class RetrievalBudget(StrictModel):
    """Hard bounds for the adaptive retrieval loop (promt.md 5.1)."""

    max_iterations: int = Field(default=3, ge=1, le=10)
    max_tokens: int = Field(default=8000, ge=256, le=200_000)
    max_seconds: float = Field(default=20.0, gt=0.0, le=120.0)
    max_cost: float = Field(default=0.0, ge=0.0)


class EntityCandidate(StrictModel):
    """A candidate entity mentioned by the query. Never a resolved identity on its own."""

    surface: str = Field(min_length=1, max_length=200)
    identifier: str | None = Field(default=None, max_length=100)
    canonical_concept: str = Field(default="", max_length=100)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


def _empty_entities() -> list[EntityCandidate]:
    return []


def _empty_sub_questions() -> list[SubQuestion]:
    return []


class QueryPlan(StrictModel):
    """The validated plan the engine executes."""

    original_query: str
    normalized_query: str
    language: str = Field(max_length=10)
    intent: str = Field(max_length=100)
    entities: list[EntityCandidate] = Field(default_factory=_empty_entities)
    sub_questions: list[SubQuestion] = Field(default_factory=_empty_sub_questions)
    required_sources: list[SourceType] = Field(default_factory=_empty_source_types)
    requires_image_analysis: bool = False
    requires_structured_data: bool = False
    requires_cross_document_reasoning: bool = False
    requires_external_search: bool = False
    clarification_required: bool = False
    clarification_options: list[str] = Field(default_factory=list)
    budget: RetrievalBudget = Field(default_factory=lambda: RetrievalBudget())


@dataclass(frozen=True, slots=True)
class QueryUnderstanding:
    """Deterministic understanding of one message, before any retrieval."""

    original_query: str
    normalized_query: str
    language: str
    tokens: tuple[str, ...]
    identifiers: tuple[str, ...]
    concepts: tuple[str, ...]
    devices: tuple[str, ...]
    accessories: tuple[str, ...]
    #: (original token, corrected token) pairs actually applied.
    corrections: tuple[tuple[str, str], ...]
    #: Tokens with several equally plausible corrections; the engine asks instead of picking.
    ambiguous: tuple[str, ...]
    #: Every alternative for those tokens, shown to the user when clarification is required.
    clarification_options: tuple[str, ...]
    #: (device, connector family) hypotheses to verify against evidence. Never facts.
    connector_hypotheses: tuple[tuple[str, str], ...]
    #: True when an ambiguous correction could change the answer.
    clarification_required: bool
