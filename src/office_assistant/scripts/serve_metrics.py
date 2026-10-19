"""Operator-enabled loopback Prometheus Python Client exporter.

No company text, product identifiers, filenames, usernames, questions,
bot tokens or raw exceptions are exposed as metric labels or values.
"""

import argparse
import asyncio
import time
from importlib import import_module
from typing import Any, cast

from office_assistant.observability.health_metrics import (
    KNOWN_CHECKS,
    KNOWN_COUNTS,
    project_metrics,
)
from office_assistant.scripts.check_readiness import check

_COUNT_COMPONENTS = ("document_processing", "search_index_jobs", "historical_pdf_ocr_replay")


def serve(*, port: int, interval: int) -> None:
    if not 1024 <= port <= 65535 or not 30 <= interval <= 3600:
        raise ValueError("Invalid monitoring port or refresh interval")
    # Prometheus client is optional and isolated from the core bot dependencies.
    prometheus = cast(Any, import_module("prometheus_client"))
    readiness = prometheus.Gauge("office_readiness_code", "2 ready, 1 degraded, 0 not ready")
    components = prometheus.Gauge(
        "office_component_health_code", "Known component status", ["component"]
    )
    jobs = prometheus.Gauge(
        "office_operational_count", "Safe bounded job counts", ["kind", "state"]
    )
    failures = prometheus.Counter("office_probe_failures_total", "Failed monitoring probe cycles")
    duration = prometheus.Histogram("office_probe_seconds", "Readiness probe duration")
    for name in sorted(KNOWN_CHECKS):
        components.labels(component=name).set(-1)
    for name in _COUNT_COMPONENTS:
        for state in sorted(KNOWN_COUNTS):
            jobs.labels(kind=name, state=state).set(0)
    # Loopback only; operator's Prometheus agent must explicitly scrape locally.
    prometheus.start_http_server(port, addr="127.0.0.1")
    while True:
        started = time.monotonic()
        try:
            state, checks = asyncio.run(check())
            sample = project_metrics(state, checks)
            readiness.set(sample.readiness)
            for name in KNOWN_CHECKS:
                components.labels(component=name).set(sample.checks.get(name, -1))
            for name in _COUNT_COMPONENTS:
                for label in KNOWN_COUNTS:
                    jobs.labels(kind=name, state=label).set(sample.counters.get((name, label), 0))
        except Exception:
            readiness.set(0)
            failures.inc()
            for name in KNOWN_CHECKS:
                components.labels(component=name).set(-1)
            for name in _COUNT_COMPONENTS:
                for label in KNOWN_COUNTS:
                    jobs.labels(kind=name, state=label).set(0)
        finally:
            duration.observe(time.monotonic() - started)
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local-only sanitized Prometheus metrics")
    parser.add_argument("--port", type=int, default=18792)
    parser.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args()
    serve(port=args.port, interval=args.interval_seconds)


if __name__ == "__main__":
    main()
