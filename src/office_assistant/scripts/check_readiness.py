"""Read-only production readiness probe: DB, Qdrant, live SearXNG and pending work.

No data modifications, no credentials echoed and no billable model requests.
Run inside the running app container, not from a Docker host namespace.
"""

import asyncio
import json
from dataclasses import asdict, dataclass

import httpx
from sqlalchemy import func, select

from office_assistant.core.settings import load_settings
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
)
from office_assistant.infrastructure.database.session import create_session_factory


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
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return Check("searxng_search", "FAIL", "Malformed or disabled JSON search API")
    results = payload["results"]
    if not results:
        return Check(
            "searxng_search", "WARN", "HTTP endpoint is healthy, but live search returned no hits"
        )
    return Check("searxng_search", "OK", f"Live search returned {len(results)} result(s)")


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
            deferred = await session.scalar(
                select(func.count(DocumentParseRun.id)).where(
                    DocumentParseRun.status == DocumentProcessingStatus.DEFERRED
                )
            )
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
            index_running = await session.scalar(
                select(func.count(KnowledgeIndexJob.id)).where(
                    KnowledgeIndexJob.status.in_(
                        [KnowledgeIndexJobStatus.PENDING, KnowledgeIndexJobStatus.RUNNING]
                    )
                )
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
                "WARN" if indexing_failed else "OK",
                f"failed={int(indexing_failed or 0)} pending_or_running={int(index_running or 0)}",
            )
        )
        return checks
    finally:
        await engine.dispose()


async def check() -> tuple[str, list[Check]]:
    settings = load_settings()
    checks: list[Check] = []
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
                if not isinstance(payload, dict) or payload.get("status") != "ok":
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
