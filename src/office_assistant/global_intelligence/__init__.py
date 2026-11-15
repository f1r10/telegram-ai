"""Global Intelligence Engine (promt.md 4).

A modular orchestration layer inside the existing application. It is intentionally not
imported for side effects: nothing in this package runs at import time, so a deployment can
ship it disabled and pay no cost.

Layers:

* :mod:`~office_assistant.global_intelligence.understanding` - deterministic, offline query
  understanding (normalization, typo tolerance, concepts, decomposition, language).
* :mod:`~office_assistant.global_intelligence.planning` - the plan the engine executes.
* :mod:`~office_assistant.global_intelligence.evidence` - confidence states and aggregation.
* :mod:`~office_assistant.global_intelligence.engine` - the orchestration entry point.
"""
