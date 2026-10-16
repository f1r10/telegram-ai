"""Operational readiness is read-only and never confuses healthz with real search."""

from office_assistant.scripts.check_readiness import Check, overall, search_health


def test_live_search_requires_actual_results_not_http_success() -> None:
    assert search_health({"results": []}).status == "WARN"
    assert search_health({"results": [{"url": "https://example.org"}]}).status == "OK"
    assert search_health({"errors": []}).status == "FAIL"
    assert search_health("not json").status == "FAIL"


def test_readiness_summary_never_masks_partial_failures() -> None:
    assert overall([Check("db", "OK", "fine"), Check("qdrant", "OK", "fine")]) == "READY"
    assert overall([Check("db", "OK", "fine"), Check("search", "WARN", "empty")]) == "DEGRADED"
    assert (
        overall([Check("db", "FAIL", "unavailable"), Check("search", "WARN", "empty")])
        == "NOT_READY"
    )
