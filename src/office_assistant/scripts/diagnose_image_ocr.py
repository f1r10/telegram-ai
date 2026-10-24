"""Content-redacted, read-only OCR and retrieval probe for one image upload."""

import argparse
import asyncio
import json
import re
from pathlib import Path

from sqlalchemy import select

from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    DocumentParseRun,
    FileObject,
    FileObjectState,
    KnowledgeIndexRecord,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage, StorageError
from office_assistant.internal_evidence.document_search import DocumentRelevancePolicy
from office_assistant.internal_evidence.query_normalization import (
    retrieval_query,
    strong_identifiers,
)
from office_assistant.knowledge.embeddings import FastEmbedSparseAdapter, FastEmbedTextAdapter
from office_assistant.knowledge.models import RetrievalFilter
from office_assistant.knowledge.qdrant_store import QdrantKnowledgeStore
from office_assistant.knowledge.retrieval import KnowledgeRetrievalService

MAX_ARTIFACT_BYTES = 1024 * 1024


def expected_text_flags(
    text: str, *, code: str, quantity: int | None, lot: str | None
) -> dict[str, bool | None]:
    def exact(value: str) -> bool:
        return re.search(
            rf"(?<![A-Za-z0-9-]){re.escape(value)}(?![A-Za-z0-9-])",
            text,
            flags=re.IGNORECASE,
        ) is not None

    return {
        "ocr_code_exact": exact(code),
        "ocr_code_normalized": re.sub(r"[-\s]+", "", code).upper() in strong_identifiers(text),
        "ocr_quantity_present": (
            re.search(rf"(?<!\d){quantity}(?!\d)", text) is not None
            if quantity is not None
            else None
        ),
        "ocr_lot_exact": exact(lot) if lot else None,
    }


async def inspect_image(
    filename: str, code: str, *, quantity: int | None, lot: str | None, probe: bool
) -> dict[str, object]:
    if Path(filename).name != filename or not filename.lower().endswith(
        (".png", ".jpg", ".jpeg")
    ):
        raise ValueError("Invalid filename")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{1,63}", code) is None:
        raise ValueError("Invalid identifier")
    if lot and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{1,63}", lot) is None:
        raise ValueError("Invalid lot")
    if quantity is not None and not 0 <= quantity <= 1000000:
        raise ValueError("Invalid quantity")

    settings = load_settings()
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    report: dict[str, object] = {"diagnostic": "IMAGE_OCR_READ_ONLY"}
    file_id: int | None = None
    try:
        async with factory() as session:
            files = (
                await session.execute(
                    select(FileObject)
                    .where(
                        FileObject.original_filename == filename,
                        FileObject.state == FileObjectState.ACTIVE,
                    )
                    .order_by(FileObject.id.desc())
                    .limit(2)
                )
            ).scalars().all()
            if len(files) != 1:
                return {**report, "state": "NOT_FOUND" if not files else "AMBIGUOUS"}
            file_id = files[0].id
            run = await session.scalar(
                select(DocumentParseRun)
                .where(DocumentParseRun.file_object_id == file_id)
                .order_by(DocumentParseRun.id.desc())
                .limit(1)
            )
            if run is None:
                return {**report, "state": "NO_PARSE"}
            indexed = await session.scalar(
                select(KnowledgeIndexRecord)
                .where(KnowledgeIndexRecord.parse_run_id == run.id)
                .order_by(KnowledgeIndexRecord.id.desc())
                .limit(1)
            )
            report.update(
                {
                    "parse_status": str(run.status),
                    "parser": run.parser_name,
                    "ocr_warnings": run.warning_count,
                    "index_status": str(indexed.status) if indexed else "NONE",
                    "indexed_chunks": int(indexed.chunk_count) if indexed else 0,
                }
            )
            text = ""
            if run.text_storage_key:
                try:
                    path = LocalFileStorage(settings.file_storage_path).path_for_key(
                        run.text_storage_key
                    )
                    if (
                        not path.is_symlink()
                        and path.is_file()
                        and 0 < path.stat().st_size <= MAX_ARTIFACT_BYTES
                    ):
                        text = path.read_text(encoding="utf-8")
                    else:
                        report["artifact_state"] = "EMPTY_OR_OUT_OF_BOUNDS"
                except (OSError, StorageError, UnicodeError):
                    report["artifact_state"] = "UNREADABLE"
            else:
                report["artifact_state"] = "MISSING"
            report["ocr_text_chars"] = len(text)
            report.update(
                expected_text_flags(text, code=code, quantity=quantity, lot=lot)
            )
    finally:
        await engine.dispose()

    if probe and file_id is not None:
        store = QdrantKnowledgeStore(
            settings.qdrant_url,
            alias=settings.qdrant_collection_alias,
            collection=settings.qdrant_collection_name,
        )
        try:
            retrieval = KnowledgeRetrievalService(
                store,
                FastEmbedTextAdapter(
                    settings.embedding_model,
                    settings.embedding_cache_path,
                    batch_size=settings.embedding_batch_size,
                ),
                FastEmbedSparseAdapter(cache_dir=settings.embedding_cache_path),
            )
            query = f"{code} məhsulunun qutu miqdarı və partiya kodu nədir?"
            normalized = retrieval_query(query)
            hits = await retrieval.search_internal_knowledge(
                normalized, mode="hybrid", limit=10
            )
            filtered = await retrieval.search_internal_knowledge(
                normalized,
                mode="hybrid",
                filters=RetrievalFilter(file_id=file_id),
                limit=10,
            )
            relevance = DocumentRelevancePolicy(
                settings.internal_rag_min_score,
                settings.internal_rag_identifier_min_score,
            )
            report.update(
                {
                    "top10_hits": len(hits),
                    "target_top10_hits": sum(x.source_file_id == file_id for x in hits),
                    "target_accepted": sum(
                        relevance.accepts(query, x)
                        for x in hits if x.source_file_id == file_id
                    ),
                    "target_filtered_hits": len(filtered),
                    "target_filtered_accepted": sum(
                        relevance.accepts(query, x) for x in filtered
                    ),
                }
            )
        except Exception as error:
            report["probe_error_type"] = type(error).__name__
        finally:
            await store.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--filename", required=True)
    parser.add_argument("--code", required=True)
    parser.add_argument("--quantity", type=int)
    parser.add_argument("--lot")
    parser.add_argument("--probe-search", action="store_true")
    args = parser.parse_args()
    try:
        result = asyncio.run(
            inspect_image(
                args.filename,
                args.code,
                quantity=args.quantity,
                lot=args.lot,
                probe=args.probe_search,
            )
        )
    except Exception as error:
        result = {"diagnostic": "FAILED", "error_type": type(error).__name__}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
