"""No private user or document content can become a metric label."""

from office_assistant.observability.health_metrics import project_metrics
from office_assistant.scripts.check_readiness import Check


def test_privacy_boundaries_and_readiness_counts() -> None:
    result = project_metrics(
        "DEGRADED",
        [
            Check("postgresql", "OK", "healthy"),
            Check("document_processing", "WARN", "deferred=4 ingestion_failed=2 email=9876"),
            Check("user@example.com", "FAIL", "name=confidential"),
        ],
    )
    assert result.readiness == 1
    assert result.checks == {"postgresql": 2, "document_processing": 1}
    assert result.counters == {
        ("document_processing", "deferred"): 4,
        ("document_processing", "ingestion_failed"): 2,
    }


def test_unknown_health_never_becomes_a_false_positive() -> None:
    result = project_metrics("nonsense", [Check("qdrant", "nonsense", "secret=4")])
    assert result.readiness == 0
    assert result.checks == {"qdrant": 0}
    assert not result.counters
