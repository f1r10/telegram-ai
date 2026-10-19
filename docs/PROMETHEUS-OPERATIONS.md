# Operator-only Prometheus monitoring

The exporter is NOT enabled by default, does not replace the Telegram bot,
and must not be exposed publicly. It uses the official Prometheus Python
Client behind an isolated operator launch and binds ONLY to 127.0.0.1.

Start after provisioning the same runtime dependencies as the application,
using an explicitly reviewed Prometheus client version:

    uv run --with 'prometheus-client>=0.21,<1' python -m office_assistant.scripts.serve_metrics --port 18792 --interval-seconds 60

The option above is run manually in a trusted environment; no private
telemetry leaves the host merely by enabling the exporter. Prometheus
itself must be operator-configured to scrape localhost:18792/metrics.
Never expose the metrics port to WAN or a public reverse proxy.

The exporter calls the existing read-only readiness probe periodically.
It exposes fixed, known component labels and integer job totals only:
PostgreSQL, Qdrant, SearXNG, OCR, ingestion/index failures and replay
state, plus probe duration and failure count. Unknown status always means
not ready. No prompts, product codes, users, raw error strings, file names,
tokens, dynamic URLs, or source text appear in labels. Counts from the
readiness doctor are not an exhaustive inventory of every worker state.

The monitoring probe performs a small live SearXNG query. Do not schedule
a scrape faster than the declared 30-second minimum refresh. Confirm
health correctness and latency in isolated staging first; the core app
and worker remain operational if this optional metrics process fails.

Alerts should cover readiness_code < 2, component_health_code < 2,
increasing office_probe_failures_total, and persistent job backlog.
Set thresholds from a real baseline, rather than inventing SLA values.
This package does NOT establish a verified production monitoring rollout.
