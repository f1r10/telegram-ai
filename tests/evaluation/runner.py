"""Evaluation runner for the S17.2 scenarios.

The runner is provider-agnostic: it measures whatever ``AnswerProvider`` it is given. Two providers
ship with Phase 0:

* :class:`ProductionRoutingProvider` — calls the **real** shipped routing function
  ``internal_evidence.router.deterministic_plan``. It measures routing decisions only and reports no
  retrieved candidates or statements, because retrieval needs PostgreSQL, Qdrant and an LLM key that
  are unavailable offline.

Optional third-party provider: a full end-to-end provider will be added in Phase 1+ once the engine
exists. Until then every end-to-end metric in ``docs/evaluation/BENCHMARK_RESULTS.md`` is marked
NOT VERIFIED rather than left blank.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from office_assistant.internal_evidence.router import deterministic_plan

from .metrics import (
    HarnessStatement,
    citation_presence,
    label_coverage,
    mean,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    unsupported_confirmed_rate,
)
from .scenarios import (
    SCENARIOS,
    EvaluationScenario,
    EvaluationSource,
)


@dataclass(frozen=True, slots=True)
class HarnessAnswer:
    """A candidate answer in the shape the metrics need, independent of the real implementation."""

    text: str
    sources_used: frozenset[EvaluationSource] = frozenset()
    cited_evidence_ids: tuple[str, ...] = ()
    retrieved_document_ids: tuple[str, ...] = ()
    statements: tuple[HarnessStatement, ...] = ()
    clarifying: bool = False
    unavailable_sources: frozenset[str] = frozenset()
    notes: str = ""


class AnswerProvider(Protocol):
    """Anything that can answer an evaluation scenario."""

    name: str

    async def answer(self, scenario: EvaluationScenario) -> HarnessAnswer: ...


@dataclass(frozen=True, slots=True)
class ScenarioOutcome:
    """What one provider did for one scenario."""

    scenario_id: str
    required_sources: frozenset[EvaluationSource]
    routed_sources: frozenset[EvaluationSource]
    relevant_document_ids: tuple[str, ...]
    required_external_internal_fallback: bool
    plan_requires_model: bool
    routing_observable: bool
    answer: HarnessAnswer

    @property
    def missing_sources(self) -> frozenset[EvaluationSource]:
        return self.required_sources - self.routed_sources

    @property
    def route_coverage(self) -> float:
        """Share of the scenario's required source groups the provider actually consulted."""

        if not self.required_sources:
            return 1.0
        return len(self.routed_sources & self.required_sources) / len(self.required_sources)


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Per-scenario outcomes plus the S17.3 aggregates."""

    provider_name: str
    outcomes: tuple[ScenarioOutcome, ...]
    retrieval_scenarios: int
    planning_scenarios: int

    def aggregate(self) -> Mapping[str, float]:
        observable = [item for item in self.outcomes if item.routing_observable]
        return {
            "scenarios": float(len(self.outcomes)),
            "routing_coverage_mean": mean([item.route_coverage for item in observable]),
            "required_source_miss_rate": mean(
                [1.0 if item.missing_sources else 0.0 for item in observable]
            ),
            "unsafe_general_fallback_rate": mean(
                [1.0 if item.required_external_internal_fallback else 0.0 for item in observable]
            ),
            "plan_requires_model_rate": mean(
                [1.0 if item.plan_requires_model else 0.0 for item in observable]
            ),
            "citation_presence_mean": mean(
                [citation_presence(item.answer.cited_evidence_ids) for item in self.outcomes]
            ),
            "unsupported_confirmed_rate_mean": mean(
                [unsupported_confirmed_rate(item.answer.statements) for item in self.outcomes]
            ),
            "label_coverage_mean": mean(
                [label_coverage(item.answer.statements) for item in self.outcomes]
            ),
        }

    def retrieval_metrics(self, k: int = 10) -> Mapping[str, float]:
        """Retrieval metrics over the scenarios that returned candidate documents.

        Returns empty values (0.0 with a zero contributing count) when the provider retrieves
        nothing, so the report can print "not measured" instead of a misleading zero.
        """

        contributing = [
            item
            for item in self.outcomes
            if item.answer.retrieved_document_ids and item.relevant_document_ids
        ]
        if not contributing:
            return {"contributing_scenarios": 0.0, "recall_at_k": 0.0, "mrr": 0.0, "ndcg_at_k": 0.0}
        return {
            "contributing_scenarios": float(len(contributing)),
            "recall_at_k": mean(
                [
                    recall_at_k(item.relevant_document_ids, item.answer.retrieved_document_ids, k)
                    for item in contributing
                ]
            ),
            "mrr": mean(
                [
                    reciprocal_rank(item.relevant_document_ids, item.answer.retrieved_document_ids)
                    for item in contributing
                ]
            ),
            "ndcg_at_k": mean(
                [
                    ndcg_at_k(item.relevant_document_ids, item.answer.retrieved_document_ids, k)
                    for item in contributing
                ]
            ),
        }

    def render_markdown(self) -> str:
        lines = [
            f"Provider: `{self.provider_name}`",
            "",
            "| Scenario | Required sources | Routed sources | Coverage | Missing | "
            "Unsafe general fallback | Plan needs model |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for item in self.outcomes:
            row = (
                "| {sid} | {required} | {routed} | {coverage:.2f} | {missing} | "
                "{unsafe} | {model} |"
            ).format(
                sid=item.scenario_id,
                required=", ".join(sorted(item.required_sources)) or "-",
                routed=", ".join(sorted(item.routed_sources)) or "-",
                coverage=item.route_coverage,
                missing=", ".join(sorted(item.missing_sources)) or "-",
                unsafe="yes" if item.required_external_internal_fallback else "no",
                model="yes" if item.plan_requires_model else "no",
            )
            lines.append(row)
        aggregates = self.aggregate()
        lines += ["", "Aggregates:", ""]
        for key, value in aggregates.items():
            lines.append(f"- `{key}` = {value:.3f}")
        lines += ["", f"- retrieval scenarios contributing: {self.retrieval_scenarios}"]
        lines += ["", f"- planning scenarios observed: {self.planning_scenarios}"]
        return "\n".join(lines)


class ProductionRoutingProvider:
    """Measure the shipped planner's routing decision.

    This provider deliberately does **not** fabricate answers: it reports which source groups the
    real production function ``deterministic_plan`` selects for each scenario query. A ``None``
    result means the shipped code hands the question to the LLM planner, which cannot run offline,
    and is recorded as ``plan_requires_model``.
    """

    name = "production-routing (office_assistant.internal_evidence.router.deterministic_plan)"

    async def answer(self, scenario: EvaluationScenario) -> HarnessAnswer:
        plan = deterministic_plan(scenario.query)
        if plan is None:
            return HarnessAnswer(
                text="",
                notes=(
                    "deterministic_plan returned None: the shipped code escalates "
                    "to the LLM planner"
                ),
            )
        sources: set[EvaluationSource] = set()
        if plan.use_structured_tools:
            sources.add(EvaluationSource.POSTGRES)
        if plan.use_document_search:
            sources.add(EvaluationSource.DOCUMENT)
        return HarnessAnswer(
            text="",
            sources_used=frozenset(sources),
            notes=f"category={plan.category} intent={plan.intent} reason={plan.reason_code}",
        )


class EvaluationRunner:
    """Run scenarios through a provider and collect the S17.3 metrics."""

    def __init__(self, provider: AnswerProvider) -> None:
        self._provider = provider

    async def run(self, scenarios: Sequence[EvaluationScenario] = SCENARIOS) -> EvaluationReport:
        outcomes: list[ScenarioOutcome] = []
        for scenario in scenarios:
            answer = await self._provider.answer(scenario)
            plan_requires_model = not answer.sources_used and not answer.text
            unsafe_fallback = self._is_unsafe_general_fallback(scenario, answer)
            outcomes.append(
                ScenarioOutcome(
                    scenario_id=scenario.scenario_id,
                    required_sources=scenario.required_sources,
                    routed_sources=answer.sources_used,
                    relevant_document_ids=scenario.relevant_document_ids,
                    required_external_internal_fallback=unsafe_fallback,
                    plan_requires_model=plan_requires_model,
                    routing_observable=scenario.routing_observable,
                    answer=answer,
                )
            )
        report = EvaluationReport(
            provider_name=self._provider.name,
            outcomes=tuple(outcomes),
            retrieval_scenarios=sum(1 for item in outcomes if item.answer.retrieved_document_ids),
            planning_scenarios=sum(1 for item in outcomes if not item.plan_requires_model),
        )
        return report

    @staticmethod
    def _is_unsafe_general_fallback(scenario: EvaluationScenario, answer: HarnessAnswer) -> bool:
        """True when an internal question was routed to general model knowledge.

        This is defect F1 in ``FAILURE_ANALYSIS.md``: the question needs company sources, but the
        router selected the general-chat path, which answers from model memory.
        """

        needs_internal = bool(
            scenario.required_sources
            & {EvaluationSource.POSTGRES, EvaluationSource.DOCUMENT, EvaluationSource.GRAPH}
        )
        if not needs_internal:
            return False
        if answer.unavailable_sources:
            return False
        return not answer.sources_used


__all__ = [
    "AnswerProvider",
    "EvaluationReport",
    "EvaluationRunner",
    "HarnessAnswer",
    "ProductionRoutingProvider",
    "ScenarioOutcome",
]
