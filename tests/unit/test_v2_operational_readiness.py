"""Operational readiness is read-only and never confuses healthz with real search."""

import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from office_assistant.core.settings import Settings
from office_assistant.scripts import check_readiness
from office_assistant.scripts.check_readiness import (
    Check,
    current_deferred_parse_count_query,
    ocr_runtime_health,
    order_reminder_delivery_health,
    overall,
    replay_progress_health,
    search_health,
)


@pytest.fixture(autouse=True)
def no_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)


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


def test_pdf_ocr_is_only_ready_with_renderer_and_requested_languages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_which(_binary: str) -> str:
        return "/usr/bin/available"

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[0] == "tesseract":
            return subprocess.CompletedProcess(
                args, 0, "List of available languages:\naze\neng\nrus\n"
            )
        return subprocess.CompletedProcess(args, 0, "")

    monkeypatch.setattr(check_readiness.shutil, "which", fake_which)
    monkeypatch.setattr(check_readiness.subprocess, "run", fake_run)
    assert ocr_runtime_health(Settings(pdf_ocr_languages="aze+eng+rus")).status == "OK"
    assert ocr_runtime_health(Settings(pdf_ocr_languages="aze+eng+rus+fra")).status == "WARN"


def test_pdf_ocr_readiness_reports_missing_binary_or_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(binary: str) -> str | None:
        return None if binary == "tesseract" else "/usr/bin/pdftoppm"

    monkeypatch.setattr(check_readiness.shutil, "which", missing)
    assert ocr_runtime_health(Settings(pdf_ocr_enabled=True)).status == "WARN"
    assert ocr_runtime_health(Settings(pdf_ocr_enabled=False)).status == "WARN"


def test_pdf_ocr_readiness_sanitizes_engine_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_which(_binary: str) -> str:
        return "/usr/bin/test"

    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(["secret/document.pdf"], 5)

    monkeypatch.setattr(check_readiness.shutil, "which", fake_which)
    monkeypatch.setattr(check_readiness.subprocess, "run", timeout)
    outcome = ocr_runtime_health(Settings())
    assert outcome.status == "WARN"
    assert "secret" not in outcome.detail


def test_expected_on_demand_image_probe_does_not_degrade_readiness() -> None:
    from sqlalchemy.dialects import postgresql

    statement = current_deferred_parse_count_query()
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "document_parse_runs.parser_name != 'image-probe'" in sql
    assert "document_parse_runs.status = 'DEFERRED'" in sql


def test_replay_progress_requires_nonempty_indexed_vectors() -> None:
    assert replay_progress_health(pending=0, failed=0, awaiting_index=0, indexed=4).status == "OK"
    assert replay_progress_health(pending=0, failed=0, awaiting_index=1, indexed=0).status == "WARN"
    assert replay_progress_health(pending=0, failed=1, awaiting_index=0, indexed=1).status == "WARN"
    assert replay_progress_health(pending=2, failed=0, awaiting_index=0, indexed=0).status == "WARN"
    detail = replay_progress_health(pending=2, failed=1, awaiting_index=3, indexed=4).detail
    assert detail == "pending_or_running=2 failed=1 awaiting_index=3 indexed=4"


@pytest.mark.parametrize(
    (
        "ledger_failed",
        "outbox_failed",
        "ledger_stale",
        "outbox_stale",
        "ledger_unknown",
        "outbox_unknown",
    ),
    [
        (1, 0, 0, 0, 0, 0),
        (0, 1, 0, 0, 0, 0),
        (0, 0, 1, 0, 0, 0),
        (0, 0, 0, 1, 0, 0),
        (0, 0, 0, 0, 1, 0),
        (0, 0, 0, 0, 0, 1),
        (0, 0, 0, 0, 0, 0),
    ],
)
def test_order_delivery_warns_only_for_failed_unknown_or_stale(
    ledger_failed: int,
    outbox_failed: int,
    ledger_stale: int,
    outbox_stale: int,
    ledger_unknown: int,
    outbox_unknown: int,
) -> None:
    now = datetime(2026, 10, 7, tzinfo=UTC)
    check = order_reminder_delivery_health(
        ledger_counts={"FAILED": ledger_failed, "PENDING": 10, "RETRY_PENDING": 3, "SENDING": 2},
        outbox_counts={"FAILED": outbox_failed, "PENDING": 5, "SENDING": 2},
        ledger_stale_sending=ledger_stale,
        outbox_stale_sending=outbox_stale,
        ledger_unknown=ledger_unknown,
        outbox_unknown=outbox_unknown,
        oldest_due_pending=now - timedelta(days=2),
        now=now,
    )
    warning = any(
        (ledger_failed, outbox_failed, ledger_stale, outbox_stale, ledger_unknown, outbox_unknown)
    )
    assert check.name == "order_reminder_delivery"
    assert check.status == ("WARN" if warning else "OK")
    assert overall([check]) == ("DEGRADED" if warning else "READY")
    assert "active_due_PENDING_oldest_lag_seconds=172800" in check.detail


@pytest.mark.parametrize("pending_age", [None, -1.0, 0.0, 90.9])
def test_order_delivery_count_output_is_fixed_and_lag_is_nonnegative(
    pending_age: float | None,
) -> None:
    now = datetime(2026, 10, 7, tzinfo=UTC)
    check = order_reminder_delivery_health(
        ledger_counts={"SENT": 12, "SKIPPED": 2, "untrusted-ledger-status": 999},
        outbox_counts={"SENT": 12, "untrusted-outbox-status": 999},
        ledger_stale_sending=0,
        outbox_stale_sending=0,
        ledger_unknown=0,
        outbox_unknown=0,
        oldest_due_pending=now - timedelta(seconds=pending_age)
        if pending_age is not None
        else None,
        now=now,
    )
    lag = max(0, int(pending_age or 0))
    assert check == Check(
        "order_reminder_delivery",
        "OK",
        "ledger[PENDING=0 CLAIMED=0 SENDING=0 SENT=12 RETRY_PENDING=0 FAILED=0 "
        "SUPERSEDED=0 SKIPPED=2 CANCELLED=0] "
        "outbox[PENDING=0 SENDING=0 SENT=12 CANCELLED=0 FAILED=0] "
        "ledger_stale_SENDING=0 outbox_stale_SENDING=0 "
        "ledger_DELIVERY_UNKNOWN=0 outbox_DELIVERY_UNKNOWN=0 "
        f"active_due_PENDING_oldest_lag_seconds={lag}",
    )
