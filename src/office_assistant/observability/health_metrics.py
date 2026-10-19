"""Safe, bounded Prometheus metrics projection from the readiness doctor."""

import re
from dataclasses import dataclass

from office_assistant.scripts.check_readiness import Check

KNOWN_CHECKS = frozenset(
    {
        "postgresql",
        "qdrant",
        "searxng_search",
        "pdf_ocr",
        "document_processing",
        "search_index_jobs",
        "historical_pdf_ocr_replay",
        "false_success_index_records",
    }
)
KNOWN_COUNTS = frozenset(
    {"deferred", "ingestion_failed", "failed", "partial", "pending_or_running",
     "awaiting_index", "indexed"}
)
_STATUS_CODES = {"OK": 2, "WARN": 1, "FAIL": 0}
_READINESS_CODES = {"READY": 2, "DEGRADED": 1, "NOT_READY": 0}
_COUNT_PATTERN = re.compile(r"\b([a-z_]+)=([0-9]+)\b")


@dataclass(frozen=True, slots=True)
class SafeMetrics:
    readiness: int
    checks: dict[str, int]
    counters: dict[tuple[str, str], int]


def project_metrics(readiness: str, checks: list[Check]) -> SafeMetrics:
    """Only allow known metric names and finite nonnegative integer values."""
    status: dict[str, int] = {}
    totals: dict[tuple[str, str], int] = {}
    for item in checks:
        if item.name not in KNOWN_CHECKS:
            continue
        status[item.name] = _STATUS_CODES.get(item.status, 0)
        if item.name not in {
            "document_processing", "search_index_jobs", "historical_pdf_ocr_replay"
        }:
            continue
        for field, amount in _COUNT_PATTERN.findall(item.detail):
            if field in KNOWN_COUNTS:
                totals[(item.name, field)] = int(amount)
    return SafeMetrics(_READINESS_CODES.get(readiness, 0), status, totals)
