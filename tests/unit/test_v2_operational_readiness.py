"""Operational readiness is read-only and never confuses healthz with real search."""

import subprocess

import pytest

from office_assistant.core.settings import Settings
from office_assistant.scripts import check_readiness

from office_assistant.scripts.check_readiness import Check, ocr_runtime_health, overall, search_health


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

    def fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if args[0] == "tesseract":
            return subprocess.CompletedProcess(args, 0, "List of available languages:\naze\neng\nrus\n")
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
