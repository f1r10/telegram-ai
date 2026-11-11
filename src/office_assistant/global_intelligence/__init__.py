"""Global Intelligence Engine — additive, feature-flagged orchestration layer.

Nothing in ``internal_evidence``, ``business_tools``, ``knowledge`` or ``processing`` is
replaced. The engine calls into them and every new path is gated by ``EngineFlags`` so the
shipped behaviour can be restored instantly (promt.md §18, TARGET_ARCHITECTURE.md §2).
"""
