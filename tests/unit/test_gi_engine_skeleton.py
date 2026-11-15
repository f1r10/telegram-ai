"""Engine skeleton: feature flags, settings wiring and evidence aggregation.

Phase 1 ships the engine contract and the deterministic path only. These tests pin the two
safety properties an operator relies on: every flag defaults to off, and the epistemic three
state contract never promotes a hypothesis to a confirmed company fact (promt.md §7, §13.4).
"""

from __future__ import annotations

import asyncio

from office_assistant.core.settings import Settings
from office_assistant.global_intelligence.engine import GlobalIntelligenceEngine
from office_assistant.global_intelligence.evidence import (
    StatementStatus,
    aggregate,
    statement_status,
)
from office_assistant.global_intelligence.flags import EngineFlags, engine_flags
from office_assistant.internal_evidence.models import EvidenceBundle, UnifiedCitation


def test_every_flag_defaults_to_off() -> None:
    assert set(EngineFlags().model_dump().values()) == {False}


def test_stage_requires_the_master_switch() -> None:
    off = EngineFlags(verification_enabled=True)
    assert off.stage("verification_enabled") is False

    on = EngineFlags(engine_enabled=True, verification_enabled=True)
    assert on.stage("verification_enabled") is True
    assert on.stage("relations_enabled") is False


def test_engine_flags_read_settings() -> None:
    flags = engine_flags(
        Settings(
            gi_engine_enabled=True,
            gi_verification_enabled=True,
            gi_relations_enabled=False,
        )
    )
    assert flags.engine_enabled is True
    assert flags.verification_enabled is True
    assert flags.relations_enabled is False
    assert flags.decomposition_enabled is False


def test_engine_is_disabled_by_default_and_enabled_only_by_settings() -> None:
    assert GlobalIntelligenceEngine().enabled is False
    assert GlobalIntelligenceEngine.from_settings(Settings()).enabled is False
    assert GlobalIntelligenceEngine.from_settings(Settings(gi_engine_enabled=True)).enabled is True


def test_engine_understand_is_deterministic() -> None:
    understanding = GlobalIntelligenceEngine().understand("Plinter üçün kabel var?")
    assert understanding.concepts == ("printer", "cable")
    assert understanding.clarification_required is False


def test_engine_plan_routes_a_device_question_internally() -> None:
    outcome = asyncio.run(GlobalIntelligenceEngine().plan(1, "Kamera üçün hansı kabellər var?"))
    assert outcome.plan.intent == "DEVICE_ACCESSORY_LOOKUP"
    assert outcome.used_model is False


def test_statement_status_is_derived_from_evidence_and_locator() -> None:
    assert statement_status(evidence_id="ev-1", locator={"page": "3"}) is StatementStatus.CONFIRMED
    assert statement_status(evidence_id="ev-1", locator=None) is StatementStatus.PROBABLE
    assert statement_status(evidence_id=None, locator={"page": "3"}) is StatementStatus.PROBABLE
    assert statement_status(evidence_id=None, locator=None) is StatementStatus.UNKNOWN
    assert (
        statement_status(evidence_id="ev-1", locator={"page": "3"}, inferred=True)
        is StatementStatus.PROBABLE
    )


def _citation(evidence_id: str, *, locator: dict[str, object] | None) -> UnifiedCitation:
    return UnifiedCitation(
        evidence_id=evidence_id,
        number=1,
        source_class="document",
        locator=locator,
        label="Kabel kataloqu",
    )


def test_aggregate_labels_citations_and_keeps_hypotheses_probable() -> None:
    bundle = EvidenceBundle(
        citations=[
            _citation("ev-1", locator={"page": "1"}),
            _citation("ev-2", locator=None),
        ]
    )
    result = aggregate(bundle, hypotheses=("Kamera üçün Ethernet kabeli lazım ola bilər",))
    assert [item.status for item in result.statements] == [
        StatementStatus.CONFIRMED,
        StatementStatus.PROBABLE,
        StatementStatus.PROBABLE,
    ]
    assert result.statements[0].is_well_supported_confirmed is True
    assert result.statements[1].is_well_supported_confirmed is False
    # A hypothesis is never a confirmed company fact, whatever the model believes.
    assert result.statements[2].is_well_supported_confirmed is False
    assert result.statements[2].evidence_id is None


def test_aggregate_of_an_empty_bundle_has_no_statements() -> None:
    result = GlobalIntelligenceEngine().aggregate(EvidenceBundle())
    assert result.statements == ()
    assert result.citations == ()
