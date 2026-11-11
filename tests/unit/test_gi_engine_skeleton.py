"""Phase 1 — engine skeleton and feature flags (architecture §2/§5)."""

from __future__ import annotations

from office_assistant.core.settings import Settings
from office_assistant.global_intelligence.engine import GlobalIntelligenceEngine
from office_assistant.global_intelligence.flags import EngineFlags
from office_assistant.global_intelligence.understanding import understand_query
from office_assistant.internal_evidence.models import RoutingCategory


def test_engine_flags_default_all_false() -> None:
    flags = EngineFlags()
    assert not any(flags.model_dump().values())


def test_shipped_settings_leave_every_engine_flag_off() -> None:
    flags = EngineFlags.from_settings(Settings())
    assert not any(flags.model_dump().values())


def test_from_settings_maps_each_flag() -> None:
    settings = Settings(gi_engine_enabled=True, gi_query_understanding_enabled=True)
    flags = EngineFlags.from_settings(settings)
    assert flags.engine_enabled is True
    assert flags.query_understanding_enabled is True
    assert flags.verification_enabled is False


def test_engine_composes_understanding_and_planning() -> None:
    engine = GlobalIntelligenceEngine()
    question = "Plinter üçün kabel var?"
    assert engine.understand(question) == understand_query(question)
    plan = engine.plan(question)
    assert plan.category is RoutingCategory.INTERNAL_DOCUMENT
    assert plan.intent == "ACCESSORY_COMPATIBILITY"


def test_engine_stores_provided_flags() -> None:
    flags = EngineFlags(engine_enabled=True)
    engine = GlobalIntelligenceEngine(flags)
    assert engine.flags.engine_enabled is True
