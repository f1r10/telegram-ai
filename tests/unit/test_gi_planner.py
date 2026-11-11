"""Deterministic-first query planning and the provider-failure fallback.

The planner asks the deterministic layer first and only consults a language model when that
layer cannot decide. A provider failure must degrade the plan, never lose the turn
(AGENTS.md invariant 8), so the failure path is asserted with a raising stub.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import TypeVar

from pydantic import BaseModel

from office_assistant.ai.provider import ModelResponse, TokenUsage
from office_assistant.global_intelligence.flags import EngineFlags
from office_assistant.global_intelligence.models import QueryPlan, SourceType
from office_assistant.global_intelligence.planning import (
    CONCEPT_CONFIDENCE,
    IDENTIFIER_CONFIDENCE,
    PLANNER_POLICY,
    QueryPlanner,
    build_query_plan,
)
from office_assistant.global_intelligence.understanding import understand
from office_assistant.internal_evidence.router import deterministic_plan

T = TypeVar("T", bound=BaseModel)

MULTI_TOPIC = "Bu printerə kabel varmı, son müqavilədə təchizatçı kimdir və anbarda neçədir?"
NEEDS_MODEL = "Sifarişlərimi göstər"


class _StubModel:
    """Records the call and returns a canned plan (or raises) - never touches a network."""

    def __init__(self, parsed: BaseModel | None = None, *, error: Exception | None = None) -> None:
        self._parsed = parsed
        self._error = error
        self.calls: list[tuple[str, str]] = []

    async def generate(
        self,
        *,
        instructions: str,
        input_messages: Sequence[dict[str, str]],
        output_schema: type[T],
        request_type: str,
        user_id: int,
    ) -> ModelResponse:
        self.calls.append((request_type, input_messages[0]["content"]))
        if self._error is not None:
            raise self._error
        if self._parsed is None:
            raise AssertionError("stub has no canned plan")
        return ModelResponse(
            parsed=self._parsed,
            model="stub",
            response_id="stub-1",
            usage=TokenUsage(),
            latency_ms=1,
        )


def _planned(question: str) -> QueryPlan:
    return build_query_plan(
        question, understand(question), deterministic=deterministic_plan(question)
    )


def _model_plan() -> QueryPlan:
    return QueryPlan(
        original_query=NEEDS_MODEL,
        normalized_query="sifarislərimi goster",
        language="az",
        intent="MODEL_ROUTE",
    )


def test_plan_carries_the_route_and_its_sources() -> None:
    plan = _planned("Kamera üçün hansı kabellər var?")
    assert plan.intent == "DEVICE_ACCESSORY_LOOKUP"
    assert plan.requires_structured_data is True
    assert SourceType.POSTGRES in plan.required_sources
    assert SourceType.DOCUMENT in plan.required_sources
    assert plan.sub_questions == []  # decomposition is a flag-gated enrichment


def test_entities_carry_real_identifiers_and_candidate_concepts() -> None:
    plan = _planned("Plinter M404 üçün kabel lazımdır")
    by_surface = {item.surface: item for item in plan.entities}
    assert by_surface["M404"].confidence == IDENTIFIER_CONFIDENCE
    assert by_surface["printer"].canonical_concept == "printer"
    assert by_surface["printer"].confidence == CONCEPT_CONFIDENCE


def test_decomposition_only_runs_when_its_flag_is_on() -> None:
    assert _planned(MULTI_TOPIC).sub_questions == []

    flags = EngineFlags(engine_enabled=True, decomposition_enabled=True)
    plan = build_query_plan(
        MULTI_TOPIC,
        understand(MULTI_TOPIC),
        deterministic=deterministic_plan(MULTI_TOPIC),
        flags=flags,
    )
    assert len(plan.sub_questions) == 3
    assert SourceType.POSTGRES in plan.sub_questions[2].required_sources


def test_ambiguity_requests_clarification_with_alternatives() -> None:
    plan = _planned("kabea")
    assert plan.clarification_required is True
    assert plan.clarification_options == ["kabel", "kabla"]


def test_deterministic_route_wins_and_the_model_is_not_called() -> None:
    model = _StubModel(_model_plan())
    outcome = asyncio.run(QueryPlanner(model=model).plan(1, "Kamera üçün hansı kabellər var?"))
    assert outcome.used_model is False
    assert outcome.fallback_deterministic is False
    assert outcome.notes == "deterministic_route"
    assert outcome.plan.intent == "DEVICE_ACCESSORY_LOOKUP"
    assert model.calls == []


def test_planner_without_a_model_reports_why_it_stayed_deterministic() -> None:
    outcome = asyncio.run(QueryPlanner(model=None).plan(1, NEEDS_MODEL))
    assert outcome.used_model is False
    assert outcome.notes == "no_model_available"
    assert outcome.plan.intent == "UNDECIDED"


def test_model_is_consulted_only_when_the_deterministic_layer_cannot_decide() -> None:
    model = _StubModel(_model_plan())
    outcome = asyncio.run(QueryPlanner(model=model).plan(7, NEEDS_MODEL))
    assert outcome.used_model is True
    assert outcome.plan.intent == "MODEL_ROUTE"
    request_type, content = model.calls[0]
    assert request_type == "gi_query_plan"
    assert NEEDS_MODEL in content
    assert "untrusted data" in content


def test_provider_failure_degrades_to_the_deterministic_plan() -> None:
    model = _StubModel(error=RuntimeError("provider down"))
    outcome = asyncio.run(QueryPlanner(model=model).plan(1, NEEDS_MODEL))
    assert outcome.used_model is False
    assert outcome.fallback_deterministic is True
    assert outcome.notes == "model_failed:RuntimeError"
    assert outcome.plan.intent == "UNDECIDED"


def test_planner_policy_treats_the_message_as_untrusted_data() -> None:
    assert "untrusted data" in PLANNER_POLICY
