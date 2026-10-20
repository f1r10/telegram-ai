"""OCR replay never promotes blank text or trusts tampered source files."""

from hashlib import sha256
from pathlib import Path

from office_assistant.infrastructure.database.models import (
    DocumentParseRun,
    DocumentProcessingStatus,
    FileObject,
    FileObjectState,
)
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.knowledge.models import ChunkingConfig
from office_assistant.processing.ocr_replay import verified_raw_pdf
from office_assistant.scripts.replay_scanned_pdfs import safe_artifacts


def test_replay_refuses_tampered_pdf_and_oversized_source(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    payload = b"%PDF-original-safe-test"
    path = storage.blob_root / "input.pdf"
    path.write_bytes(payload)
    file = FileObject(
        storage_key="blobs/input.pdf",
        original_filename="input.pdf",
        content_type="application/pdf",
        sha256=sha256(payload).hexdigest(),
        byte_size=len(payload),
        state=FileObjectState.ACTIVE,
    )
    assert verified_raw_pdf(storage, file, 50) == path
    assert verified_raw_pdf(storage, file, 10) is None
    path.write_bytes(b"%PDF-other-content")
    assert verified_raw_pdf(storage, file, 50) is None
    path.write_bytes(b"not-a-pdf")
    assert verified_raw_pdf(storage, file, 50) is None


def test_replay_rejects_deleted_file_and_wrong_mime(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    payload = b"%PDF-sample"
    (storage.blob_root / "x.pdf").write_bytes(payload)
    file = FileObject(
        storage_key="blobs/x.pdf",
        original_filename="x.pdf",
        content_type="application/pdf",
        sha256=sha256(payload).hexdigest(),
        byte_size=len(payload),
        state=FileObjectState.DELETED,
    )
    assert verified_raw_pdf(storage, file, 1024) is None
    file.state = FileObjectState.ACTIVE
    file.content_type = "text/plain"
    assert verified_raw_pdf(storage, file, 1024) is None


def test_ocr_queue_preflight_rejects_changed_active_source_and_unknown_mime(
    tmp_path: Path,
) -> None:
    storage = LocalFileStorage(tmp_path)
    payload = b"%PDF-synthetic-legacy-scan"
    raw = storage.blob_root / "legacy.pdf"
    raw.write_bytes(payload)
    file = FileObject(
        id=39,
        storage_key="blobs/legacy.pdf",
        original_filename="legacy.pdf",
        content_type="application/pdf",
        sha256=sha256(payload).hexdigest(),
        byte_size=len(payload),
        state=FileObjectState.ACTIVE,
    )
    run = DocumentParseRun(
        id=21,
        file_object_id=file.id,
        ingestion_job_id=2,
        status=DocumentProcessingStatus.DEFERRED,
        parser_name="pypdf",
        parser_version="historic",
        requires_ocr=True,
    )
    run.normalized_storage_key = storage.write_derived_json(
        file.id, run.id, {"schema_version": "v1", "blocks": []}
    )
    config = ChunkingConfig("v1-medium-1800-180", 1800, 180)
    args = {
        "max_raw_bytes": 1024,
        "chunking": config,
    }
    assert safe_artifacts(storage, run, file, **args)
    raw.write_bytes(b"%PDF-CHANGED-legacy-scan")
    assert not safe_artifacts(storage, run, file, **args)
    raw.write_bytes(payload)
    file.state = FileObjectState.DELETED
    assert not safe_artifacts(storage, run, file, **args)
    file.state = FileObjectState.ACTIVE
    file.content_type = "text/plain"
    assert not safe_artifacts(storage, run, file, **args)
    file.content_type = "application/pdf"
    assert safe_artifacts(storage, run, file, **args)
