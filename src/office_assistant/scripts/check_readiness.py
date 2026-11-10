"""Read-only production readiness probe: DB, Qdrant, live SearXNG and pending work.

No data modifications, no credentials echoed and no billable model requests.
Run inside the running app container, not from a Docker host namespace.
"""

import asyncio
import json
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    DocumentParseRun,
    DocumentProcessingStatus,
    IngestionJob,
    IngestionJobStatus,
    KnowledgeIndexJob,
    KnowledgeIndexJobStatus,
    KnowledgeIndexRecord,
    KnowledgeIndexRecordStatus,
    OcrReplayJob,
    Order,
    OrderReminder,
    OrderReminderStatus,
    OrderState,
    OutboxEvent,
    OutboxEventStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.processing.pdf_ocr import PdfOcrConfig


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: str
    detail: str


def overall(checks: list[Check]) -> str:
    if any(item.status == "FAIL" for item in checks):
        return "NOT_READY"
    if any(item.status == "WARN" for item in checks):
        return "DEGRADED"
    return "READY"


def search_health(payload: object) -> Check:
    if not isinstance(payload, dict):
        return Check("searxng_search", "FAIL", "Malformed or disabled JSON search API")
    value = cast(dict[str, object], payload).get("results")
    if not isinstance(value, list):
        return Check("searxng_search", "FAIL", "Malformed or disabled JSON search API")
    results = cast(list[object], value)
    if not results:
        return Check(
            "searxng_search", "WARN", "HTTP endpoint is healthy, but live search returned no hits"
        )
    return Check("searxng_search", "OK", f"Live search returned {len(results)} result(s)")


def ocr_runtime_health(settings: Settings) -> Check:
    """Check the actual local OCR prerequisites without reading employee documents."""

    if not settings.pdf_ocr_enabled:
        return Check("pdf_ocr", "WARN", "Worker PDF OCR is disabled")
    try:
        PdfOcrConfig(
            languages=settings.pdf_ocr_languages,
            max_pages=settings.pdf_ocr_max_pages,
            page_timeout_seconds=settings.pdf_ocr_page_timeout_seconds,
            document_timeout_seconds=settings.pdf_ocr_document_timeout_seconds,
        )
    except ValueError:
        return Check("pdf_ocr", "WARN", "Worker PDF OCR settings are invalid")

    for binary in ("pdftoppm", "tesseract"):
        if shutil.which(binary) is None:
            return Check("pdf_ocr", "WARN", f"Required local OCR executable missing: {binary}")

    try:
        renderer = subprocess.run(
            ["pdftoppm", "-v"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        languages = subprocess.run(
            ["tesseract", "--list-langs"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return Check("pdf_ocr", "WARN", "Local OCR runtime failed its bounded health probe")
    if renderer.returncode or languages.returncode:
        return Check("pdf_ocr", "WARN", "Local OCR runtime executable is not operational")

    installed = set(languages.stdout.splitlines())
    expected = set(settings.pdf_ocr_languages.split("+"))
    missing = sorted(expected - installed)
    if missing:
        return Check("pdf_ocr", "WARN", "Missing OCR language data: " + ", ".join(missing))
    return Check("pdf_ocr", "OK", "Local PDF renderer and configured OCR languages available")


def replay_progress_health(
    *, pending: int, failed: int, awaiting_index: int, indexed: int
) -> Check:
    """Operator OCR attempts are not complete until nonempty vectors are persisted."""

    detail = (
        f"pending_or_running={pending} failed={failed} "
        f"awaiting_index={awaiting_index} indexed={indexed}"
    )
    status = "WARN" if pending or failed or awaiting_index else "OK"
    return Check("historical_pdf_ocr_replay", status, detail)


def order_reminder_delivery_health(
    *,
    ledger_counts: Mapping[str, int],
    outbox_counts: Mapping[str, int],
    ledger_stale_sending: int,
    outbox_stale_sending: int,
    ledger_unknown: int,
    outbox_unknown: int,
    oldest_due_pending: datetime | None,
    now: datetime,
) -> Check:
    """Report fixed aggregate labels only; plans and retries alone are not failures."""

    ledger = " ".join(f"{status}={ledger_counts.get(status, 0)}" for status in OrderReminderStatus)
    outbox = " ".join(f"{status}={outbox_counts.get(status, 0)}" for status in OutboxEventStatus)
    lag = max(0, int((now - oldest_due_pending).total_seconds())) if oldest_due_pending else 0
    warning = (
        ledger_counts.get(OrderReminderStatus.FAILED, 0)
        or outbox_counts.get(OutboxEventStatus.FAILED, 0)
        or ledger_stale_sending
        or outbox_stale_sending
        or ledger_unknown
        or outbox_unknown
    )
    return Check(
        "order_reminder_delivery",
        "WARN" if warning else "OK",
        f"ledger[{ledger}] outbox[{outbox}] "
        f"ledger_stale_SENDING={ledger_stale_sending} "
        f"outbox_stale_SENDING={outbox_stale_sending} "
        f"ledger_DELIVERY_UNKNOWN={ledger_unknown} outbox_DELIVERY_UNKNOWN={outbox_unknown} "
        f"active_due_PENDING_oldest_lag_seconds={lag}",
    )


async def order_reminder_delivery_check(
    session: AsyncSession, *, now: datetime, lease_seconds: int
) -> Check:
    """Read aggregate delivery state, without fetching payloads or provider errors."""

    cutoff = now - timedelta(seconds=lease_seconds)
    ledger = (
        await session.execute(
            select(
                OrderReminder.status,
                func.count(),
                func.count().filter(OrderReminder.last_error == "DELIVERY_UNKNOWN"),
                func.count().filter(
                    OrderReminder.status == OrderReminderStatus.SENDING,
                    OrderReminder.claimed_at < cutoff,
                ),
                func.min(OrderReminder.scheduled_for).filter(
                    OrderReminder.status == OrderReminderStatus.PENDING,
                    OrderReminder.scheduled_for <= now,
                    Order.state == OrderState.ACTIVE,
                ),
            )
            .join(Order, Order.id == OrderReminder.order_id)
            .group_by(OrderReminder.status)
        )
    ).all()
    # Outbox rows have no claim timestamp. Match the durable ledger even if its
    # status has since changed (for example, cancellation during an interrupted send).
    stale_claim = exists(
        select(OrderReminder.id).where(
            OrderReminder.id == OutboxEvent.payload["reminder_id"].as_integer(),
            OrderReminder.claimed_at < cutoff,
        )
    )
    outbox = (
        await session.execute(
            select(
                OutboxEvent.status,
                func.count(),
                func.count().filter(OutboxEvent.last_error == "DELIVERY_UNKNOWN"),
                func.count().filter(OutboxEvent.status == OutboxEventStatus.SENDING, stale_claim),
            )
            .where(OutboxEvent.event_type == "ORDER_REMINDER_DUE")
            .group_by(OutboxEvent.status)
        )
    ).all()
    # Status totals include historical/inactive plans, but only ACTIVE orders have
    # due delivery work. Never present frozen/cancelled plans as pending delivery lag.
    oldest = min((row[4] for row in ledger if row[4] is not None), default=None)
    return order_reminder_delivery_health(
        ledger_counts={row[0]: int(row[1]) for row in ledger},
        outbox_counts={row[0]: int(row[1]) for row in outbox},
        ledger_stale_sending=sum(int(row[3]) for row in ledger),
        outbox_stale_sending=sum(int(row[3]) for row in outbox),
        ledger_unknown=sum(int(row[2]) for row in ledger),
        outbox_unknown=sum(int(row[2]) for row in outbox),
        oldest_due_pending=oldest,
        now=now,
    )


def current_deferred_parse_count_query() -> Select[tuple[int]]:
    """Count unresolved text parses, excluding on-demand standalone image probes.

    ImageProbeParser intentionally leaves image-only uploads DEFERRED when OCR finds no
    text. Their original bytes are registered as searchable visual assets, and vision
    reading is performed only when a user asks about that image; these are not stuck
    document-ingestion jobs and must not degrade service readiness.
    """

    current_parse = (
        select(func.max(DocumentParseRun.id).label("id"))
        .group_by(DocumentParseRun.file_object_id)
        .subquery()
    )
    return (
        select(func.count(DocumentParseRun.id))
        .join(current_parse, DocumentParseRun.id == current_parse.c.id)
        .where(
            DocumentParseRun.status == DocumentProcessingStatus.DEFERRED,
            DocumentParseRun.parser_name != "image-probe",
        )
    )


async def _database() -> list[Check]:
    settings = load_settings()
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            indexed = await session.scalar(
                select(func.count(KnowledgeIndexRecord.id)).where(
                    KnowledgeIndexRecord.status == KnowledgeIndexRecordStatus.SUCCEEDED,
                    KnowledgeIndexRecord.chunk_count == 0,
                )
            )
            # Count unresolved *current* text parses only. On-demand image probes are
            # intentionally DEFERRED after visual-asset registration and are not stuck jobs.
            deferred = await session.scalar(current_deferred_parse_count_query())
            ingestion_failed = await session.scalar(
                select(func.count(IngestionJob.id)).where(
                    IngestionJob.status == IngestionJobStatus.FAILED
                )
            )
            indexing_failed = await session.scalar(
                select(func.count(KnowledgeIndexJob.id)).where(
                    KnowledgeIndexJob.status == KnowledgeIndexJobStatus.FAILED
                )
            )
            indexing_partial = await session.scalar(
                select(func.count(KnowledgeIndexJob.id)).where(
                    KnowledgeIndexJob.status == KnowledgeIndexJobStatus.PARTIAL
                )
            )
            replay_pending = await session.scalar(
                select(func.count(OcrReplayJob.id)).where(
                    OcrReplayJob.status.in_(["PENDING", "RUNNING"])
                )
            )
            replay_failed = await session.scalar(
                select(func.count(OcrReplayJob.id)).where(OcrReplayJob.status == "FAILED")
            )
            indexed_replay = exists(
                select(KnowledgeIndexRecord.id).where(
                    KnowledgeIndexRecord.parse_run_id == OcrReplayJob.result_parse_run_id,
                    KnowledgeIndexRecord.status == KnowledgeIndexRecordStatus.SUCCEEDED,
                    KnowledgeIndexRecord.chunk_count > 0,
                )
            )
            replay_waiting = await session.scalar(
                select(func.count(OcrReplayJob.id)).where(
                    OcrReplayJob.status == "INDEX_QUEUED",
                    ~indexed_replay,
                )
            )
            replay_indexed = await session.scalar(
                select(func.count(OcrReplayJob.id)).where(
                    OcrReplayJob.status == "INDEX_QUEUED",
                    indexed_replay,
                )
            )
            index_running = await session.scalar(
                select(func.count(KnowledgeIndexJob.id)).where(
                    KnowledgeIndexJob.status.in_(
                        [KnowledgeIndexJobStatus.PENDING, KnowledgeIndexJobStatus.RUNNING]
                    )
                )
            )
            order_delivery = await order_reminder_delivery_check(
                session, now=datetime.now(UTC), lease_seconds=settings.worker_lease_seconds
            )
        checks = [Check("postgresql", "OK", "SELECT and job-table queries passed")]
        zero = int(indexed or 0)
        checks.append(
            Check(
                "false_success_index_records",
                "WARN" if zero else "OK",
                f"{zero} historical successful records contain 0 searchable chunks",
            )
        )
        checks.append(
            Check(
                "document_processing",
                "WARN" if deferred or ingestion_failed else "OK",
                f"deferred={int(deferred or 0)} ingestion_failed={int(ingestion_failed or 0)}",
            )
        )
        checks.append(
            Check(
                "search_index_jobs",
                "WARN" if indexing_failed or indexing_partial else "OK",
                f"failed={int(indexing_failed or 0)} partial={int(indexing_partial or 0)} "
                f"pending_or_running={int(index_running or 0)}",
            )
        )
        checks.append(
            replay_progress_health(
                pending=int(replay_pending or 0),
                failed=int(replay_failed or 0),
                awaiting_index=int(replay_waiting or 0),
                indexed=int(replay_indexed or 0),
            )
        )
        checks.append(order_delivery)
        return checks
    finally:
        await engine.dispose()


async def check() -> tuple[str, list[Check]]:
    settings = load_settings()
    checks: list[Check] = [await asyncio.to_thread(ocr_runtime_health, settings)]
    try:
        checks.extend(await asyncio.wait_for(_database(), timeout=15.0))
    except Exception as error:
        checks.append(Check("postgresql", "FAIL", f"Database unavailable ({type(error).__name__})"))
    async with httpx.AsyncClient(timeout=12.0, trust_env=False) as client:
        if settings.qdrant_url:
            try:
                response = await client.get(settings.qdrant_url.rstrip("/") + "/collections")
                response.raise_for_status()
                payload: object = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Unexpected Qdrant response")
                if cast(dict[str, object], payload).get("status") != "ok":
                    raise ValueError("Unexpected Qdrant response")
                checks.append(Check("qdrant", "OK", "Collection API is accessible"))
            except (httpx.HTTPError, ValueError) as error:
                checks.append(Check("qdrant", "FAIL", f"Unavailable ({type(error).__name__})"))
        else:
            checks.append(Check("qdrant", "FAIL", "QDRANT_URL not configured"))
        if settings.searxng_base_url:
            try:
                response = await client.get(
                    settings.searxng_base_url.rstrip("/") + "/search",
                    params={"q": "Wikipedia", "format": "json", "categories": "general"},
                )
                response.raise_for_status()
                checks.append(search_health(response.json()))
            except (httpx.HTTPError, ValueError) as error:
                checks.append(
                    Check("searxng_search", "WARN", f"Live search failed ({type(error).__name__})")
                )
        else:
            checks.append(Check("searxng_search", "WARN", "SearXNG is not configured"))
    return overall(checks), checks


def main() -> None:
    try:
        state, checks = asyncio.run(check())
    except Exception as error:
        print(json.dumps({"readiness": "NOT_READY", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
    print(
        json.dumps(
            {"readiness": state, "checks": [asdict(item) for item in checks]},
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(0 if state == "READY" else 2)


if __name__ == "__main__":
    main()
